#!/usr/bin/env python3
"""
Skyscanner Scraper
"""

import re
from datetime import datetime
from typing import List, Dict, Optional

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class SkyscannerScraper:
    """Scrapes flight prices from Skyscanner"""

    def __init__(self, headless: bool = True):
        self.headless = headless

    def _parse_price(self, price_text: str) -> tuple:
        """Extract price and currency"""
        if not price_text:
            return None, None
        currency = 'GBP'
        if '$' in price_text:
            currency = 'USD'
        elif '€' in price_text:
            currency = 'EUR'
        match = re.search(r'[\d,]+', price_text.replace(',', ''))
        if match:
            try:
                return float(match.group()), currency
            except:
                pass
        return None, None

    def _convert_to_gbp(self, price: float, currency: str) -> float:
        rates = {'GBP': 1.0, 'USD': 0.79, 'EUR': 0.86}
        return round(price * rates.get(currency, 1.0), 2)

    def _parse_duration(self, text: str) -> Optional[int]:
        if not text:
            return None
        total = 0
        hr = re.search(r'(\d+)\s*h', text, re.I)
        mn = re.search(r'(\d+)\s*m', text, re.I)
        if hr:
            total += int(hr.group(1)) * 60
        if mn:
            total += int(mn.group(1))
        return total if total > 0 else None

    def search(self, origin: str, destination: str,
               departure_date: str, return_date: str,
               max_results: int = 10) -> List[Dict]:
        """Search Skyscanner for flights"""
        if not PLAYWRIGHT_AVAILABLE:
            return []

        results = []
        # Format: /transport/flights/lhr/bom/260216/260223/
        dep_fmt = datetime.strptime(departure_date, '%Y-%m-%d').strftime('%y%m%d')
        ret_fmt = datetime.strptime(return_date, '%Y-%m-%d').strftime('%y%m%d')
        url = f"https://www.skyscanner.net/transport/flights/{origin.lower()}/{destination.lower()}/{dep_fmt}/{ret_fmt}/"

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                    viewport={'width': 1920, 'height': 1080},
                )
                page = context.new_page()

                print(f"  [Skyscanner] Navigating...")
                page.goto(url, timeout=45000)
                page.wait_for_timeout(8000)

                # Handle cookie consent
                try:
                    accept_btn = page.locator('button:has-text("Accept all")').first
                    if accept_btn.is_visible(timeout=3000):
                        accept_btn.click()
                        page.wait_for_timeout(2000)
                except:
                    pass

                page.wait_for_timeout(5000)
                text = page.inner_text('body')

                # Parse flights - Skyscanner format varies
                # Look for price patterns and nearby airline/duration info
                AIRLINES = ['Air India', 'British Airways', 'Emirates', 'KLM', 'Air France',
                           'Lufthansa', 'Virgin Atlantic', 'IndiGo', 'Qatar Airways', 'Etihad']

                lines = text.split('\n')
                current = {}

                for line in lines:
                    line = line.strip()

                    # Check airline
                    for airline in AIRLINES:
                        if airline.lower() in line.lower() and len(line) < 60:
                            current['airline'] = airline
                            break

                    # Duration pattern
                    dur_match = re.match(r'^(\d+h\s*\d*m?)$', line, re.I)
                    if dur_match:
                        current['duration'] = dur_match.group(1)

                    # Stops
                    if 'direct' in line.lower():
                        current['stops'] = 0
                    elif '1 stop' in line.lower():
                        current['stops'] = 1

                    # Time pattern
                    time_match = re.match(r'^(\d{2}:\d{2})\s*[–-]\s*(\d{2}:\d{2})(?:\+\d)?$', line)
                    if time_match:
                        current['times'] = f"{time_match.group(1)} → {time_match.group(2)}"

                    # Price - marks end of flight entry
                    price_match = re.match(r'^[£$€]([\d,]+)$', line)
                    if price_match and current.get('airline'):
                        price_val, currency = self._parse_price(line)
                        if price_val and price_val < 10000:
                            price_gbp = self._convert_to_gbp(price_val, currency)
                            stops = current.get('stops', 1)
                            if stops <= 1:  # Only direct or 1 stop
                                results.append({
                                    'departure_date': departure_date,
                                    'return_date': return_date,
                                    'airline': current.get('airline', 'Unknown'),
                                    'price_gbp': price_gbp,
                                    'price_currency': currency,
                                    'price_original': price_val,
                                    'stops': stops,
                                    'duration_minutes': self._parse_duration(current.get('duration', '')),
                                    'booking_site': 'Skyscanner',
                                    'booking_url': url,
                                    'metadata': {'times': current.get('times', '')}
                                })
                                print(f"    [Skyscanner] {current.get('airline')} - £{price_gbp:.0f}")
                        current = {}

                        if len(results) >= max_results:
                            break

                browser.close()

        except Exception as e:
            print(f"  [Skyscanner] Error: {e}")

        print(f"  [Skyscanner] Found {len(results)} results")
        return results


def search_skyscanner(origin: str, destination: str,
                      departure_date: str, return_date: str,
                      headless: bool = True) -> List[Dict]:
    scraper = SkyscannerScraper(headless=headless)
    return scraper.search(origin, destination, departure_date, return_date)
