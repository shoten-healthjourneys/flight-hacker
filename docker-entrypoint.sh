#!/bin/bash
#
# Flight Hacker Container Entrypoint
# Connects to VPN (if configured), then runs the flight scraper
# Writes status updates to /app/output/status_{COUNTRY}.json for real-time monitoring
#

set -e

# Configuration from environment
VPN_COUNTRY="${VPN_COUNTRY:-}"
ORIGIN="${ORIGIN:-LHR}"
DESTINATION="${DESTINATION:-BOM}"
DEPARTURE_DATE="${DEPARTURE_DATE:-}"
RETURN_DATE="${RETURN_DATE:-}"
OUTPUT_FILE="${OUTPUT_FILE:-/app/output/results.json}"

# Determine status file location based on VPN_COUNTRY or default to UK
STATUS_COUNTRY="${VPN_COUNTRY:-UK}"
STATUS_FILE="/app/output/status_${STATUS_COUNTRY}.json"

# Function to write status update
write_status() {
    local status="$1"
    local phase="$2"
    local vpn_connected="${3:-false}"
    local vpn_city="${4:-}"
    local searches_completed="${5:-0}"
    local searches_total="${6:-0}"
    local flights_found="${7:-0}"
    local error_message="${8:-}"

    cat > "$STATUS_FILE" << EOF
{
  "country": "$STATUS_COUNTRY",
  "status": "$status",
  "phase": "$phase",
  "vpn_connected": $vpn_connected,
  "vpn_city": "$vpn_city",
  "progress": {
    "searches_completed": $searches_completed,
    "searches_total": $searches_total,
    "flights_found": $flights_found
  },
  "error": "$error_message",
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
}

echo "=========================================="
echo "Flight Hacker Scraper Container"
echo "=========================================="
echo "Origin: $ORIGIN"
echo "Destination: $DESTINATION"
echo "Departure: $DEPARTURE_DATE"
echo "Return: $RETURN_DATE"
echo "VPN Country: ${VPN_COUNTRY:-None (direct)}"
echo ""

# Create output directory
mkdir -p "$(dirname "$STATUS_FILE")"

# Write initial status
write_status "starting" "initializing" "false" "" "0" "0" "0"

# Function to get current location
get_location() {
    curl -s --connect-timeout 10 https://ipapi.co/json/ 2>/dev/null || \
    curl -s --connect-timeout 10 http://ip-api.com/json/ 2>/dev/null || \
    echo '{"country":"Unknown","city":"Unknown","country_code":"XX"}'
}

# Connect to VPN if country is specified
if [ -n "$VPN_COUNTRY" ] && [ -f "/app/vpn_configs/${VPN_COUNTRY}.ovpn" ]; then
    echo "[VPN] Connecting to $VPN_COUNTRY..."
    write_status "connecting" "vpn_connect" "false" "" "0" "0" "0"

    # Start OpenVPN in background
    # Note: --data-ciphers includes AES-128-CBC for compatibility with VPNGate servers
    openvpn --config "/app/vpn_configs/${VPN_COUNTRY}.ovpn" \
            --auth-user-pass /app/vpn_configs/credentials.txt \
            --data-ciphers AES-256-GCM:AES-128-GCM:AES-128-CBC \
            --daemon --log /tmp/openvpn.log

    # Wait for connection (max 60 seconds)
    TIMEOUT=60
    ELAPSED=0
    CONNECTED=false
    VPN_CITY=""

    while [ $ELAPSED -lt $TIMEOUT ]; do
        sleep 3
        ELAPSED=$((ELAPSED + 3))

        # Check if tun interface is up
        if ip addr show tun0 &>/dev/null; then
            LOCATION=$(get_location)
            COUNTRY=$(echo "$LOCATION" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('country_code', d.get('countryCode', 'XX')))" 2>/dev/null || echo "XX")
            VPN_CITY=$(echo "$LOCATION" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('city', 'Unknown'))" 2>/dev/null || echo "Unknown")

            echo "[VPN] Connected: $VPN_CITY, $COUNTRY"
            write_status "connected" "vpn_ready" "true" "$VPN_CITY" "0" "0" "0"
            CONNECTED=true
            break
        fi

        echo "[VPN] Waiting for connection... ($ELAPSED/$TIMEOUT sec)"
    done

    if [ "$CONNECTED" = false ]; then
        echo "[VPN] Connection timeout - proceeding without VPN"
        write_status "error" "vpn_timeout" "false" "" "0" "0" "0" "VPN connection timed out"
        cat /tmp/openvpn.log 2>/dev/null || true
    fi
else
    echo "[VPN] No VPN configured - using direct connection"
    VPN_CITY="Direct"
fi

# Get current location for logging
LOCATION=$(get_location)
LOCATION_LABEL=$(echo "$LOCATION" | python3 -c "import sys,json; d=json.load(sys.stdin); cc=d.get('country_code', d.get('countryCode', 'XX')); city=d.get('city', 'Unknown'); print(f'{cc} - {city}')" 2>/dev/null || echo "XX - Unknown")
echo ""
echo "[Location] Scanning from: $LOCATION_LABEL"
echo ""

# Create output directory
mkdir -p "$(dirname "$OUTPUT_FILE")"

# Update status to scraping
write_status "scraping" "google_flights" "true" "$VPN_CITY" "0" "1" "0"

# Run the scraper with status updates
python3 -c "
import json
import sys
import os
sys.path.insert(0, '/app')

from scrapers.google_flights import search_google_flights
from scrapers.skyscanner import search_skyscanner

ORIGIN = '$ORIGIN'
DESTINATION = '$DESTINATION'
DEPARTURE_DATE = '$DEPARTURE_DATE'
RETURN_DATE = '$RETURN_DATE'
VPN_LOCATION = '$LOCATION_LABEL'
OUTPUT_FILE = '$OUTPUT_FILE'
STATUS_FILE = '$STATUS_FILE'
VPN_CITY = '$VPN_CITY'
STATUS_COUNTRY = '$STATUS_COUNTRY'

def write_status(status, phase, searches_completed, searches_total, flights_found):
    status_data = {
        'country': STATUS_COUNTRY,
        'status': status,
        'phase': phase,
        'vpn_connected': True,
        'vpn_city': VPN_CITY,
        'progress': {
            'searches_completed': searches_completed,
            'searches_total': searches_total,
            'flights_found': flights_found
        },
        'error': '',
        'updated_at': __import__('datetime').datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    }
    with open(STATUS_FILE, 'w') as f:
        json.dump(status_data, f, indent=2)

print(f'[Scraper] Searching {ORIGIN} -> {DESTINATION}')
print(f'[Scraper] Dates: {DEPARTURE_DATE} to {RETURN_DATE}')
print('')

all_results = []
total_searches = 2  # Google Flights + Skyscanner
completed_searches = 0

# Google Flights
print('[Google Flights] Starting search...')
write_status('scraping', 'google_flights', completed_searches, total_searches, len(all_results))
try:
    gf_results = search_google_flights(
        origin=ORIGIN,
        destination=DESTINATION,
        departure_date=DEPARTURE_DATE,
        return_date=RETURN_DATE,
        headless=True,
        country_code=VPN_LOCATION.split(' - ')[0] if VPN_LOCATION else None
    )
    for r in gf_results:
        r['vpn_location'] = VPN_LOCATION
    all_results.extend(gf_results)
    print(f'[Google Flights] Found {len(gf_results)} results')
except Exception as e:
    print(f'[Google Flights] Error: {e}')
completed_searches += 1
write_status('scraping', 'skyscanner', completed_searches, total_searches, len(all_results))

# Skyscanner
print('[Skyscanner] Starting search...')
try:
    ss_results = search_skyscanner(
        origin=ORIGIN,
        destination=DESTINATION,
        departure_date=DEPARTURE_DATE,
        return_date=RETURN_DATE,
        headless=True
    )
    for r in ss_results:
        r['vpn_location'] = VPN_LOCATION
    all_results.extend(ss_results)
    print(f'[Skyscanner] Found {len(ss_results)} results')
except Exception as e:
    print(f'[Skyscanner] Error: {e}')
completed_searches += 1

# Write results to output file
print(f'')
print(f'[Output] Writing {len(all_results)} results to {OUTPUT_FILE}')
with open(OUTPUT_FILE, 'w') as f:
    json.dump(all_results, f, indent=2)

# Final status - complete
write_status('complete', 'done', completed_searches, total_searches, len(all_results))

print('')
print('========================================')
print(f'SCAN COMPLETE: {len(all_results)} flights found')
if all_results:
    best = min(all_results, key=lambda x: x.get('price_gbp', float('inf')))
    print(f'Best price: £{best[\"price_gbp\"]:.0f} - {best.get(\"airline\", \"Unknown\")}')
print('========================================')
"

echo ""
echo "Container finished."
