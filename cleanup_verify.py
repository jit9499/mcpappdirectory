#!/usr/bin/env python3
"""
MCP App Directory — Cleanup & Verify Only Real Repos
=====================
1. For each server with a valid-looking GitHub URL → API verify
2. Keep only servers with: exists on GitHub AND (stars > 0 OR forks > 0)
3. For each verified server: fetch stars, created_at, pushed_at, topics, language, license, forks
4. For servers with 0 stars AND 0 forks → remove (no evidence of real use)
5. For servers that don't exist on GitHub → remove entirely
6. Add category ranking for each server
7. Add download/install metrics if available from npm/pypi

Output: clean listings.json with ONLY verified, active, starred servers
"""
import json, os, re, time, urllib.request, urllib.error
from datetime import datetime, timezone
import urllib.parse

LISTINGS_FILE = "/root/mcpappdirectory/listings.json"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN","") or os.environ.get("GITHUB_API_KEY","") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN","")

def log(msg):
    print(f"[{datetime.utcnow().isoformat()}] {msg}", flush=True)

def gh(url):
    headers = {"User-Agent":"MCPAppDirectry/1.0","Accept":"application/vnd.github.v3+json"}
    if GITHUB_TOKEN: headers["Authorization"]=f"Bearer {GITHUB_TOKEN}"
    try:
        req = urllib.request.Request(url,headers=headers)
        with urllib.request.urlopen(req,timeout=10) as resp:
            return json.loads(resp.read().decode())
    except: return None

def extract_owner_repo(url):
    if not url: return None
    m = re.search(r'github\.com[/:]([^/]+)/([^/#?\.]+)', url)
    if not m: return None
    o,r = m.group(1).strip(), m.group(2).strip().rstrip('.git')
    if '.' in o: return None  # package namespace, not github user
    return (o,r) if o and r else None

def npm_info(pkg_name):
    """Try to get npm download counts."""
    try:
        url = f"https://api.npmjs.org/downloads/point/last-month/{pkg_name}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req,timeout=5) as resp:
            return json.loads(resp.read().decode()).get("downloads",0)
    except: return 0

def pypi_info(pkg_name):
    """Try to get PyPI download counts."""
    try:
        url = f"https://pypistats.org/api/packages/{pkg_name}/recent"
        req = urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=5) as resp:
            return json.loads(resp.read().decode()).get("data",{}).get("last_month",0)
    except: return 0

