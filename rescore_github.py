#!/usr/bin/env python3
"""
Quick rescore of all servers using GitHub API.
Score 0-100 based on 8 parameters.
No xAI needed — all data from GitHub.
"""
import json, os, sys, re, time, base64, urllib.request
from datetime import datetime, timezone
from collections import Counter

BASE_DIR = "/root/mcpappdirectory"
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")

def fetch(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MCPAppDirectory/3.0", "Accept": "application/vnd.github.v3+json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                time.sleep(30)
                continue
            return None
        except: return None
    return None

def extract_owner_repo(url):
    m = re.search(r'github\.com[/:]([^/]+)/([^/\#\?\.]+)', url)
    if not m: return None, None
    return m.group(1), m.group(2).rstrip('.git')

def score_server(server):
    url = server.get("url", "")
    owner, repo = extract_owner_repo(url)
    if not owner or not repo:
        server["score"] = 0
        server["grade"] = "F"
        server["score_details"] = {}
        return server
    
    rd = fetch(f"https://api.github.com/repos/{owner}/{repo}")
    if not rd:
        server["score"] = 40
        server["grade"] = "F"
        server["score_details"] = {}
        return server
    
    now = datetime.now(timezone.utc)
    created = datetime.fromisoformat(rd.get("created_at","").replace("Z","+00:00")) if rd.get("created_at") else now
    pushed = datetime.fromisoformat(rd.get("pushed_at","").replace("Z","+00:00")) if rd.get("pushed_at") else now
    days_since_push = (now - pushed).days
    days_since_creation = max((now - created).days, 1)
    
    stars = rd.get("stargazers_count", 0)
    forks = rd.get("forks_count", 0)
    open_issues = rd.get("open_issues_count", 0)
    language = rd.get("language", "") or ""
    description = rd.get("description", "") or ""
    
    months = max(days_since_creation / 30.0, 0.1)
    star_velocity = stars / months
    
    # Check README content
    readme_data = fetch(f"https://api.github.com/repos/{owner}/{repo}/readme")
    has_readme = readme_data is not None
    readme_text = ""
    if readme_data and readme_data.get("content"):
        try: readme_text = base64.b64decode(readme_data["content"]).decode("utf-8", errors="replace")
        except: pass
    
    # Check for tests, auth, security in README
    rl = readme_text.lower() if readme_text else ""
    has_tests = any(k in rl for k in ["test", "spec", "jest", "pytest", "vitest", "mocha"])
    has_auth = any(k in rl for k in ["auth", "api_key", "token", "oauth", "jwt", "secret"])
    has_security = any(k in rl for k in ["security", "vulnerability", "permission", "sandbox", "validation"])
    has_install = any(k in rl for k in ["install", "setup", "usage", "example", "quickstart", "config", "docker", "npx", "pip"])
    
    # Also check package files for test scripts
    if not has_tests:
        for fname in ["package.json", "pyproject.toml", "setup.cfg"]:
            pkg = fetch(f"https://api.github.com/repos/{owner}/{repo}/contents/{fname}")
            if pkg and pkg.get("content"):
                try:
                    c = base64.b64decode(pkg["content"]).decode("utf-8", errors="replace").lower()
                    if any(k in c for k in ["test", "pytest", "jest", "vitest"]):
                        has_tests = True
                        break
                except: pass
    
    # Scores
    # 1. Recency (20pts)
    recency = 20 if days_since_push <= 7 else (18 if days_since_push <= 30 else (15 if days_since_push <= 90 else (10 if days_since_push <= 180 else (5 if days_since_push <= 365 else 2))))
    
    # 2. Documentation (15pts)
    if has_readme and readme_text:
        l = len(readme_text)
        docs = 15 if l > 2000 else (10 if l > 500 else 5)
    else: docs = 0
    
    # 3. Tests (10pts)
    tests = 10 if has_tests else 0
    
    # 4. Auth (10pts)
    auth = 10 if has_auth else 3
    
    # 5. Star Velocity (15pts)
    if star_velocity >= 100: vel = 15
    elif star_velocity >= 50: vel = 13
    elif star_velocity >= 20: vel = 11
    elif star_velocity >= 10: vel = 9
    elif star_velocity >= 5: vel = 7
    elif star_velocity >= 1: vel = 5
    elif stars > 0: vel = 3
    else: vel = 0
    
    # 6. Security (15pts)
    security = 15 if has_security else 8
    if stars > 10 and open_issues > stars * 0.5: security = max(security - 5, 0)
    
    # 7. Install Instructions (15pts)
    install_count = sum(1 for k in ["install","setup","usage","example","config","docker","npx","pip"] if k in rl) if has_readme else 0
    install_docs = 15 if install_count >= 5 else (12 if install_count >= 3 else (8 if install_count >= 1 else 3))
    
    total = recency + docs + tests + auth + vel + security + install_docs
    total = max(0, min(100, total))
    
    if total >= 90: grade = "A"
    elif total >= 70: grade = "B"
    elif total >= 50: grade = "C"
    elif total >= 30: grade = "D"
    else: grade = "F"
    
    server["score"] = total
    server["grade"] = grade
    server["score_details"] = {"recency": recency, "documentation": docs, "tests": tests, "auth": auth, "star_velocity": vel, "security": security, "install_instructions": install_docs}
    server["stars"] = stars
    server["forks"] = forks
    server["open_issues"] = open_issues
    server["language"] = language
    server["last_push"] = rd.get("pushed_at", "")
    server["created"] = rd.get("created_at", "")
    if description: server["description"] = description
    
    return server

def main():
    servers = json.load(open(LISTINGS_FILE))
    print(f"Scoring {len(servers)} servers with GitHub API...")
    
    results = Counter()
    for i, s in enumerate(servers):
        try:
            score_server(s)
            results[s.get("grade","F")] += 1
            if (i+1) % 100 == 0:
                print(f"  {i+1}/{len(servers)} — {dict(results)}")
                json.dump(servers, open(LISTINGS_FILE, "w"), indent=2)
        except Exception as e:
            print(f"  Error on {i}: {e}")
    
    json.dump(servers, open(LISTINGS_FILE, "w"), indent=2)
    
    ab = sum(1 for s in servers if s.get("grade") in ("A","B"))
    avg = sum(s.get("score",0) for s in servers) / max(len(servers),1)
    print(f"\nDone! Grades: {dict(results)}")
    print(f"A+B: {ab} servers, Avg score: {avg:.1f}/100")

if __name__ == "__main__":
    main()
