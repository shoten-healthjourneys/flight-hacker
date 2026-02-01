# Flight Hacker

Automated flight price scanner that compares prices across multiple VPN locations using parallel Docker containers.

## Features

- **Parallel VPN Scanning** - Run 8+ scrapers simultaneously, each through a different VPN location
- **Geo-pricing Detection** - Find price differences based on your apparent location
- **Google Flights Scraper** - Playwright-based headless browser scraping
- **Skyscanner Scraper** - Additional price source
- **Web Dashboard** - Real-time results at http://localhost:8000
- **SQLite Database** - All results stored for analysis

## Quick Start

### Prerequisites

- Docker Desktop
- Python 3.9+

### 1. Run Parallel Scan

```bash
python3 run-parallel-scan.py LHR BOM 2026-02-16 2026-02-23
```

This will:
1. Fetch fresh VPN configs from VPNGate (free)
2. Spin up 8 Docker containers in parallel
3. Each container connects to a different VPN location
4. Scrape Google Flights from each location
5. Aggregate results into the database

### 2. View Results

```bash
python3 web_server.py
# Open http://localhost:8000
```

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│                   run-parallel-scan.py                   │
│  1. Fetches VPN configs from VPNGate                    │
│  2. Generates docker-compose.yml                         │
│  3. Launches parallel containers                         │
└─────────────────────────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │ scanner  │    │ scanner  │    │ scanner  │
    │   -uk    │    │   -jp    │    │   -kr    │
    │ (no VPN) │    │(VPN: JP) │    │(VPN: KR) │
    └────┬─────┘    └────┬─────┘    └────┬─────┘
         │               │               │
         ▼               ▼               ▼
    Google Flights  Google Flights  Google Flights
    (GBP prices)    (USD prices)    (KRW prices)
         │               │               │
         └───────────────┼───────────────┘
                         ▼
                  ┌─────────────┐
                  │  flights.db │
                  │  (SQLite)   │
                  └─────────────┘
                         │
                         ▼
                  ┌─────────────┐
                  │  Dashboard  │
                  │ :8000       │
                  └─────────────┘
```

## Project Structure

```
flight-hacker/
├── run-parallel-scan.py    # Main entry point - orchestrates Docker scan
├── web_server.py           # Web dashboard + manual scan launcher
├── flight_tracker.py       # Database management
├── docker-compose.yml      # Generated - container orchestration
├── docker-entrypoint.sh    # Container startup script
├── Dockerfile              # Scanner container image
├── scrapers/
│   ├── google_flights.py   # Playwright-based Google Flights scraper
│   └── skyscanner.py       # Skyscanner scraper
├── vpn_configs/            # VPN configuration files
│   ├── credentials.txt     # VPNGate credentials (vpn/vpn)
│   ├── JP.ovpn            # Auto-downloaded from VPNGate
│   ├── KR.ovpn
│   └── ...
├── output/                 # JSON results from each container
└── flights.db              # SQLite database with all results
```

## VPN Configuration

### Default: VPNGate (Free)

The scanner automatically downloads free VPN configs from [VPNGate](https://www.vpngate.net/). These are volunteer-run servers from the University of Tsukuba, Japan.

**Limitations:**
- Server availability varies
- Some locations may be slow or unavailable
- Credentials are always `vpn` / `vpn`

### Optional: ProtonVPN (Paid - More Reliable)

For more reliable geo-pricing comparison:

1. Get OpenVPN configs from https://account.protonvpn.com/downloads
2. Save as `vpn_configs/US.ovpn`, `vpn_configs/NL.ovpn`, etc.
3. Create `vpn_configs/credentials.txt` with your OpenVPN credentials

## Commands

### Run Full Parallel Scan
```bash
python3 run-parallel-scan.py <origin> <destination> <departure> <return>

# Example:
python3 run-parallel-scan.py LHR BOM 2026-02-16 2026-02-23
```

### Start Web Dashboard
```bash
python3 web_server.py
# Open http://localhost:8000
```

### Query Database Directly
```bash
# Best prices
sqlite3 flights.db "SELECT airline, price_gbp, vpn_location FROM flights ORDER BY price_gbp LIMIT 10;"

# Price by location
sqlite3 flights.db "SELECT vpn_location, MIN(price_gbp), AVG(price_gbp) FROM flights GROUP BY vpn_location;"
```

### Manual Container Control
```bash
# Build containers
docker-compose build

# Run single container
docker-compose run --rm scanner-uk

# Run all containers
docker-compose up

# View logs
docker-compose logs -f
```

## Example Results

```
PRICE COMPARISON BY LOCATION:
  GB - London:    min £542, avg £651 (shown in GBP)
  JP - Natori:    min £589, avg £708 (shown in USD)
  KR - Seoul:     min £612, avg £734 (shown in KRW)

TOP 5 CHEAPEST FLIGHTS:
  £542 - KLM [1 stop] from GB - London
  £550 - Air France [1 stop] from GB - London
  £589 - KLM [1 stop] from JP - Natori
  £593 - KLM [1 stop] from GB - London
  £598 - Air France [1 stop] from JP - Natori
```

## Troubleshooting

### Docker not running
```bash
open -a Docker
# Wait 30 seconds for startup
```

### VPN not connecting
Check the OpenVPN logs:
```bash
docker-compose run --rm scanner-jp cat /tmp/openvpn.log
```

Common issues:
- Cipher mismatch (fixed in docker-entrypoint.sh)
- Server unavailable (try different VPNGate servers)
- Credentials wrong (VPNGate uses `vpn`/`vpn`)

### No results from scraper
Google Flights may show consent page in local language. The scraper handles multiple languages but may need updates for new ones.

### Port 8000 in use
```bash
lsof -ti:8000 | xargs kill -9
```

## Technical Notes

- **Playwright** runs headless Chromium inside Docker containers
- **OpenVPN** connects to VPN before scraping starts
- **Multi-language consent** handling for Google's cookie popups
- **Currency conversion** - all prices normalized to GBP
- **Parallel execution** - all containers run simultaneously

## License

MIT
