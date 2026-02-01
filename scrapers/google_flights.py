#!/usr/bin/env python3
"""
Google Flights Scraper
Uses Playwright with stealth mode to scrape flight prices from Google Flights
"""

import re
from datetime import datetime
from typing import List, Dict, Optional

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("Warning: playwright not installed. Run: pip install playwright && playwright install chromium")


class GoogleFlightsScraper:
    """Scrapes flight prices from Google Flights using headless browser"""

    # Localized Google Flights domains by country
    GOOGLE_DOMAINS = {
        'US': 'www.google.com',
        'GB': 'www.google.co.uk',
        'NL': 'www.google.nl',
        'JP': 'www.google.co.jp',
        'DE': 'www.google.de',
        'FR': 'www.google.fr',
        'IN': 'www.google.co.in',
    }

    def __init__(self, headless: bool = True, country_code: str = None):
        self.headless = headless
        self.country_code = country_code
        # Always use google.com for consistent search behavior
        self.base_url = "https://www.google.com/travel/flights"

    def build_search_url(self, origin: str, destination: str,
                         departure_date: str, return_date: str) -> str:
        """
        Build Google Flights search URL

        Args:
            origin: Origin airport code (e.g., 'LON')
            destination: Destination airport code (e.g., 'BOM')
            departure_date: Departure date (YYYY-MM-DD)
            return_date: Return date (YYYY-MM-DD)

        Returns:
            Google Flights search URL
        """
        # Google Flights URL format for round trip
        # Use hl=en to force English interface
        url = (
            f"{self.base_url}/search?"
            f"q=flights+from+{origin}+to+{destination}+"
            f"on+{departure_date}+returning+{return_date}"
            f"&hl=en"
        )
        return url

    def _parse_price(self, price_text: str) -> tuple:
        """Extract numeric price and currency from text like '£285' or '$350'
        Returns (price, currency) tuple
        """
        if not price_text:
            return None, None

        # Detect currency
        currency = 'GBP'
        if '$' in price_text:
            currency = 'USD'
        elif '€' in price_text:
            currency = 'EUR'
        elif '¥' in price_text:
            currency = 'JPY'

        # Remove currency symbols and commas, extract number
        match = re.search(r'[\d,]+(?:\.\d{2})?', price_text.replace(',', ''))
        if match:
            try:
                return float(match.group()), currency
            except ValueError:
                return None, None
        return None, None

    def _convert_to_gbp(self, price: float, currency: str) -> float:
        """Convert price to GBP"""
        # Approximate exchange rates
        rates = {
            'GBP': 1.0,
            'USD': 0.79,
            'EUR': 0.86,
            'JPY': 0.0053,
        }
        rate = rates.get(currency, 1.0)
        return round(price * rate, 2)

    def _parse_duration(self, duration_text: str) -> Optional[int]:
        """Parse duration like '12 hr 30 min' to minutes"""
        if not duration_text:
            return None

        total_minutes = 0
        # Match hours
        hr_match = re.search(r'(\d+)\s*(?:hr|hour|h)', duration_text, re.IGNORECASE)
        if hr_match:
            total_minutes += int(hr_match.group(1)) * 60

        # Match minutes
        min_match = re.search(r'(\d+)\s*(?:min|m)(?!\w)', duration_text, re.IGNORECASE)
        if min_match:
            total_minutes += int(min_match.group(1))

        return total_minutes if total_minutes > 0 else None

    def _parse_stops(self, stops_text: str) -> int:
        """Parse stops from text like 'Nonstop', '1 stop', '2 stops'"""
        if not stops_text:
            return 0
        if 'nonstop' in stops_text.lower() or 'direct' in stops_text.lower():
            return 0
        match = re.search(r'(\d+)\s*stop', stops_text.lower())
        if match:
            return int(match.group(1))
        return 0

    def search(self, origin: str, destination: str,
               departure_date: str, return_date: str,
               max_results: int = 10) -> List[Dict]:
        """
        Search for flights on Google Flights

        Args:
            origin: Origin airport code
            destination: Destination airport code
            departure_date: Departure date (YYYY-MM-DD)
            return_date: Return date (YYYY-MM-DD)
            max_results: Maximum number of results to return

        Returns:
            List of flight dictionaries with price, airline, stops, duration, etc.
        """
        if not PLAYWRIGHT_AVAILABLE:
            print("  Playwright not available, skipping Google Flights search")
            return []

        results = []
        url = self.build_search_url(origin, destination, departure_date, return_date)

        try:
            with sync_playwright() as p:
                # Launch browser with stealth settings
                browser = p.chromium.launch(
                    headless=self.headless,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                    ]
                )

                # Incognito-like context - no persistent state
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080},
                    locale='en-GB' if not self.country_code else f'en-{self.country_code}',
                    ignore_https_errors=True,
                    java_script_enabled=True,
                    # No persistent storage - like incognito
                    storage_state=None,
                )

                page = context.new_page()

                # Navigate to Google Flights
                print(f"  Navigating to: {url}")
                page.goto(url, wait_until='networkidle', timeout=45000)

                # Wait for page to stabilize
                page.wait_for_timeout(3000)

                # Handle cookie consent if it appears (multiple languages)
                consent_handled = False
                try:
                    consent_texts = [
                        "Reject all", "Accept all", "I agree",  # English
                        "Alles afwijzen", "Alles accepteren",   # Dutch
                        "Alle ablehnen", "Alle akzeptieren",    # German
                        "Tout refuser", "Tout accepter",        # French
                        "すべて拒否", "すべて同意",               # Japanese
                        "모두 거부", "모두 동의",                # Korean
                        "Отклонить все", "Принять все",         # Russian
                    ]
                    for btn_text in consent_texts:
                        consent_button = page.locator(f'button:has-text("{btn_text}")').first
                        if consent_button.is_visible(timeout=1000):
                            print(f"  Handling cookie consent ({btn_text})...")
                            consent_button.click()
                            page.wait_for_timeout(2000)
                            consent_handled = True
                            break
                except:
                    pass

                # If consent was handled, navigate to search URL again
                if consent_handled:
                    print(f"  Re-navigating after consent...")
                    page.goto(url, wait_until='networkidle', timeout=45000)
                    page.wait_for_timeout(3000)

                # Wait for flight results to load
                print(f"  Waiting for results...")
                page.wait_for_timeout(8000)

                # Parse flights from page text (more reliable than DOM selectors)
                page_text = page.inner_text('body')

                # Debug output
                print(f"  DEBUG: Page text length: {len(page_text)} chars")

                # Save debug file
                try:
                    with open('/app/output/debug_page.txt', 'w') as f:
                        f.write(page_text)
                except:
                    pass

                # Known airlines to look for
                AIRLINES = [
                    'KLM', 'Air India', 'British Airways', 'Emirates', 'Air France',
                    'Gulf Air', 'Qatar Airways', 'Etihad', 'Oman Air', 'Virgin Atlantic',
                    'IndiGo', 'Lufthansa', 'Swiss', 'Turkish Airlines', 'Singapore Airlines',
                    'Cathay Pacific', 'EgyptAir', 'Saudia', 'Ethiopian', 'Air Canada',
                    'United', 'American Airlines', 'Delta', 'Kenya Airways', 'Thai Airways'
                ]

                # Parse flight info from text
                lines = page_text.split('\n')
                current_flight = {}

                for i, line in enumerate(lines):
                    line = line.strip()

                    # Check for time pattern (e.g., "6:30 AM" or "11:00 PM+1")
                    time_match = re.match(r'^(\d{1,2}:\d{2}\s*[AP]M)(\+\d)?$', line, re.IGNORECASE)
                    if time_match:
                        if 'dep_time' not in current_flight:
                            current_flight['dep_time'] = time_match.group(1)
                        elif 'arr_time' not in current_flight:
                            current_flight['arr_time'] = time_match.group(1)
                            if time_match.group(2):
                                current_flight['arr_time'] += time_match.group(2)
                        continue

                    # Check for airline
                    for airline_name in AIRLINES:
                        if airline_name.lower() in line.lower() and len(line) < 80:
                            current_flight['airline'] = airline_name
                            break

                    # Check for duration
                    dur_match = re.match(r'^(\d+\s*hr(?:\s*\d+\s*min)?)$', line, re.IGNORECASE)
                    if dur_match:
                        current_flight['duration'] = dur_match.group(1)

                    # Check for route (e.g., "LHR–BOM")
                    route_match = re.match(r'^([A-Z]{3})[–-]([A-Z]{3})$', line)
                    if route_match:
                        current_flight['route'] = f"{route_match.group(1)}-{route_match.group(2)}"

                    # Check for stops
                    if line.lower() in ['nonstop', '1 stop', '2 stops', '3 stops']:
                        current_flight['stops'] = line

                    # Check for price - this marks end of a flight entry
                    price_match = re.match(r'^([£$€])([\d,]+)$', line)
                    if price_match and current_flight.get('duration'):
                        price_str = price_match.group(1) + price_match.group(2)
                        price_val, currency = self._parse_price(price_str)

                        # Filter: only direct or 1 stop
                        num_stops = self._parse_stops(current_flight.get('stops', '')) if current_flight.get('stops') else 0
                        if num_stops > 1:
                            current_flight = {}
                            continue

                        if price_val and price_val < 10000:
                            price_gbp = self._convert_to_gbp(price_val, currency)

                            # Build flight times string
                            times_str = ""
                            if current_flight.get('dep_time') and current_flight.get('arr_time'):
                                times_str = f"{current_flight['dep_time']} → {current_flight['arr_time']}"

                            result = {
                                'departure_date': departure_date,
                                'return_date': return_date,
                                'airline': current_flight.get('airline', 'Unknown'),
                                'price_gbp': price_gbp,
                                'price_currency': currency,
                                'price_original': price_val,
                                'stops': num_stops,
                                'duration_minutes': self._parse_duration(current_flight.get('duration', '')),
                                'booking_site': 'Google Flights',
                                'booking_url': url,
                                'metadata': {
                                    'dep_time': current_flight.get('dep_time', ''),
                                    'arr_time': current_flight.get('arr_time', ''),
                                    'route': current_flight.get('route', ''),
                                    'times': times_str
                                }
                            }
                            results.append(result)
                            airline_name = current_flight.get('airline', 'Unknown')
                            print(f"    {airline_name} - {price_str} (£{price_gbp:.0f}) {times_str} ({num_stops} stops)")

                        # Reset for next flight
                        current_flight = {}

                        if len(results) >= max_results:
                            break

                browser.close()

        except PlaywrightTimeout as e:
            print(f"  Google Flights timeout: {e}")
        except Exception as e:
            print(f"  Google Flights error: {e}")

        print(f"  Found {len(results)} results from Google Flights")
        return results


