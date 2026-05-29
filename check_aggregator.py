#!/usr/bin/env python3
import json

# Check aggregator output
data = json.load(open("/root/.hermes/scripts/listings.json"))
print("Aggregator output: %d servers" % len(data))
if data:
    print("Sample keys: %s" % list(data[0].keys()))
    print("First 3 names: %s" % [s.get("name","?") for s in data[:3]])

# Check all_servers.json
ad = json.load(open("/root/.hermes/scripts/all_servers.json"))
print("All servers: %s" % ad.get("count","?"))
print("Last updated: %s" % ad.get("last_updated","?"))

# Compare with live
live = json.load(open("/root/mcpappdirectory/listings.json"))
print("Live server count: %d" % len(live))

# Find servers in aggregator but not in live (by name)
live_names = set(s.get("name","").lower() for s in live)
agg_names = set(s.get("name","").lower() for s in data)
new_names = agg_names - live_names
print("New servers in aggregator (by name): %d" % len(new_names))
if new_names:
    for n in sorted(list(new_names))[:20]:
        print("  - %s" % n)
