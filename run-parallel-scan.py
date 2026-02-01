#!/usr/bin/env python3
"""
Flight Hacker - Parallel VPN Scanner
Downloads fresh VPN configs from VPNGate and runs parallel Docker containers

This module can be:
1. Run directly: python3 run-parallel-scan.py [origin] [destination] [dep_date] [ret_date]
2. Imported: from `run-parallel-scan` import fetch_vpngate_configs, generate_docker_compose
"""

import subprocess
import sys
import os
import json
import urllib.request
import base64
import csv
import io
import sqlite3
import time
from pathlib import Path
from datetime import datetime

# Configuration
PROJECT_DIR = Path(__file__).parent
VPN_CONFIGS_DIR = PROJECT_DIR / "vpn_configs"
OUTPUT_DIR = PROJECT_DIR / "output"
DB_PATH = PROJECT_DIR / "flights.db"

# Default search params (can be overridden via command line)
DEFAULT_ORIGIN = "LHR"
DEFAULT_DESTINATION = "BOM"
DEFAULT_DEPARTURE_DATE = "2026-02-16"
DEFAULT_RETURN_DATE = "2026-02-23"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def fetch_vpngate_configs(output_dir=None):
    """
    Download fresh VPN configs from VPNGate

    Args:
        output_dir: Directory to save configs. Defaults to PROJECT_DIR/vpn_configs

    Returns:
        List of country codes with saved configs
    """
    vpn_dir = Path(output_dir) if output_dir else VPN_CONFIGS_DIR
    log("Fetching VPNGate server list...")

    try:
        url = "https://www.vpngate.net/api/iphone/"
        response = urllib.request.urlopen(url, timeout=30)
        data = response.read().decode('utf-8')
    except Exception as e:
        log(f"Error fetching VPNGate: {e}")
        return []

    lines = data.split('\n')
    csv_data = '\n'.join(lines[1:])
    reader = csv.reader(io.StringIO(csv_data))
    next(reader)  # Skip headers

    # Get best server per country
    best_servers = {}

    for row in reader:
        if len(row) < 15:
            continue
        try:
            country = row[6]
            score = int(row[2]) if row[2] else 0
            speed = int(row[4]) if row[4] else 0
            hostname = row[0]
            config_b64 = row[14] if len(row) > 14 else None

            if config_b64 and country:
                current = best_servers.get(country)
                if not current or score > current['score']:
                    best_servers[country] = {
                        'config_b64': config_b64,
                        'score': score,
                        'speed': speed,
                        'hostname': hostname
                    }
        except (ValueError, IndexError):
            continue

    # Save configs
    vpn_dir.mkdir(exist_ok=True)
    saved = []

    for country, data in best_servers.items():
        try:
            config = base64.b64decode(data['config_b64']).decode('utf-8')
            filepath = vpn_dir / f"{country}.ovpn"
            with open(filepath, 'w') as f:
                f.write(config)
            saved.append(country)
            log(f"  {country}: {data['hostname']} ({data['speed']/1000000:.1f} Mbps)")
        except Exception as e:
            log(f"  {country}: Error - {e}")

    # Create credentials file
    creds_file = vpn_dir / "credentials.txt"
    with open(creds_file, 'w') as f:
        f.write("vpn\nvpn\n")

    return saved


def generate_docker_compose(countries, origin, destination, dep_date, ret_date,
                           output_path=None, project_dir=None):
    """
    Generate docker-compose.yml for all VPN locations

    Args:
        countries: List of country codes to create scanner services for
        origin: Airport code (e.g., 'LHR')
        destination: Airport code (e.g., 'BOM')
        dep_date: Departure date (YYYY-MM-DD)
        ret_date: Return date (YYYY-MM-DD)
        output_path: Path to write compose file. Defaults to PROJECT_DIR/docker-compose.yml
        project_dir: Project directory for build context. Defaults to PROJECT_DIR

    Returns:
        Path to generated compose file
    """
    proj_dir = Path(project_dir) if project_dir else PROJECT_DIR
    compose_path = Path(output_path) if output_path else proj_dir / "docker-compose.yml"

    services = {}

    # UK baseline (no VPN)
    services['scanner-uk'] = {
        'build': '.',
        'container_name': 'flight-scanner-uk',
        'environment': [
            'VPN_COUNTRY=',
            f'ORIGIN={origin}',
            f'DESTINATION={destination}',
            f'DEPARTURE_DATE={dep_date}',
            f'RETURN_DATE={ret_date}',
            'OUTPUT_FILE=/app/output/UK.json'
        ],
        'volumes': ['./output:/app/output'],
        'cap_add': ['NET_ADMIN'],
        'devices': ['/dev/net/tun:/dev/net/tun']
    }

    # VPN containers
    for country in countries:
        service_name = f'scanner-{country.lower()}'
        services[service_name] = {
            'build': '.',
            'container_name': f'flight-scanner-{country.lower()}',
            'environment': [
                f'VPN_COUNTRY={country}',
                f'ORIGIN={origin}',
                f'DESTINATION={destination}',
                f'DEPARTURE_DATE={dep_date}',
                f'RETURN_DATE={ret_date}',
                f'OUTPUT_FILE=/app/output/{country}.json'
            ],
            'volumes': [
                './output:/app/output',
                './vpn_configs:/app/vpn_configs:ro'
            ],
            'cap_add': ['NET_ADMIN'],
            'devices': ['/dev/net/tun:/dev/net/tun']
        }

    compose = {
        'version': '3.8',
        'services': services
    }

    # Write YAML manually (avoid PyYAML dependency)
    yaml_content = "# Auto-generated by run-parallel-scan.py\nversion: '3.8'\n\nservices:\n"

    for svc_name, svc_config in services.items():
        yaml_content += f"  {svc_name}:\n"
        yaml_content += f"    build: .\n"
        yaml_content += f"    container_name: {svc_config['container_name']}\n"
        yaml_content += f"    environment:\n"
        for env in svc_config['environment']:
            yaml_content += f"      - {env}\n"
        yaml_content += f"    volumes:\n"
        for vol in svc_config['volumes']:
            yaml_content += f"      - {vol}\n"
        yaml_content += f"    cap_add:\n"
        yaml_content += f"      - NET_ADMIN\n"
        yaml_content += f"    devices:\n"
        yaml_content += f"      - /dev/net/tun:/dev/net/tun\n"
        yaml_content += "\n"

    with open(compose_path, 'w') as f:
        f.write(yaml_content)

    log(f"Generated docker-compose.yml with {len(services)} services")
    return compose_path