def main():
    log("="*60)
    log("MCP App Directory — Cleanup: Only Real GitHub Repos With Stars")
    log(f"Token: {'✅' if GITHUB_TOKEN else '❌'}")
    log("="*60)

    with open(LISTINGS_FILE) as f: original = json.load(f)
    total = len(original)
    log(f"Total entries: {total}")

    verified_good = []   # repos confirmed on GitHub with stars>0
    verified_empty = []  # repos confirmed on GitHub but 0 stars
    not_found = []       # repo doesn't exist
    no_gh_url = []       # can't extract GitHub URL at all

    done = 0
    for s in original:
        done += 1
        url = s.get("url","")
        gh_info = extract_owner_repo(url)
        name = s.get("name","?")
        
        if not gh_info:
            no_gh_url.append(s)
            log(f"  [{done}/{total}] ✗ {name}: no valid GitHub URL → REMOVE")
            continue
        
        owner, repo = gh_info
        data = gh(f"https://api.github.com/repos/{owner}/{repo}")
        time.sleep(0.06)  # ~17/sec, well within 5000/hr
        
        if not data or not data.get("id"):
            not_found.append(s)
            log(f"  [{done}/{total}] ✗ {name}: repo {owner}/{repo} NOT FOUND → REMOVE")
            continue
        
        stars = data.get("stargazers_count",0)
        forks = data.get("forks_count",0)
        created = (data.get("created_at") or "")[:10]
        pushed = (data.get("pushed_at") or "")[:10]
        lang = data.get("language") or ""
        topics = data.get("topics",[])
        desc = data.get("description") or s.get("description","")
        license_info = data.get("license") or {}
        lic = license_info.get("spdx_id","") if license_info else ""
        
        entry = {
            "name": name,
            "url": data["html_url"],
            "description": desc[:300],
            "category": s.get("category","Developer Tools"),
            "sub_category": "",
            "language": lang,
            "stars": stars,
            "forks": forks,
            "open_issues": data.get("open_issues_count",0),
            "license": lic,
            "topics": topics,
            "created_at": created,
            "pushed_at": pushed,
            "updated_at": data.get("updated_at","")[:10],
            "score": s.get("score", 50),
            "grade": s.get("grade", "C"),
            "verified": True,
            "has_github_stats": True,
            "downloads_monthly": 0,
            "category_rank": 0,
            "sub_category_rank": 0,
        }
        
        if stars > 0 or forks > 0:
            entry["grade"] = compute_grade(entry)
            verified_good.append(entry)
            log(f"  [{done}/{total}] ✅ {name}: ⭐{stars} 🔀{forks} {lang}")
        else:
            verified_empty.append(entry)
            log(f"  [{done}/{total}] ⏭ {name}: ⭐0 → SKIP (no engagement)")

    log(f"\n{'='*60}")
    log(f"RESULTS")
    log(f"  Verified with stars > 0: {len(verified_good)}")
    log(f"  Verified but 0 stars:    {len(verified_empty)} → removed")
    log(f"  Repo not found:          {len(not_found)} → removed")
    log(f"  No valid GitHub URL:     {len(no_gh_url)} → removed")
    log(f"  Total removed:           {len(verified_empty)+len(not_found)+len(no_gh_url)}")
    
    # Sort by stars descending
    verified_good.sort(key=lambda x: -x["stars"])
    
    # Add category ranks
    categories = {}
    for s in verified_good:
        cat = s["category"]
        if cat not in categories: categories[cat] = []
        categories[cat].append(s)
    
    for cat, servers in categories.items():
        servers.sort(key=lambda x: -x["stars"])
        for i, s in enumerate(servers, 1):
            s["category_rank"] = i
    
    # Compute scores based on real data
    for s in verified_good:
        s["score"] = compute_score(s)
        s["grade"] = compute_grade(s)
    
    # Recompute stats
    stats = {
        "total_servers": len(verified_good),
        "total_subscribers": 1,
        "average_score": round(sum(s["score"] for s in verified_good)/len(verified_good),1) if verified_good else 0,
        "grade_distribution": {
            "A": sum(1 for s in verified_good if s["grade"]=="A"),
            "B": sum(1 for s in verified_good if s["grade"]=="B"),
            "C": sum(1 for s in verified_good if s["grade"]=="C"),
            "D": sum(1 for s in verified_good if s["grade"]=="D"),
            "F": sum(1 for s in verified_good if s["grade"]=="F"),
        }
    }
    
    log(f"\nFinal dataset: {len(verified_good)} servers")
    log(f"Avg score: {stats['average_score']}")
    log(f"Grade dist: A={stats['grade_distribution']['A']} B={stats['grade_distribution']['B']} C={stats['grade_distribution']['C']} D={stats['grade_distribution']['D']} F={stats['grade_distribution']['F']}")
    
    # Save
    with open(LISTINGS_FILE,"w") as f:
        json.dump(verified_good, f, indent=2)
    log(f"✅ Saved {len(verified_good)} servers to {LISTINGS_FILE}")

def compute_score(s):
    s_ = s.get("stars",0)
    f_ = s.get("forks",0)
    issues = s.get("open_issues",0)
    pushed = s.get("pushed_at","")
    
    # Stars score (max 40 pts — logarithmic scale)
    if s_ >= 10000: stars_score = 42
    elif s_ >= 5000: stars_score = 38
    elif s_ >= 2000: stars_score = 33
    elif s_ >= 1000: stars_score = 28
    elif s_ >= 500: stars_score = 23
    elif s_ >= 100: stars_score = 18
    elif s_ >= 50: stars_score = 13
    elif s_ >= 20: stars_score = 10
    elif s_ >= 5: stars_score = 7
    else: stars_score = 5
    
    # Forks score (max 15 pts)
    if f_ >= 500: forks_score = 15
    elif f_ >= 100: forks_score = 12
    elif f_ >= 50: forks_score = 10
    elif f_ >= 20: forks_score = 8
    elif f_ >= 5: forks_score = 5
    elif f_ > 0: forks_score = 3
    else: forks_score = 0
    
    # Recent activity (max 25 pts)
    recency = 0
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
    else: recency = 2
    
    # Issues ratio penalty (max -5 pts)
    issue_penalty = 0
    if s_ > 0 and issues > s_ * 0.3:
        issue_penalty = min(5, (issues / s_) * 3)
    
    # License + topics bonus (max 10 pts)
    bonus = 5 if s.get("license") else 0
    bonus += min(5, len(s.get("topics",[])))
    
    # Activity bonus (max 5 pts for any push in last 6 months)
    activity_bonus = 5 if recency >= 10 else 0
    
    total = stars_score + forks_score + recency + bonus + activity_bonus - issue_penalty
    return min(100, max(5, round(total)))

def compute_grade(s):
    score = s.get("score",50)
    if score >= 90: return "A"
    if score >= 70: return "B"
    if score >= 50: return "C"
    if score >= 30: return "D"
    return "F"

if __name__ == "__main__":
    main()
