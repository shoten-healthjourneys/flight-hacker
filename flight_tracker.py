#!/usr/bin/env python3
"""
Flight Price Tracker - London to Mumbai
Tracks prices across multiple VPN locations and booking sites
"""

import sqlite3
import json
import time
from datetime import datetime
from pathlib import Path

# Database setup
DB_PATH = Path(__file__).parent / "flights.db"

def init_database():
    """Initialize SQLite database for flight tracking"""
    conn = sqlite3.connect(DB_PATH)
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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            vpn_location TEXT,
            status TEXT,
            results_count INTEGER,
            error_message TEXT
        )
    ''')

    conn.commit()
    conn.close()
    print(f"✓ Database initialized at {DB_PATH}")

def add_flight_result(data):
    """Add a flight search result to the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO flights (
            timestamp, departure_date, return_date, airline,
            price_gbp, price_currency, price_original, vpn_location,
            booking_site, stops, duration_minutes, booking_url, search_metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().isoformat(),
        data.get('departure_date'),
        data.get('return_date'),
        data.get('airline'),
        data.get('price_gbp'),
        data.get('price_currency'),
        data.get('price_original'),
        data.get('vpn_location'),
        data.get('booking_site'),
        data.get('stops'),
        data.get('duration_minutes'),
        data.get('booking_url'),
        json.dumps(data.get('metadata', {}))
    ))

    conn.commit()
    conn.close()

def get_latest_prices(limit=50):
    """Get the most recent flight prices"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT departure_date, return_date, airline, price_gbp,
               vpn_location, booking_site, timestamp, booking_url
        FROM flights
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (limit,))

    results = cursor.fetchall()
    conn.close()
    return results

def get_best_deals(min_price=None):
    """Get the best deals grouped by departure date"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    query = '''
        SELECT departure_date, return_date, airline, MIN(price_gbp) as price,
               vpn_location, booking_site, booking_url
        FROM flights
        GROUP BY departure_date, return_date
        ORDER BY price ASC
    '''

    cursor.execute(query)
    results = cursor.fetchall()
    conn.close()
    return results

def get_price_by_vpn():
    """Compare average prices by VPN location"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT vpn_location,
               COUNT(*) as search_count,
               AVG(price_gbp) as avg_price,
               MIN(price_gbp) as min_price
        FROM flights
        WHERE price_gbp IS NOT NULL
        GROUP BY vpn_location
        ORDER BY avg_price ASC
    ''')

    results = cursor.fetchall()
    conn.close()
    return results


def get_flight_by_id(flight_id: int):
    """
    Get a specific flight by its ID

    Args:
        flight_id: The database ID of the flight

    Returns:
        Dict with flight details or None if not found
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, timestamp, departure_date, return_date, airline,
               price_gbp, price_currency, price_original, vpn_location,
               booking_site, stops, duration_minutes, booking_url, search_metadata
        FROM flights
        WHERE id = ?
    ''', (flight_id,))

    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            'id': row[0],
            'timestamp': row[1],
            'departure_date': row[2],
            'return_date': row[3],
            'airline': row[4],
            'price_gbp': row[5],
            'price_currency': row[6],
            'price_original': row[7],
            'vpn_location': row[8],
            'booking_site': row[9],
            'stops': row[10],
            'duration_minutes': row[11],
            'booking_url': row[12],
            'metadata': json.loads(row[13]) if row[13] else {}
        }
    return None

if __name__ == "__main__":
    init_database()
    print("\n📊 Flight Tracker Database Ready")
    print(f"Location: {DB_PATH}")
