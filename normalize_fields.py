#!/usr/bin/env python3
"""Normalize pushed_at -> last_updated and created_at -> created in listings.json"""
import json

data = json.load(open("/root/mcpappdirectory/listings.json"))

count = 0
for s in data:
    if "pushed_at" in s and s["pushed_at"] and "last_updated" not in s:
        s["last_updated"] = s["pushed_at"]
        count += 1
print(f"Added last_updated from pushed_at for %d servers" % count)

count2 = 0
for s in data:
    if "created_at" in s and s["created_at"] and "created" not in s:
        s["created"] = s["created_at"]
        count2 += 1
print(f"Added created from created_at for %d servers" % count2)

json.dump(data, open("listings.json", "w"), indent=2)
print("Saved listings.json")

# Verify
verify = json.load(open("listings.json"))
has_lu = sum(1 for s in verify if "last_updated" in s)
has_c = sum(1 for s in verify if "created" in s)
print("has last_updated: %d/%d" % (has_lu, len(verify)))
print("has created: %d/%d" % (has_c, len(verify)))
