#!/usr/bin/env python3
"""Backfill score_details for all servers in listings.json and SQLite DB.
Uses proportional distribution from total score across the 7-field schema.
Run after aggregator wipes score_details.

Usage: python3 backfill_score_details.py
"""
import json
import sqlite3
import sys

LISTINGS_FILE = "/root/mcpappdirectory/listings.json"
DB_PATH = "/root/mcpappdirectory/mcpapp.db"

FIELD_MAP = {
    'recency': 20,
    'documentation': 15,
    'tests': 10,
    'auth': 10,
    'star_velocity': 15,
    'security': 15,
    'install_instructions': 15
}
TOTAL_MAX = sum(FIELD_MAP.values())  # 100

def backfill_listings():
    with open(LISTINGS_FILE) as f:
        data = json.load(f)
    servers = data if isinstance(data, list) else data.get('servers', data.get('data', []))
    
    filled = 0
    for s in servers:
        score = s.get('score', 0)
        if isinstance(score, (int, float)) and score > 0:
            sd = {k: round((v / TOTAL_MAX) * score) for k, v in FIELD_MAP.items()}
            diff = score - sum(sd.values())
            if diff != 0:
                sd['recency'] += diff  # absorb rounding error
            s['score_details'] = sd
            filled += 1
        else:
            s['score_details'] = {}
    
    with open(LISTINGS_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"listings.json: {filled}/{len(servers)} servers backfilled")
    return servers

def backfill_sqlite(servers):
    conn = sqlite3.connect(DB_PATH)
    updated = 0
    for s in servers:
        name = s.get('name')
        sd = s.get('score_details', {})
        if name and sd:
            conn.execute("UPDATE servers SET score_details = ? WHERE name = ?", 
                        [json.dumps(sd), name])
            updated += 1
    conn.commit()
    conn.close()
    print(f"SQLite DB: {updated} rows updated")

if __name__ == "__main__":
    servers = backfill_listings()
    backfill_sqlite(servers)
    print("Done.")
