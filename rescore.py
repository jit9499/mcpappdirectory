#!/usr/bin/env python3
"""Recompute scores using improved formula. Data is already clean."""
import json
from datetime import datetime, timezone

LISTINGS_FILE = "/root/mcpappdirectory/listings.json"

with open(LISTINGS_FILE) as f:
    servers = json.load(f)

def compute_score(s):
    s_ = s.get("stars",0)
    f_ = s.get("forks",0)
    issues = s.get("open_issues",0)
    pushed = s.get("pushed_at","")
    
    if s_ >= 10000: stars_score = 42
    elif s_ >= 5000: stars_score = 38
    elif s_ >= 2000: stars_score = 33
    elif s_ >= 1000: stars_score = 28
    elif s_ >= 500: stars_score = 25
    elif s_ >= 100: stars_score = 20
    elif s_ >= 50: stars_score = 16
    elif s_ >= 20: stars_score = 13
    elif s_ >= 10: stars_score = 10
    elif s_ >= 5: stars_score = 8
    else: stars_score = 6
    
    if f_ >= 500: forks_score = 15
    elif f_ >= 100: forks_score = 12
    elif f_ >= 50: forks_score = 10
    elif f_ >= 20: forks_score = 8
    elif f_ >= 5: forks_score = 5
    elif f_ > 0: forks_score = 3
    else: forks_score = 0
    
    recency = 2
    if pushed:
        try:
            days_since = (datetime.now(timezone.utc) - datetime.fromisoformat(pushed)).days
            if days_since < 7: recency = 25
            elif days_since < 30: recency = 22
            elif days_since < 90: recency = 18
            elif days_since < 180: recency = 14
            elif days_since < 365: recency = 10
            else: recency = 5
        except: recency = 5
    
    issue_penalty = 0
    if s_ > 0 and issues > s_ * 0.3:
        issue_penalty = min(5, (issues / s_) * 3)
    
    bonus = 5 if s.get("license") else 0
    bonus += min(5, len(s.get("topics",[])))
    activity_bonus = 5 if recency >= 10 else 0
    
    total = stars_score + forks_score + recency + bonus + activity_bonus - issue_penalty
    return min(100, max(5, round(total)))

grade_counts = {"A":0,"B":0,"C":0,"D":0,"F":0}

for s in servers:
    s["score"] = compute_score(s)
    sc = s["score"]
    sc = s["score"]
    if sc >= 85: s["grade"] = "A"
    elif sc >= 65: s["grade"] = "B"
    elif sc >= 45: s["grade"] = "C"
    elif sc >= 25: s["grade"] = "D"
    else: s["grade"] = "F"
    grade_counts[s["grade"]] += 1

# Sort by stars
servers.sort(key=lambda x: -x["stars"])

# Recompute category ranks
cats = {}
for s in servers:
    cats.setdefault(s["category"], []).append(s)
for cat, srv in cats.items():
    srv.sort(key=lambda x: -x["stars"])
    for i, s in enumerate(srv, 1):
        s["category_rank"] = i

avg_score = round(sum(s["score"] for s in servers)/len(servers), 1)
print(f"Servers: {len(servers)}")
print(f"Avg score: {avg_score}")
print(f"Grades: A={grade_counts['A']} B={grade_counts['B']} C={grade_counts['C']} D={grade_counts['D']} F={grade_counts['F']}")
print()

top20 = servers[:20]
for i,s in enumerate(top20,1):
    print(f"  {i:2d}. {s['name'][:32]:32s} ⭐{s['stars']:>5d} 🔀{s['forks']:>4d} {s.get('language','')[:12]:12s} grade={s['grade']} score={s['score']:2d} rank_in_cat={s['category_rank']}")

with open(LISTINGS_FILE,"w") as f:
    json.dump(servers, f, indent=2)
print(f"\n✅ Saved {len(servers)} servers")
