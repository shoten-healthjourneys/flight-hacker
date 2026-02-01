#!/usr/bin/env python3
"""
Flight Hacker - Web Server
Dashboard + Parallel Scanner Controller
"""

import http.server
import socketserver
import json
import sqlite3
import threading
import queue
import time
import sys
import subprocess
import os
import requests
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from flight_tracker import init_database, add_flight_result, get_best_deals, get_flight_by_id

# Import parallel scan functions
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_parallel_scan", Path(__file__).parent / "run-parallel-scan.py")
    run_parallel_scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_parallel_scan)
    fetch_vpngate_configs = run_parallel_scan.fetch_vpngate_configs
    generate_docker_compose = run_parallel_scan.generate_docker_compose
    aggregate_results = run_parallel_scan.aggregate_results
    clear_old_results = run_parallel_scan.clear_old_results
    PARALLEL_AVAILABLE = True
except Exception as e:
    print(f"Warning: Parallel scan not available: {e}")
    PARALLEL_AVAILABLE = False

PORT = 8000
PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "flights.db"
OUTPUT_DIR = PROJECT_DIR / "output"

# Log queue for real-time logs
log_queue = queue.Queue()

# Parallel scan state
parallel_scan_status = {
    'running': False,
    'countries': [],
    'start_time': None
}


def log(message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_queue.put(f"[{timestamp}] {message}")
    print(f"[{timestamp}] {message}")


def get_current_location() -> dict:
    """Get current IP location"""
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
    ]

    for url, parser in apis:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                return parser(response.json())
        except:
            continue

    return {'country': 'Unknown', 'country_code': 'XX', 'city': 'Unknown', 'ip': 'Unknown'}


def get_scanner_statuses():
    """Read all status_*.json files from output directory"""
    statuses = []
    OUTPUT_DIR.mkdir(exist_ok=True)

    for status_file in OUTPUT_DIR.glob("status_*.json"):
        try:
            with open(status_file) as f:
                status = json.load(f)
                statuses.append(status)
        except:
            pass

    return statuses


