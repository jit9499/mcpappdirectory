#!/usr/bin/env python3
"""Quick health check for mcpappdirectory.com"""
import json

data = json.load(open("/root/mcpappdirectory/listings.json"))
print(f"Total servers: {len(data)}")

# Grade distribution
grades = {}
for s in data:
    g = s.get("grade", "?")
    grades[g] = grades.get(g, 0) + 1
print(f"Grade distribution: {json.dumps(grades)}")
print(f"Average score: {sum(s.get('score',0) for s in data)/len(data):.1f}")

# Top servers by score
top = sorted([s for s in data if s.get("score",0) > 60], 
             key=lambda s: s.get("stars",0), reverse=True)[:10]
print("\nTop reputation servers (score>60, by stars):")
for s in top:
    print(f"  {s['name'][:35]:35s} stars={s.get('stars',0):>7d} score={s['score']:3d} grade={s['grade']}")

# Field coverage
for f in ['stars', 'language', 'last_updated', 'license', 'forks', 'has_github_stats', 'score_details']:
    count = sum(1 for s in data if f in s and s.get(f))
    missing = sum(1 for s in data if f not in s or not s.get(f))
    print(f"  {f}: {count}/{len(data)} present, {missing} missing/empty")

# Categories
cats = {}
for s in data:
    c = s.get("category", "?")
    cats[c] = cats.get(c, 0) + 1
print("\nCategories:")
for c, n in sorted(cats.items()):
    print(f"  {c}: {n}")

# Total stars
print(f"\nTotal stars: {sum(s.get('stars',0) for s in data):,}")
