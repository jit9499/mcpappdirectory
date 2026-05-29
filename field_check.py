#!/usr/bin/env python3
import json
data = json.load(open("/root/mcpappdirectory/listings.json"))
for field in ["pushed_at", "created_at", "last_updated", "score", "stars", "category", "sub_category", "open_issues", "verified", "install", "source"]:
    count = sum(1 for s in data if field in s)
    print(f"{field}: {count}/{len(data)}")
# Check what the frontend expects - look for last_updated in index.html
with open("/root/mcpappdirectory/index.html") as f:
    html = f.read()
for term in ["last_updated", "pushed_at", "relativeDate", "timeago"]:
    idx = html.find(term)
    if idx >= 0:
        print(f"\n'{term}' found in index.html at pos {idx}")
        print(html[max(0,idx-50):idx+80])