def run_parallel_scan_thread(config):
    """Run parallel scan in background thread"""
    global parallel_scan_status

    try:
        parallel_scan_status['running'] = True
        parallel_scan_status['start_time'] = datetime.now().isoformat()

        origin = config.get('origin', 'LHR')
        destination = config.get('destination', 'BOM')
        dep_date = config.get('departure_date', '2026-02-16')
        ret_date = config.get('return_date', '2026-02-23')
        countries = config.get('countries', [])

        log(f"Starting parallel scan: {origin} -> {destination}")
        log(f"Dates: {dep_date} to {ret_date}")

        # Clear old results
        clear_old_results(OUTPUT_DIR, DB_PATH)

        # Fetch VPN configs if not specified
        if not countries:
            log("Fetching VPN servers from VPNGate...")
            countries = fetch_vpngate_configs(PROJECT_DIR / "vpn_configs")

        parallel_scan_status['countries'] = ['UK'] + countries
        log(f"Will scan from {len(parallel_scan_status['countries'])} locations: UK, {', '.join(countries)}")

        # Generate docker-compose.yml
        compose_path = generate_docker_compose(
            countries, origin, destination, dep_date, ret_date,
            output_path=PROJECT_DIR / "docker-compose.yml",
            project_dir=PROJECT_DIR
        )

        # Build and run containers
        log("Building scanner containers...")
        build_result = subprocess.run(
            ["docker-compose", "build"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True
        )

        if build_result.returncode != 0:
            log(f"Build failed: {build_result.stderr}")
            parallel_scan_status['running'] = False
            return

        log("Launching parallel scanners...")
        subprocess.run(
            ["docker-compose", "up", "-d"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True
        )

        # Monitor containers until all complete
        while parallel_scan_status['running']:
            ps_result = subprocess.run(
                ["docker-compose", "ps", "-q"],
                cwd=PROJECT_DIR,
                capture_output=True,
                text=True
            )

            if not ps_result.stdout.strip():
                break

            statuses = get_scanner_statuses()
            complete_count = sum(1 for s in statuses if s.get('status') == 'complete')
            total_count = len(parallel_scan_status['countries'])

            if complete_count >= total_count:
                break

            time.sleep(2)

        # Aggregate results
        if parallel_scan_status['running']:
            log("Aggregating results...")
            total = aggregate_results(OUTPUT_DIR, DB_PATH)
            log(f"Scan complete: {total} flights found")

    except Exception as e:
        log(f"Parallel scan error: {e}")
    finally:
        parallel_scan_status['running'] = False


def stop_parallel_scan():
    """Stop running parallel scan containers"""
    global parallel_scan_status

    try:
        subprocess.run(
            ["docker-compose", "down"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True
        )
        log("Scanners stopped")
    except Exception as e:
        log(f"Error stopping scanners: {e}")

    parallel_scan_status['running'] = False
    parallel_scan_status['countries'] = []


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path

        if path == '/' or path == '/index.html':
            self.serve_dashboard()
        elif path == '/api/data':
            self.serve_api_data()
        elif path == '/api/logs':
            self.serve_logs()
        elif path == '/api/scanner-status':
            self.serve_scanner_status()
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/start':
            self.start_parallel_scan()
        elif path == '/api/stop':
            self.stop_parallel_scan()
        else:
            self.send_error(404)

    def serve_scanner_status(self):
        """Return status of all scanner containers"""
        statuses = get_scanner_statuses()

        total_flights = sum(s.get('progress', {}).get('flights_found', 0) for s in statuses)
        complete_count = sum(1 for s in statuses if s.get('status') == 'complete')
        error_count = sum(1 for s in statuses if s.get('status') == 'error')

        self.send_json({
            'parallel_running': parallel_scan_status['running'],
            'countries': parallel_scan_status['countries'],
            'start_time': parallel_scan_status['start_time'],
            'scanners': statuses,
            'summary': {
                'total_scanners': len(parallel_scan_status['countries']),
                'complete': complete_count,
                'errors': error_count,
                'total_flights': total_flights
            }
        })

    def start_parallel_scan(self):
        """Start parallel scan with provided config"""
        global parallel_scan_status

        if parallel_scan_status['running']:
            self.send_json({'ok': False, 'error': 'Scan already running'})
            return

        if not PARALLEL_AVAILABLE:
            self.send_json({'ok': False, 'error': 'Parallel scan not available'})
            return

        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else '{}'
            config = json.loads(body)

            thread = threading.Thread(target=run_parallel_scan_thread, args=(config,), daemon=True)
            thread.start()

            self.send_json({'ok': True, 'message': 'Scan started'})
        except Exception as e:
            self.send_json({'ok': False, 'error': str(e)})

    def stop_parallel_scan(self):
        """Stop parallel scan"""
        stop_parallel_scan()
        self.send_json({'ok': True, 'message': 'Scan stopped'})

    def serve_dashboard(self):
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
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }

        header {
            text-align: center;
            padding: 40px 20px;
            background: rgba(255,255,255,0.05);
            border-radius: 20px;
            margin-bottom: 30px;
        }
        h1 { font-size: 3em; margin-bottom: 10px; }
        .subtitle { font-size: 1.3em; color: #a0a0a0; }

        .panel {
            background: rgba(255,255,255,0.08);
            border-radius: 15px;
            padding: 25px;
            margin-bottom: 20px;
        }
        .panel h2 { margin-bottom: 20px; color: #667eea; }

        .config-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
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
        .config-group input:focus { outline: 2px solid #667eea; }
        .airport-input {
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 2px;
        }

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
        .btn-stop { background: linear-gradient(135deg, #e74c3c, #c0392b); color: white; display: none; }

        .summary-stats {
            display: flex;
            justify-content: space-around;
            padding: 20px;
            background: rgba(0,0,0,0.3);
            border-radius: 12px;
            margin-bottom: 20px;
        }
        .summary-stat { text-align: center; }
        .summary-stat .value { font-size: 2.5em; font-weight: 700; color: #00b894; }
        .summary-stat .label { font-size: 0.9em; color: #888; margin-top: 5px; }

        .progress-bar {
            width: 100%;
            height: 10px;
            background: rgba(255,255,255,0.1);
            border-radius: 5px;
            overflow: hidden;
            margin-bottom: 20px;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea, #00b894);
            transition: width 0.5s ease;
        }

        .scanner-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
            gap: 15px;
        }
        .scanner-card {
            background: rgba(0,0,0,0.3);
            border-radius: 12px;
            padding: 15px;
            text-align: center;
            border: 2px solid transparent;
            transition: all 0.3s;
        }
        .scanner-card.starting { border-color: #888; }
        .scanner-card.connecting { border-color: #f39c12; }
        .scanner-card.connected { border-color: #3498db; }
        .scanner-card.scraping { border-color: #00b894; animation: pulse 1.5s infinite; }
        .scanner-card.complete { border-color: #27ae60; background: rgba(39, 174, 96, 0.2); }
        .scanner-card.error { border-color: #e74c3c; background: rgba(231, 76, 60, 0.2); }

        @keyframes pulse {
            0%, 100% { box-shadow: 0 0 5px rgba(0, 184, 148, 0.5); }
            50% { box-shadow: 0 0 20px rgba(0, 184, 148, 0.8); }
        }

        .scanner-country { font-size: 1.4em; font-weight: 700; margin-bottom: 5px; }
        .scanner-status { font-size: 0.85em; color: #888; text-transform: capitalize; }
        .scanner-flights { font-size: 1.1em; color: #00b894; font-weight: 600; margin-top: 8px; }
        .scanner-city { font-size: 0.75em; color: #666; margin-top: 5px; }

        .log-box {
            background: #0a0a0a;
            border-radius: 10px;
            padding: 15px;
            height: 200px;
            overflow-y: auto;
            font-family: 'Monaco', 'Menlo', monospace;
            font-size: 0.85em;
            line-height: 1.6;
            margin-top: 20px;
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

        .empty-state {
            text-align: center;
            padding: 40px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Flight Hacker</h1>
            <div class="subtitle">Multi-location price comparison with parallel Docker scanners</div>
        </header>

        <div class="panel">
            <h2>Search Configuration</h2>
            <div class="config-row">
                <div class="config-group">
                    <label>From</label>
                    <input type="text" id="originInput" class="airport-input" value="LHR" maxlength="3">
                </div>
                <div class="config-group">
                    <label>To</label>
                    <input type="text" id="destInput" class="airport-input" value="BOM" maxlength="3">
                </div>
                <div class="config-group">
                    <label>Departure Date</label>
                    <input type="date" id="depDateInput" value="2026-02-16">
                </div>
                <div class="config-group">
                    <label>Return Date</label>
                    <input type="date" id="retDateInput" value="2026-02-23">
                </div>
            </div>

            <div class="controls">
                <button class="btn-start" id="startBtn" onclick="startScan()">Start Parallel Scan</button>
                <button class="btn-stop" id="stopBtn" onclick="stopScan()">Stop Scan</button>
            </div>
        </div>

        <div class="panel">
            <h2>Scanner Progress</h2>

            <div class="summary-stats">
                <div class="summary-stat">
                    <div class="value" id="scannerCount">0</div>
                    <div class="label">Scanners</div>
                </div>
                <div class="summary-stat">
                    <div class="value" id="completeCount">0</div>
                    <div class="label">Complete</div>
                </div>
                <div class="summary-stat">
                    <div class="value" id="totalFlightsCount">0</div>
                    <div class="label">Flights Found</div>
                </div>
            </div>

            <div class="progress-bar">
                <div class="progress-fill" id="progressBar" style="width: 0%"></div>
            </div>

            <div class="scanner-grid" id="scannerGrid">
                <div class="empty-state" style="grid-column: 1/-1;">
                    Click "Start Parallel Scan" to launch scanners from multiple locations
                </div>
            </div>

            <div class="log-box" id="logBox">
                <div class="log-line">Ready to scan...</div>
            </div>
        </div>

        <div class="panel">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px; flex-wrap:wrap; gap:10px;">
                <h2>Flight Results</h2>
                <div style="display:flex; align-items:center; gap:20px;">
                    <div style="font-size:0.8em; color:#f39c12;">
                        Tip: To book foreign prices, connect to VPN in that country first
                    </div>
                    <label style="display:flex; align-items:center; gap:8px; cursor:pointer;">
                        <input type="checkbox" id="directOnly" onchange="loadResults()">
                        <span>Direct only</span>
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
                    <tr><td colspan="9" class="empty-state">No results yet</td></tr>
                </tbody>
            </table>
            <div id="pagination" style="display:flex; justify-content:center; gap:10px; margin-top:20px;"></div>
        </div>
    </div>

    <script>
        let pollInterval;

        async function startScan() {
            document.getElementById('startBtn').disabled = true;
            document.getElementById('stopBtn').style.display = 'block';
            document.getElementById('logBox').innerHTML = '';
            document.getElementById('scannerGrid').innerHTML = '<div class="empty-state" style="grid-column:1/-1;">Starting scanners...</div>';

            const config = {
                origin: document.getElementById('originInput').value.trim().toUpperCase(),
                destination: document.getElementById('destInput').value.trim().toUpperCase(),
                departure_date: document.getElementById('depDateInput').value,
                return_date: document.getElementById('retDateInput').value
            };

            await fetch('/api/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });

            pollInterval = setInterval(pollStatus, 1000);
        }

        async function stopScan() {
            await fetch('/api/stop', { method: 'POST' });
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').style.display = 'none';
            clearInterval(pollInterval);
        }

        async function pollStatus() {
            try {
                const [statusRes, logsRes] = await Promise.all([
                    fetch('/api/scanner-status'),
                    fetch('/api/logs')
                ]);

                const data = await statusRes.json();
                const logs = await logsRes.json();

                // Update summary
                document.getElementById('scannerCount').textContent = data.summary?.total_scanners || 0;
                document.getElementById('completeCount').textContent = data.summary?.complete || 0;
                document.getElementById('totalFlightsCount').textContent = data.summary?.total_flights || 0;

                // Progress bar
                const total = data.summary?.total_scanners || 1;
                const complete = data.summary?.complete || 0;
                document.getElementById('progressBar').style.width = Math.round((complete / total) * 100) + '%';

                // Scanner cards
                if (data.scanners?.length > 0) {
                    document.getElementById('scannerGrid').innerHTML = data.scanners.map(s => `
                        <div class="scanner-card ${s.status}">
                            <div class="scanner-country">${s.country}</div>
                            <div class="scanner-status">${s.status}</div>
                            <div class="scanner-flights">${s.progress?.flights_found || 0} flights</div>
                            ${s.vpn_city ? `<div class="scanner-city">${s.vpn_city}</div>` : ''}
                        </div>
                    `).join('');
                }

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

                // Stop polling when done
                if (!data.parallel_running && data.summary?.complete >= data.summary?.total_scanners && data.summary?.total_scanners > 0) {
                    clearInterval(pollInterval);
                    document.getElementById('startBtn').disabled = false;
                    document.getElementById('stopBtn').style.display = 'none';
                    loadResults();
                }
            } catch (e) {
                console.error('Poll error:', e);
            }
        }

        let currentPage = 1;

        async function loadResults(page = 1) {
            currentPage = page;
            const directOnly = document.getElementById('directOnly').checked;
            const res = await fetch(`/api/data?page=${page}&per_page=10&direct=${directOnly}`);
            const data = await res.json();

            if (data.best_deals?.length > 0) {
                document.getElementById('resultsBody').innerHTML = data.best_deals.map(d => `
                    <tr>
                        <td>${d.departure}<br><small style="color:#888">to ${d.return}</small></td>
                        <td>${d.times || '--'}</td>
                        <td>${d.return_times || '--'}</td>
                        <td><strong>${d.airline || 'Unknown'}</strong></td>
                        <td>${d.duration || '--'}</td>
                        <td>${d.stops === 0 ? '<span style="color:#00b894">Direct</span>' : d.stops + ' stop'}</td>
                        <td class="price">£${d.price?.toFixed(0) || '--'}</td>
                        <td><span class="vpn-badge">${d.vpn || 'UK'}</span></td>
                        <td>${d.url ? `<a href="${d.url}" target="_blank" style="color:#667eea;" onclick="return confirmBook('${d.vpn}')">Book</a>` : '--'}</td>
                    </tr>
                `).join('');

                const p = data.pagination;
                if (p?.total_pages > 1) {
                    let html = '';
                    if (p.page > 1) html += `<button onclick="loadResults(${p.page-1})" style="padding:8px 15px;border:none;border-radius:5px;background:#333;color:#fff;cursor:pointer;">Prev</button>`;
                    html += `<span style="padding:8px 15px;">Page ${p.page} of ${p.total_pages}</span>`;
                    if (p.page < p.total_pages) html += `<button onclick="loadResults(${p.page+1})" style="padding:8px 15px;border:none;border-radius:5px;background:#333;color:#fff;cursor:pointer;">Next</button>`;
                    document.getElementById('pagination').innerHTML = html;
                } else {
                    document.getElementById('pagination').innerHTML = `<span style="color:#666">${p?.total || 0} flights</span>`;
                }
            } else {
                document.getElementById('resultsBody').innerHTML = '<tr><td colspan="9" class="empty-state">No results yet</td></tr>';
                document.getElementById('pagination').innerHTML = '';
            }
        }

        function confirmBook(location) {
            if (!location || location.includes('UK') || location.includes('GB')) {
                return true; // UK prices, no VPN needed
            }

            const country = location.split(' - ')[0];
            const msg = `This price was found from ${location}.\\n\\nTo get this price, you need to:\\n1. Connect to a VPN in ${country}\\n2. Then click Book again\\n\\nContinue anyway?`;
            return confirm(msg);
        }

        // Initial load
        loadResults();
        setInterval(() => loadResults(currentPage), 5000);
    </script>
</body>
</html>'''
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_api_data(self):
        try:
            query = parse_qs(urlparse(self.path).query)
            page = int(query.get('page', [1])[0])
            per_page = int(query.get('per_page', [10])[0])
            direct_only = query.get('direct', ['false'])[0].lower() == 'true'
            offset = (page - 1) * per_page

            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

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
                    'times': metadata.get('times', ''),
                    'return_times': metadata.get('return_times', '')
                })

            conn.close()

            total_pages = (total + per_page - 1) // per_page
            self.send_json({
                'stats': {'best_price': best_price, 'total_searches': total},
                'best_deals': deals,
                'pagination': {'page': page, 'per_page': per_page, 'total': total, 'total_pages': total_pages}
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

    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


if __name__ == "__main__":
    os.chdir(str(Path(__file__).parent))

    init_database()
    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"\n{'='*50}")
    print("Flight Hacker")
    print(f"{'='*50}")
    print(f"\nOpen: http://localhost:{PORT}")
    print(f"\nPress Ctrl+C to stop\n")

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
