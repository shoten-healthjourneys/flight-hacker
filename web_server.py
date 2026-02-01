#!/usr/bin/env python3
"""
Flight Hacker - Web Server
Dashboard + Scanner Launcher in one
"""

import http.server
import socketserver
import json
import sqlite3
import threading
import queue
import time
import sys
import requests
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from flight_tracker import init_database, add_flight_result, get_best_deals, get_flight_by_id
from scrapers.google_flights import search_google_flights, PLAYWRIGHT_AVAILABLE

PORT = 8000
DB_PATH = Path(__file__).parent / "flights.db"

# Scanner state
log_queue = queue.Queue()
scan_status = {'running': False, 'vpn_waiting': False, 'location': 'Unknown', 'phase': ''}

# Search config (defaults, can be overridden from UI)
search_config = {
    'origin': 'LHR',
    'destination': 'BOM',
    'departure_dates': ['2026-02-16', '2026-02-17', '2026-02-18'],
    'return_dates': ['2026-02-23', '2026-02-24']
}


def log(message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_queue.put(f"[{timestamp}] {message}")
    print(f"[{timestamp}] {message}")


def get_current_location() -> dict:
    """Get current IP location using multiple APIs as fallback"""
    apis = [
        ('http://ip-api.com/json/', lambda d: {
            'country': d.get('country', 'Unknown'),
            'country_code': d.get('countryCode', 'XX'),
            'city': d.get('city', 'Unknown'),
            'ip': d.get('query', 'Unknown')
        }),
        ('https://ipapi.co/json/', lambda d: {
            'country': d.get('country_name', 'Unknown'),
            'country_code': d.get('country_code', 'XX'),
            'city': d.get('city', 'Unknown'),
            'ip': d.get('ip', 'Unknown')
        }),
        ('https://ipinfo.io/json', lambda d: {
            'country': d.get('country', 'Unknown'),
            'country_code': d.get('country', 'XX'),
            'city': d.get('city', 'Unknown'),
            'ip': d.get('ip', 'Unknown')
        }),
    ]

    for url, parser in apis:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                result = parser(data)
                if result['country_code'] != 'XX' and result['ip'] != 'Unknown':
                    return result
        except:
            continue

    return {'country': 'Unknown', 'country_code': 'XX', 'city': 'Unknown', 'ip': 'Unknown'}


def run_scan_phase():
    location = get_current_location()
    location_label = f"{location['country_code']} - {location['city']}"
    country_code = location['country_code'] if location['country_code'] != 'XX' else None
    scan_status['location'] = location_label

    # Log which Google domain will be used
    domains = {'US': 'google.com', 'GB': 'google.co.uk', 'NL': 'google.nl', 'JP': 'google.co.jp'}
    domain = domains.get(country_code, 'google.com')
    log(f"  Using {domain} (country: {country_code or 'default'})")

    results = []
    dep_dates = search_config['departure_dates']
    ret_dates = search_config['return_dates']
    total = len(dep_dates) * len(ret_dates)
    count = 0

    for dep_date in dep_dates:
        for ret_date in ret_dates:
            if not scan_status['running']:
                return results
            count += 1
            log(f"  [{count}/{total}] {dep_date} → {ret_date}")
            try:
                gf_results = search_google_flights(
                    origin=search_config['origin'],
                    destination=search_config['destination'],
                    departure_date=dep_date, return_date=ret_date,
                    headless=True,
                    country_code=country_code
                )
                for r in gf_results:
                    r['vpn_location'] = location_label
                    add_flight_result(r)
                    results.append(r)
                log(f"      Found {len(gf_results)} flights")
            except Exception as e:
                log(f"      Error: {e}")

    return results


def run_full_scan():
    global scan_status
    scan_status['running'] = True
    scan_status['vpn_waiting'] = False

    log("=" * 50)
    log("STARTING FLIGHT SCAN")
    log(f"Route: {search_config['origin']} → {search_config['destination']}")
    log(f"Departures: {', '.join(search_config['departure_dates'])}")
    log(f"Returns: {', '.join(search_config['return_dates'])}")
    log("=" * 50)

    init_database()
    all_results = []

    # Phase 1: Current location
    scan_status['phase'] = 'UK Scan'
    log("")
    log("PHASE 1: Scanning from current location")
    results = run_scan_phase()
    all_results.extend(results)
    log(f"Phase 1 complete: {len(results)} flights")

    if not scan_status['running']:
        log("Scan stopped")
        return

    # Phase 2: VPN
    scan_status['phase'] = 'Waiting for VPN'
    log("")
    log("=" * 50)
    log("PHASE 2: Connect your VPN now!")
    log("=" * 50)
    scan_status['vpn_waiting'] = True

    start_loc = get_current_location()
    start_ip = start_loc['ip']
    start_country = start_loc['country_code']
    log(f"Current IP: {start_ip} ({start_country})")
    start_time = time.time()

    while time.time() - start_time < 120:
        if not scan_status['running']:
            log("Scan stopped")
            scan_status['vpn_waiting'] = False
            return

        time.sleep(3)
        loc = get_current_location()
        scan_status['location'] = f"{loc['country_code']} - {loc['city']}"

        # Detect VPN by IP change OR country change
        if loc['ip'] != start_ip and loc['ip'] != 'Unknown':
            log(f"IP changed: {start_ip} -> {loc['ip']}")
            log(f"VPN Connected: {loc['city']}, {loc['country']}")
            scan_status['vpn_waiting'] = False
            break

        if loc['country_code'] != start_country and loc['country_code'] != 'XX':
            log(f"VPN Connected: {loc['city']}, {loc['country']}")
            scan_status['vpn_waiting'] = False
            break
    else:
        log("VPN timeout - skipping")
        scan_status['vpn_waiting'] = False
        scan_status['running'] = False
        return

    scan_status['phase'] = 'VPN Scan'
    results = run_scan_phase()
    all_results.extend(results)

    # Done
    log("")
    log("=" * 50)
    log("SCAN COMPLETE")
    log(f"Total: {len(all_results)} flights found")
    if all_results:
        best = min(all_results, key=lambda x: x.get('price_gbp', float('inf')))
        log(f"Best: £{best['price_gbp']:.0f} ({best.get('airline', 'Unknown')})")
    log("=" * 50)

    scan_status['running'] = False
    scan_status['phase'] = 'Complete'


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path

        if path == '/' or path == '/index.html':
            self.serve_launcher()
        elif path == '/dashboard':
            self.serve_dashboard()
        elif path == '/api/data':
            self.serve_api_data()
        elif path == '/api/logs':
            self.serve_logs()
        elif path == '/api/status':
            self.serve_status()
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/start':
            self.start_scan()
        elif path == '/api/stop':
            self.stop_scan()
        else:
            self.send_error(404)

    def serve_launcher(self):
        html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Flight Hacker</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
            color: #fff;
            min-height: 100vh;
        }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }

        header {
            text-align: center;
            padding: 40px 20px;
            background: rgba(255,255,255,0.05);
            border-radius: 20px;
            margin-bottom: 30px;
        }
        h1 { font-size: 3em; margin-bottom: 10px; }
        .route { font-size: 1.5em; color: #a0a0a0; }

        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
        @media (max-width: 900px) { .panel[style*="span 2"] { grid-column: span 1 !important; } }

        .panel {
            background: rgba(255,255,255,0.08);
            border-radius: 15px;
            padding: 25px;
        }
        .panel h2 { margin-bottom: 20px; color: #667eea; }

        .controls { display: flex; gap: 15px; margin-bottom: 20px; }
        button {
            flex: 1;
            padding: 18px;
            font-size: 1.1em;
            font-weight: 600;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.3s;
        }
        .btn-start { background: linear-gradient(135deg, #00b894, #00cec9); color: white; }
        .btn-start:hover { transform: translateY(-2px); box-shadow: 0 10px 30px rgba(0,184,148,0.3); }
        .btn-start:disabled { background: #444; transform: none; box-shadow: none; cursor: not-allowed; }
        .btn-stop { background: linear-gradient(135deg, #e74c3c, #c0392b); color: white; }
        .btn-dashboard { background: linear-gradient(135deg, #667eea, #764ba2); color: white; }

        .config-panel {
            background: rgba(255,255,255,0.08);
            border-radius: 15px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .config-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 15px;
        }
        .config-group label {
            display: block;
            color: #888;
            font-size: 0.85em;
            margin-bottom: 5px;
        }
        .config-group input {
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 8px;
            background: rgba(0,0,0,0.3);
            color: #fff;
            font-size: 1em;
        }
        .config-group input:focus {
            outline: 2px solid #667eea;
        }
        .airport-input {
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 2px;
        }

        .status-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px; }
        .status-item {
            background: rgba(0,0,0,0.3);
            padding: 15px;
            border-radius: 10px;
            text-align: center;
        }
        .status-label { font-size: 0.85em; color: #888; margin-bottom: 5px; }
        .status-value { font-size: 1.3em; font-weight: 600; }
        .status-value.running { color: #00b894; }
        .status-value.waiting { color: #f39c12; }

        .vpn-alert {
            background: linear-gradient(135deg, #f39c12, #e74c3c);
            padding: 25px;
            border-radius: 12px;
            text-align: center;
            font-size: 1.4em;
            font-weight: 600;
            margin-bottom: 20px;
            display: none;
            animation: pulse 1.5s infinite;
        }
        .vpn-alert.active { display: block; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.7; } }

        .log-box {
            background: #0a0a0a;
            border-radius: 10px;
            padding: 15px;
            height: 350px;
            overflow-y: auto;
            font-family: 'Monaco', 'Menlo', monospace;
            font-size: 0.85em;
            line-height: 1.6;
        }
        .log-line { padding: 2px 0; }

        .results-table { width: 100%; border-collapse: collapse; }
        .results-table th, .results-table td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        .results-table th { color: #667eea; font-weight: 600; }
        .price { color: #00b894; font-weight: 600; font-size: 1.1em; }
        .vpn-badge {
            background: #667eea;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.8em;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>✈️ Flight Hacker</h1>
            <div class="route">Find the cheapest flights with VPN price comparison</div>
        </header>

        <div class="config-panel">
            <div class="config-row">
                <div class="config-group">
                    <label>From (Airport Code)</label>
                    <input type="text" id="originInput" class="airport-input" value="LHR" maxlength="3" placeholder="LHR">
                </div>
                <div class="config-group">
                    <label>To (Airport Code)</label>
                    <input type="text" id="destInput" class="airport-input" value="BOM" maxlength="3" placeholder="BOM">
                </div>
                <div class="config-group">
                    <label>Departure Dates (comma separated)</label>
                    <input type="text" id="depDatesInput" value="2026-02-16, 2026-02-17, 2026-02-18" placeholder="YYYY-MM-DD, YYYY-MM-DD">
                </div>
                <div class="config-group">
                    <label>Return Dates (comma separated)</label>
                    <input type="text" id="retDatesInput" value="2026-02-23, 2026-02-24" placeholder="YYYY-MM-DD, YYYY-MM-DD">
                </div>
            </div>
        </div>

        <div class="vpn-alert" id="vpnAlert">
            🔐 Connect your VPN now! Click "Quick Connect" in ProtonVPN
        </div>

        <div class="grid">
            <div class="panel">
                <h2>Scanner Control</h2>

                <div class="controls">
                    <button class="btn-start" id="startBtn" onclick="startScan()">▶ Start Scan</button>
                    <button class="btn-stop" id="stopBtn" onclick="stopScan()" style="display:none">■ Stop</button>
                </div>

                <div class="status-grid">
                    <div class="status-item">
                        <div class="status-label">Status</div>
                        <div class="status-value" id="statusText">Ready</div>
                    </div>
                    <div class="status-item">
                        <div class="status-label">Location</div>
                        <div class="status-value" id="locationText">--</div>
                    </div>
                    <div class="status-item">
                        <div class="status-label">Phase</div>
                        <div class="status-value" id="phaseText">--</div>
                    </div>
                    <div class="status-item">
                        <div class="status-label">Flights Found</div>
                        <div class="status-value" id="countText">0</div>
                    </div>
                </div>

                <div class="log-box" id="logBox">
                    <div class="log-line">Ready. Click "Start Scan" to begin.</div>
                </div>
            </div>

            <div class="panel" style="grid-column: span 2;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;">
                    <h2>Flight Results</h2>
                    <div style="display:flex; gap:10px; align-items:center;">
                        <label style="display:flex; align-items:center; gap:5px; cursor:pointer;">
                            <input type="checkbox" id="directOnly" onchange="loadResults()">
                            <span>Direct flights only</span>
                        </label>
                    </div>
                </div>
                <table class="results-table">
                    <thead>
                        <tr>
                            <th>Dates</th>
                            <th>Outbound</th>
                            <th>Return</th>
                            <th>Airline</th>
                            <th>Duration</th>
                            <th>Stops</th>
                            <th>Price</th>
                            <th>Location</th>
                            <th>Book</th>
                        </tr>
                    </thead>
                    <tbody id="resultsBody">
                        <tr><td colspan="9" style="color:#666">No results yet</td></tr>
                    </tbody>
                </table>
                <div id="pagination" style="display:flex; justify-content:center; gap:10px; margin-top:20px;"></div>
            </div>
        </div>
    </div>

    <script>
        let pollInterval;

        async function startScan() {
            document.getElementById('startBtn').disabled = true;
            document.getElementById('stopBtn').style.display = 'block';
            document.getElementById('logBox').innerHTML = '';
            document.getElementById('resultsBody').innerHTML = '<tr><td colspan="9" style="color:#666">Scanning...</td></tr>';
            document.getElementById('pagination').innerHTML = '';
            document.getElementById('countText').textContent = '0';

            // Get config from inputs
            const config = {
                origin: document.getElementById('originInput').value.trim(),
                destination: document.getElementById('destInput').value.trim(),
                departure_dates: document.getElementById('depDatesInput').value.split(',').map(d => d.trim()).filter(d => d),
                return_dates: document.getElementById('retDatesInput').value.split(',').map(d => d.trim()).filter(d => d),
                clear_db: true  // Clear old results
            };

            await fetch('/api/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });
            pollInterval = setInterval(poll, 1000);
        }

        async function stopScan() {
            await fetch('/api/stop', { method: 'POST' });
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').style.display = 'none';
            document.getElementById('vpnAlert').classList.remove('active');
            clearInterval(pollInterval);
        }

        async function poll() {
            try {
                const [statusRes, logsRes, dataRes] = await Promise.all([
                    fetch('/api/status'),
                    fetch('/api/logs'),
                    fetch('/api/data')
                ]);

                const status = await statusRes.json();
                const logs = await logsRes.json();
                const data = await dataRes.json();

                // Update status
                document.getElementById('statusText').textContent = status.running ? 'Scanning...' : 'Ready';
                document.getElementById('statusText').className = 'status-value' + (status.running ? ' running' : '');
                document.getElementById('locationText').textContent = status.location;
                document.getElementById('phaseText').textContent = status.phase || '--';
                document.getElementById('countText').textContent = data.stats?.total_searches || 0;

                // VPN alert
                document.getElementById('vpnAlert').classList.toggle('active', status.vpn_waiting);

                // Logs
                if (logs.length > 0) {
                    const box = document.getElementById('logBox');
                    logs.forEach(line => {
                        const div = document.createElement('div');
                        div.className = 'log-line';
                        div.textContent = line;
                        box.appendChild(div);
                    });
                    box.scrollTop = box.scrollHeight;
                }

                // Results
                if (data.best_deals?.length > 0) {
                    document.getElementById('resultsBody').innerHTML = data.best_deals.slice(0, 8).map(d => `
                        <tr>
                            <td>${d.departure} → ${d.return}</td>
                            <td class="price">£${d.price?.toFixed(0) || '--'}</td>
                            <td>${d.airline || 'Unknown'}</td>
                            <td><span class="vpn-badge">${d.vpn || 'UK'}</span></td>
                        </tr>
                    `).join('');
                }

                // Stop polling when done
                if (!status.running && !status.vpn_waiting) {
                    clearInterval(pollInterval);
                    document.getElementById('startBtn').disabled = false;
                    document.getElementById('stopBtn').style.display = 'none';
                }
            } catch (e) {
                console.error(e);
            }
        }

        // Initial status
        fetch('/api/status').then(r => r.json()).then(s => {
            document.getElementById('locationText').textContent = s.location;
        });

        let currentPage = 1;

        async function loadResults(page = 1) {
            currentPage = page;
            const directOnly = document.getElementById('directOnly').checked;
            const res = await fetch(`/api/data?page=${page}&per_page=10&direct=${directOnly}`);
            const data = await res.json();

            document.getElementById('countText').textContent = data.stats?.total_searches || 0;

            if (data.best_deals?.length > 0) {
                document.getElementById('resultsBody').innerHTML = data.best_deals.map(d => `
                    <tr>
                        <td>${d.departure}<br><small style="color:#888">→ ${d.return}</small></td>
                        <td>${d.times || '--'}</td>
                        <td>${d.return_times || '--'}</td>
                        <td><strong>${d.airline || 'Unknown'}</strong></td>
                        <td>${d.duration || '--'}</td>
                        <td>${d.stops === 0 ? '<span style="color:#00b894">Direct</span>' : d.stops + ' stop'}</td>
                        <td class="price">£${d.price?.toFixed(0) || '--'}</td>
                        <td><span class="vpn-badge">${d.vpn || 'UK'}</span></td>
                        <td>${d.url ? `<a href="${d.url}" target="_blank" style="color:#667eea;text-decoration:none;">Book →</a>` : '--'}</td>
                    </tr>
                `).join('');

                // Pagination
                const p = data.pagination;
                if (p && p.total_pages > 1) {
                    let pagHtml = '';
                    if (p.page > 1) {
                        pagHtml += `<button onclick="loadResults(${p.page - 1})" style="padding:8px 15px;border:none;border-radius:5px;background:#333;color:#fff;cursor:pointer;">← Prev</button>`;
                    }
                    pagHtml += `<span style="padding:8px 15px;">Page ${p.page} of ${p.total_pages} (${p.total} flights)</span>`;
                    if (p.page < p.total_pages) {
                        pagHtml += `<button onclick="loadResults(${p.page + 1})" style="padding:8px 15px;border:none;border-radius:5px;background:#333;color:#fff;cursor:pointer;">Next →</button>`;
                    }
                    document.getElementById('pagination').innerHTML = pagHtml;
                } else {
                    document.getElementById('pagination').innerHTML = `<span style="color:#666">${data.pagination?.total || 0} flights total</span>`;
                }
            } else {
                document.getElementById('resultsBody').innerHTML = '<tr><td colspan="8" style="color:#666">No results yet</td></tr>';
                document.getElementById('pagination').innerHTML = '';
            }
        }

        // Initial load and poll every 5s
        loadResults();
        setInterval(() => loadResults(currentPage), 5000);
    </script>
</body>
</html>'''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_dashboard(self):
        # Original dashboard redirect or serve
        self.send_response(302)
        self.send_header('Location', '/')
        self.end_headers()

    def serve_api_data(self):
        try:
            # Parse query params for pagination and filters
            query = parse_qs(urlparse(self.path).query)
            page = int(query.get('page', [1])[0])
            per_page = int(query.get('per_page', [10])[0])
            direct_only = query.get('direct', ['false'])[0].lower() == 'true'
            offset = (page - 1) * per_page

            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # Base filter
            where_clause = "WHERE price_gbp IS NOT NULL"
            if direct_only:
                where_clause += " AND stops = 0"

            cursor.execute(f'SELECT MIN(price_gbp) FROM flights {where_clause}')
            best_price = cursor.fetchone()[0]

            cursor.execute(f'SELECT COUNT(*) FROM flights {where_clause}')
            total = cursor.fetchone()[0]

            cursor.execute(f'''
                SELECT f.id, f.departure_date, f.return_date, f.price_gbp,
                       f.airline, f.vpn_location, f.booking_site, f.booking_url,
                       f.stops, f.duration_minutes, f.search_metadata
                FROM flights f
                {where_clause}
                ORDER BY f.price_gbp ASC
                LIMIT ? OFFSET ?
            ''', (per_page, offset))

            deals = []
            for r in cursor.fetchall():
                metadata = json.loads(r[10]) if r[10] else {}
                duration = r[9]
                dur_str = f"{duration//60}h {duration%60}m" if duration else ""

                deals.append({
                    'id': r[0],
                    'departure': r[1],
                    'return': r[2],
                    'price': r[3],
                    'airline': r[4],
                    'vpn': r[5],
                    'site': r[6],
                    'url': r[7],
                    'stops': r[8],
                    'duration': dur_str,
                    'dep_time': metadata.get('dep_time', ''),
                    'arr_time': metadata.get('arr_time', ''),
                    'times': metadata.get('times', ''),
                    'return_times': metadata.get('return_times', '')
                })

            conn.close()

            total_pages = (total + per_page - 1) // per_page
            self.send_json({
                'stats': {'best_price': best_price, 'total_searches': total},
                'best_deals': deals,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'total_pages': total_pages
                }
            })
        except Exception as e:
            self.send_json({'error': str(e), 'stats': {}, 'best_deals': [], 'pagination': {}})

    def serve_logs(self):
        logs = []
        while not log_queue.empty():
            try:
                logs.append(log_queue.get_nowait())
            except:
                break
        self.send_json(logs)

    def serve_status(self):
        loc = get_current_location()
        scan_status['location'] = f"{loc['country_code']} - {loc['city']}"
        self.send_json(scan_status)

    def start_scan(self):
        global search_config
        # Read config from POST body
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)
                if data.get('origin'):
                    search_config['origin'] = data['origin'].upper()
                if data.get('destination'):
                    search_config['destination'] = data['destination'].upper()
                if data.get('departure_dates'):
                    search_config['departure_dates'] = data['departure_dates']
                if data.get('return_dates'):
                    search_config['return_dates'] = data['return_dates']

                # Clear database if requested
                if data.get('clear_db'):
                    try:
                        conn = sqlite3.connect(DB_PATH)
                        conn.execute('DELETE FROM flights')
                        conn.commit()
                        conn.close()
                    except:
                        pass
        except:
            pass

        if not scan_status['running']:
            threading.Thread(target=run_full_scan, daemon=True).start()
        self.send_json({'ok': True})

    def stop_scan(self):
        scan_status['running'] = False
        self.send_json({'ok': True})

    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


if __name__ == "__main__":
    import os
    os.chdir(str(Path(__file__).parent))

    # Init DB
    init_database()

    # Get initial location
    loc = get_current_location()
    scan_status['location'] = f"{loc['country_code']} - {loc['city']}"

    print(f"\n{'='*60}")
    print("✈️  Flight Hacker")
    print(f"{'='*60}")
    print(f"\n🌐 Open: http://localhost:{PORT}")
    print(f"\nPress Ctrl+C to stop\n")

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