def search_google_flights(origin: str, destination: str,
                         departure_date: str, return_date: str,
                         headless: bool = True,
                         country_code: str = None) -> List[Dict]:
    """
    Convenience function to search Google Flights

    Args:
        origin: Origin airport code
        destination: Destination airport code
        departure_date: Departure date (YYYY-MM-DD)
        return_date: Return date (YYYY-MM-DD)
        headless: Run browser in headless mode
        country_code: Country code for localized Google domain (US, GB, NL, JP, etc.)

    Returns:
        List of flight dictionaries
    """
    scraper = GoogleFlightsScraper(headless=headless, country_code=country_code)
    return scraper.search(origin, destination, departure_date, return_date)


if __name__ == "__main__":
    print("\n" + "="*60)
    print("Google Flights Scraper - Test")
    print("="*60 + "\n")

    if not PLAYWRIGHT_AVAILABLE:
        print("Please install playwright first:")
        print("  pip install playwright")
        print("  playwright install chromium")
        exit(1)

    # Test search
    results = search_google_flights(
        origin='LON',
        destination='BOM',
        departure_date='2026-02-16',
        return_date='2026-02-23',
        headless=True
    )

    print(f"\nResults: {len(results)} flights found")
    for r in results[:5]:
        print(f"  {r['airline']} - £{r['price_gbp']} ({r['stops']} stops)")