def init_database(db_path=None):
    """Initialize SQLite database"""
    db = db_path or DB_PATH
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS flights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            departure_date TEXT NOT NULL,
            return_date TEXT NOT NULL,
            airline TEXT,
            price_gbp REAL,
            price_currency TEXT,
            price_original REAL,
            vpn_location TEXT,
            booking_site TEXT,
            stops INTEGER,
            duration_minutes INTEGER,
            booking_url TEXT,
            search_metadata TEXT
        )
    ''')
    conn.commit()
    conn.close()


def clear_old_results(output_dir=None, db_path=None):
    """Clear previous scan results"""
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    db = db_path or DB_PATH

    # Clear output JSON files
    out_dir.mkdir(exist_ok=True)
    for f in out_dir.glob("*.json"):
        f.unlink()

    # Clear database
    if Path(db).exists():
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM flights")
        conn.commit()
        conn.close()

    log("Cleared previous results")


def aggregate_results(output_dir=None, db_path=None):
    """Aggregate JSON results into database"""
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    db = db_path or DB_PATH

    init_database(db)

    conn = sqlite3.connect(db)
    cursor = conn.cursor()

    total = 0
    for json_file in out_dir.glob("*.json"):
        # Skip status files
        if json_file.name.startswith("status_"):
            continue

        try:
            with open(json_file) as f:
                results = json.load(f)

            for r in results:
                cursor.execute('''
                    INSERT INTO flights (
                        timestamp, departure_date, return_date, airline,
                        price_gbp, price_currency, price_original, vpn_location,
                        booking_site, stops, duration_minutes, booking_url, search_metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    datetime.now().isoformat(),
                    r.get('departure_date'),
                    r.get('return_date'),
                    r.get('airline'),
                    r.get('price_gbp'),
                    r.get('price_currency'),
                    r.get('price_original'),
                    r.get('vpn_location'),
                    r.get('booking_site'),
                    r.get('stops'),
                    r.get('duration_minutes'),
                    r.get('booking_url'),
                    json.dumps(r.get('metadata', {}))
                ))

            total += len(results)
            log(f"  {json_file.name}: {len(results)} flights")
        except Exception as e:
            log(f"  {json_file.name}: Error - {e}")

    conn.commit()
    conn.close()

    return total


def run_scan(origin, destination, dep_date, ret_date):
    """Run the full parallel scan"""

    print("=" * 60)
    print("FLIGHT HACKER - Parallel VPN Scanner")
    print("=" * 60)
    print(f"Route: {origin} -> {destination}")
    print(f"Dates: {dep_date} to {ret_date}")
    print("=" * 60)
    print()

    # Step 1: Fetch fresh VPN configs
    countries = fetch_vpngate_configs()
    if not countries:
        log("No VPN servers available, running UK-only scan")
        countries = []
    else:
        log(f"Found {len(countries)} VPN locations: {', '.join(countries)}")

    print()

    # Step 2: Clear old results
    clear_old_results()

    # Step 3: Generate docker-compose.yml
    generate_docker_compose(countries, origin, destination, dep_date, ret_date)

    print()

    # Step 4: Build Docker image
    log("Building Docker image (this may take a few minutes first time)...")
    result = subprocess.run(
        ["docker-compose", "build"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        log(f"Build failed: {result.stderr}")
        return False
    log("Build complete")

    print()

    # Step 5: Run containers
    log(f"Starting {len(countries) + 1} parallel scanners...")
    log("(This will take 2-5 minutes)")
    print()

    # Run without --abort-on-container-exit so all containers finish independently
    result = subprocess.run(
        ["docker-compose", "up"],
        cwd=PROJECT_DIR
    )

    print()

    # Step 6: Aggregate results
    log("Aggregating results...")
    total = aggregate_results()

    print()
    print("=" * 60)
    print(f"SCAN COMPLETE: {total} flights found")
    print("=" * 60)

    # Show best deals
    if total > 0:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT airline, price_gbp, vpn_location, stops
            FROM flights
            ORDER BY price_gbp ASC
            LIMIT 5
        ''')
        print("\nTop 5 Best Prices:")
        for row in cursor.fetchall():
            stops_txt = "direct" if row[3] == 0 else f"{row[3]} stop"
            print(f"  £{row[1]:.0f} - {row[0]} ({row[2]}) [{stops_txt}]")
        conn.close()

    print()
    print("View full results: python3 web_server.py")
    print("Then open: http://localhost:8000")

    return True


if __name__ == "__main__":
    # Parse command line args
    origin = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ORIGIN
    destination = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DESTINATION
    dep_date = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_DEPARTURE_DATE
    ret_date = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_RETURN_DATE

    run_scan(origin, destination, dep_date, ret_date)
