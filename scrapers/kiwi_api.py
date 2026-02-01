#!/usr/bin/env python3
"""
Kiwi Tequila API Client
Uses the Kiwi.com Tequila API to search for flights
Free tier available - get API key from https://tequila.kiwi.com/
"""

import os
import requests
from datetime import datetime
from typing import List, Dict, Optional

# Kiwi Tequila API configuration
KIWI_API_BASE = "https://api.tequila.kiwi.com/v2"

# Get API key from environment variable
# Sign up at https://tequila.kiwi.com/ to get a free API key
KIWI_API_KEY = os.environ.get('KIWI_API_KEY', '')


class KiwiAPIClient:
    """Client for Kiwi Tequila flight search API"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or KIWI_API_KEY
        self.base_url = KIWI_API_BASE

        if not self.api_key:
            print("Warning: KIWI_API_KEY not set. Set it with: export KIWI_API_KEY='your_key'")
            print("Get a free API key at: https://tequila.kiwi.com/")

    def _convert_date_format(self, date_str: str) -> str:
        """Convert YYYY-MM-DD to DD/MM/YYYY format required by Kiwi API"""
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return dt.strftime('%d/%m/%Y')
        except ValueError:
            return date_str

    def _parse_price_to_gbp(self, price: float, currency: str) -> float:
        """
        Convert price to GBP (approximate conversion)
        For accurate rates, use a currency API
        """
        # Approximate exchange rates (update as needed)
        rates_to_gbp = {
            'GBP': 1.0,
            'EUR': 0.86,
            'USD': 0.79,
            'INR': 0.0095,
            'JPY': 0.0053,
        }

        rate = rates_to_gbp.get(currency, 1.0)
        return round(price * rate, 2)

    def search(self, origin: str, destination: str,
               departure_date: str, return_date: str,
               adults: int = 1,
               max_results: int = 10) -> List[Dict]:
        """
        Search for flights using Kiwi Tequila API

        Args:
            origin: Origin airport/city code (e.g., 'LON', 'london_gb')
            destination: Destination airport/city code (e.g., 'BOM', 'mumbai_in')
            departure_date: Departure date (YYYY-MM-DD)
            return_date: Return date (YYYY-MM-DD)
            adults: Number of adult passengers
            max_results: Maximum number of results to return

        Returns:
            List of flight dictionaries
        """
        if not self.api_key:
            print("  Kiwi API key not configured, skipping search")
            return []

        results = []

        try:
            # Convert dates to Kiwi format
            dep_date = self._convert_date_format(departure_date)
            ret_date = self._convert_date_format(return_date)

            # API parameters
            params = {
                'fly_from': origin,
                'fly_to': destination,
                'date_from': dep_date,
                'date_to': dep_date,  # Same date for exact match
                'return_from': ret_date,
                'return_to': ret_date,  # Same date for exact match
                'adults': adults,
                'curr': 'GBP',
                'limit': max_results,
                'sort': 'price',
                'flight_type': 'round',
            }

            headers = {
                'apikey': self.api_key,
                'Content-Type': 'application/json',
            }

            print(f"  Searching Kiwi API...")
            response = requests.get(
                f"{self.base_url}/search",
                params=params,
                headers=headers,
                timeout=30
            )

            if response.status_code == 401:
                print("  Kiwi API authentication failed. Check your API key.")
                return []

            response.raise_for_status()
            data = response.json()

            flights = data.get('data', [])
            print(f"  Kiwi API returned {len(flights)} results")

            for flight in flights[:max_results]:
                try:
                    # Extract airline information
                    airlines = set()
                    for route in flight.get('route', []):
                        airline = route.get('airline')
                        if airline:
                            airlines.add(airline)

                    airline_str = ', '.join(sorted(airlines)) if airlines else 'Multiple'

                    # Calculate total stops (routes - 2 for round trip with no stops)
                    route_count = len(flight.get('route', []))
                    # For round trip: outbound legs + return legs
                    # Minimum is 2 (one outbound, one return)
                    stops = max(0, (route_count - 2) // 2)

                    # Duration in minutes (Kiwi provides in seconds for each leg)
                    duration_seconds = flight.get('duration', {}).get('total', 0)
                    duration_minutes = duration_seconds // 60 if duration_seconds else None

                    # Price
                    price = flight.get('price', 0)
                    currency = flight.get('currency', 'GBP')

                    # Booking link
                    booking_url = flight.get('deep_link', '')

                    result = {
                        'departure_date': departure_date,
                        'return_date': return_date,
                        'airline': airline_str,
                        'price_gbp': price if currency == 'GBP' else self._parse_price_to_gbp(price, currency),
                        'price_currency': currency,
                        'price_original': price,
                        'stops': stops,
                        'duration_minutes': duration_minutes,
                        'booking_site': 'Kiwi.com',
                        'booking_url': booking_url,
                        'metadata': {
                            'kiwi_id': flight.get('id'),
                            'quality': flight.get('quality'),
                            'route_count': route_count,
                        }
                    }
                    results.append(result)

                    duration_str = f"{duration_minutes // 60}h {duration_minutes % 60}m" if duration_minutes else "N/A"
                    print(f"    {airline_str} - £{result['price_gbp']:.2f} ({stops} stops, {duration_str})")

                except Exception as e:
                    print(f"    Error parsing flight: {e}")
                    continue

        except requests.RequestException as e:
            print(f"  Kiwi API request failed: {e}")
        except Exception as e:
            print(f"  Kiwi API error: {e}")

        return results

    def get_locations(self, query: str, location_type: str = 'airport') -> List[Dict]:
        """
        Search for location codes

        Args:
            query: Search query (e.g., 'London', 'Mumbai')
            location_type: Type of location ('airport', 'city', 'country')

        Returns:
            List of matching locations
        """
        if not self.api_key:
            return []

        try:
            params = {
                'term': query,
                'location_types': location_type,
                'limit': 5,
            }

            headers = {
                'apikey': self.api_key,
            }

            response = requests.get(
                f"{self.base_url}/locations/query",
                params=params,
                headers=headers,
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            return data.get('locations', [])

        except Exception as e:
            print(f"Location search failed: {e}")
            return []


def search_kiwi(origin: str, destination: str,
                departure_date: str, return_date: str,
                api_key: str = None) -> List[Dict]:
    """
    Convenience function to search Kiwi API

    Args:
        origin: Origin airport code
        destination: Destination airport code
        departure_date: Departure date (YYYY-MM-DD)
        return_date: Return date (YYYY-MM-DD)
        api_key: Optional API key (uses env var if not provided)

    Returns:
        List of flight dictionaries
    """
    client = KiwiAPIClient(api_key=api_key)
    return client.search(origin, destination, departure_date, return_date)


if __name__ == "__main__":
    print("\n" + "="*60)
    print("Kiwi Tequila API - Test")
    print("="*60 + "\n")

    if not KIWI_API_KEY:
        print("To use the Kiwi API, you need an API key:")
        print("1. Sign up at https://tequila.kiwi.com/")
        print("2. Create a project and get your API key")
        print("3. Set it with: export KIWI_API_KEY='your_key'")
        print("\nThe free tier includes 3,000 searches per month.")
        exit(0)

    # Test search
    client = KiwiAPIClient()

    print("Searching for London to Mumbai flights...")
    results = client.search(
        origin='london_gb',
        destination='bombay_in',
        departure_date='2026-02-16',
        return_date='2026-02-23'
    )

    print(f"\nResults: {len(results)} flights found")
    for r in results[:5]:
        print(f"  {r['airline']} - £{r['price_gbp']:.2f} ({r['stops']} stops)")
        if r.get('booking_url'):
            print(f"    Book: {r['booking_url'][:60]}...")
