#!/usr/bin/env python3
import json
data = json.load(open("/root/mcpappdirectory/listings.json"))
top = sorted([s for s in data if s.get("stars",0) >= 10000], key=lambda s: s.get("stars",0), reverse=True)
print("=== 10K+ Star MCP Servers in Directory ===")
for s in top[:20]:
    name = s["name"]
    stars = s["stars"]
    score = s.get("score",0)
    grade = s.get("grade","?")
    lang = s.get("language","?")
    desc = s.get("description","")[:80]
    print("%-35s ⭐%7d %3d/%s %-12s %s" % (name, stars, score, grade, lang, desc))
print("\nTotal 10K+ star servers: %d" % len(top))

# Summary stats
total_stars = sum(s.get("stars",0) for s in data)
print("\nTotal stars across all servers: %d" % total_stars)
print("Servers: %d" % len(data))
print("Avg stars: %d" % (total_stars / len(data)))
