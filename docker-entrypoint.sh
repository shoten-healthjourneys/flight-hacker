#!/bin/bash
#
# Flight Hacker Container Entrypoint
# Connects to VPN (if configured), then runs the flight scraper
#

set -e

# Configuration from environment
VPN_COUNTRY="${VPN_COUNTRY:-}"
ORIGIN="${ORIGIN:-LHR}"
DESTINATION="${DESTINATION:-BOM}"
DEPARTURE_DATE="${DEPARTURE_DATE:-}"
RETURN_DATE="${RETURN_DATE:-}"
OUTPUT_FILE="${OUTPUT_FILE:-/app/output/results.json}"

echo "=========================================="
echo "Flight Hacker Scraper Container"
echo "=========================================="
echo "Origin: $ORIGIN"
echo "Destination: $DESTINATION"
echo "Departure: $DEPARTURE_DATE"
echo "Return: $RETURN_DATE"
echo "VPN Country: ${VPN_COUNTRY:-None (direct)}"
echo ""

# Function to get current location
get_location() {
    curl -s --connect-timeout 10 https://ipapi.co/json/ 2>/dev/null || \
    curl -s --connect-timeout 10 http://ip-api.com/json/ 2>/dev/null || \
    echo '{"country":"Unknown","city":"Unknown","country_code":"XX"}'
}

# Connect to VPN if country is specified
if [ -n "$VPN_COUNTRY" ] && [ -f "/app/vpn_configs/${VPN_COUNTRY}.ovpn" ]; then
    echo "[VPN] Connecting to $VPN_COUNTRY..."

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

    while [ $ELAPSED -lt $TIMEOUT ]; do
        sleep 3
        ELAPSED=$((ELAPSED + 3))

        # Check if tun interface is up
        if ip addr show tun0 &>/dev/null; then
            LOCATION=$(get_location)
            COUNTRY=$(echo "$LOCATION" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('country_code', d.get('countryCode', 'XX')))" 2>/dev/null || echo "XX")
            CITY=$(echo "$LOCATION" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('city', 'Unknown'))" 2>/dev/null || echo "Unknown")

            echo "[VPN] Connected: $CITY, $COUNTRY"
            CONNECTED=true
            break
        fi

        echo "[VPN] Waiting for connection... ($ELAPSED/$TIMEOUT sec)"
    done

    if [ "$CONNECTED" = false ]; then
        echo "[VPN] Connection timeout - proceeding without VPN"
        cat /tmp/openvpn.log 2>/dev/null || true
    fi
else
    echo "[VPN] No VPN configured - using direct connection"
fi

# Get current location for logging
LOCATION=$(get_location)
LOCATION_LABEL=$(echo "$LOCATION" | python3 -c "import sys,json; d=json.load(sys.stdin); cc=d.get('country_code', d.get('countryCode', 'XX')); city=d.get('city', 'Unknown'); print(f'{cc} - {city}')" 2>/dev/null || echo "XX - Unknown")
echo ""
echo "[Location] Scanning from: $LOCATION_LABEL"
echo ""

# Create output directory
mkdir -p "$(dirname "$OUTPUT_FILE")"

# Run the scraper
python3 -c "
import json
import sys
sys.path.insert(0, '/app')

from scrapers.google_flights import search_google_flights
from scrapers.skyscanner import search_skyscanner

ORIGIN = '$ORIGIN'
DESTINATION = '$DESTINATION'
DEPARTURE_DATE = '$DEPARTURE_DATE'
RETURN_DATE = '$RETURN_DATE'
VPN_LOCATION = '$LOCATION_LABEL'
OUTPUT_FILE = '$OUTPUT_FILE'

print(f'[Scraper] Searching {ORIGIN} -> {DESTINATION}')
print(f'[Scraper] Dates: {DEPARTURE_DATE} to {RETURN_DATE}')
print('')

all_results = []

# Google Flights
print('[Google Flights] Starting search...')
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

# Write results to output file
print(f'')
print(f'[Output] Writing {len(all_results)} results to {OUTPUT_FILE}')
with open(OUTPUT_FILE, 'w') as f:
    json.dump(all_results, f, indent=2)

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
