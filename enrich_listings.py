#!/usr/bin/env python3
"""
MCP Server Data Enricher v2 — Fast parallel version.
Adds GitHub stars, npm downloads, language, last_updated to listings.json.
Uses concurrent.futures for parallel API calls.
Rate limit: 5,000 req/hr with token = 83/min = ~1.4/sec

Usage:
  export GITHUB_TOKEN=ghp_xxx
  python3 enrich_listings.py              # Full run on all servers
  python3 enrich_listings.py --test 20     # Test first 20
  python3 enrich_listings.py --status      # Show enrichment stats only
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
import re
import concurrent.futures
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")
LOG_FILE = os.path.join(BASE_DIR, "enrich.log")

# Get token from git remote if not in env
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
if not GITHUB_TOKEN:
    try:
        import subprocess
        r = subprocess.run(["git", "remote", "-v"], capture_output=True, text=True, timeout=5, cwd=BASE_DIR)
        m = re.search(r'ghp_[a-zA-Z0-9]+', r.stdout)
        if m:
            GITHUB_TOKEN = m.group(0)
    except:
        pass

def log(msg):
    ts = datetime.now().isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def api_get(url, headers=None, timeout=15):
    h = {"User-Agent": "MCPAppDirectory/2.0", "Accept": "application/vnd.github.v3+json"}
    if headers:
        h.update(headers)
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return json.loads(body), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        if e.code == 403 and "rate limit" in body.lower():
            log(f"   ⚠️ RATE LIMITED")
        return None, e.code
    except Exception as e:
        return None, 0

def enrich_one(server):
    """Enrich a single server entry. Called in parallel."""
    sid = server.get("id", "")
    source = server.get("source", "")
    url = server.get("url", "")
    name = server.get("name", "")
    
    result = {"id": sid, "changed": False}
    
    # Skip if already enriched
    if server.get("has_github_stats") and server.get("has_npm_stats"):
        return result
    
    # GitHub repos
    gh_match = re.search(r'github\.com[/:]([^/]+)/([^/\.]+)', url)
    if gh_match:
        owner, repo_name = gh_match.group(1), gh_match.group(2).rstrip('.git')
        api_url = f"https://api.github.com/repos/{owner}/{repo_name}"
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
        
        data, status = api_get(api_url, headers)
        if data and status == 200:
            changes = {}
            stars = data.get("stargazers_count", 0)
            lang = data.get("language")
            pushed = data.get("pushed_at", "")
            topics = data.get("topics", [])
            
            if stars != server.get("stars", 0):
                changes["stars"] = stars
            if lang and lang != "Unknown" and server.get("language", "Unknown") != lang:
                changes["language"] = lang
            if pushed:
                changes["last_updated"] = pushed
            if topics:
                changes["topics"] = topics
            if data.get("license") and data["license"].get("spdx_id"):
                changes["license"] = data["license"]["spdx_id"]
            if data.get("forks_count"):
                changes["forks"] = data["forks_count"]
            
            changes["has_github_stats"] = True
            server.update(changes)
            result["changed"] = True
            result["msg"] = f"{name}: {stars}⭐ {lang or '?'}"
        else:
            result["msg"] = f"{name}: no GH data"
        return result
    
    # npm packages
    npm_match = re.search(r'npmjs\.com/package/(.+?)$', url)
    if npm_match and not server.get("has_npm_stats"):
        pkg = npm_match.group(1)
        # Get weekly downloads
        dl_url = f"https://api.npmjs.org/downloads/point/last-week/{pkg}"
        dl_data, dl_status = api_get(dl_url, timeout=10)
        downloads = dl_data.get("downloads", 0) if dl_data and dl_status == 200 else 0
        
        changes = {}
        if downloads > 0:
            changes["downloads_weekly"] = downloads
        
        # Also try to get GitHub info from npm metadata
        reg_url = f"https://registry.npmjs.org/{pkg}/latest"
        meta, _ = api_get(reg_url, timeout=10)
        if meta and isinstance(meta, dict):
            repo_url = meta.get("repository", {}).get("url", "") if isinstance(meta.get("repository"), dict) else ""
            if repo_url and "github.com" in repo_url and not server.get("has_github_stats"):
                gh_match2 = re.search(r'github\.com[/:]([^/]+)/([^/\.]+)', repo_url)
                if gh_match2:
                    o, r = gh_match2.group(1), gh_match2.group(2).rstrip('.git')
                    api_url2 = f"https://api.github.com/repos/{o}/{r}"
                    headers2 = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
                    gh_data, gh_status = api_get(api_url2, headers2, timeout=10)
                    if gh_data and gh_status == 200:
                        stars2 = gh_data.get("stargazers_count", 0)
                        lang2 = gh_data.get("language")
                        pushed2 = gh_data.get("pushed_at", "")
                        if stars2 > 0:
                            changes["stars"] = stars2
                        if lang2:
                            changes["language"] = lang2
                        if pushed2:
                            changes["last_updated"] = pushed2
                        changes["has_github_stats"] = True
        
        changes["has_npm_stats"] = True
        server.update(changes)
        result["changed"] = True
        result["msg"] = f"{name}: {downloads:,} dl/wk"
        return result
    
    return result

def show_status(servers):
    total = len(servers)
    has_stars = sum(1 for s in servers if s.get("stars", 0) > 0)
    has_gh = sum(1 for s in servers if s.get("has_github_stats"))
    has_npm = sum(1 for s in servers if s.get("has_npm_stats"))
    has_lang = sum(1 for s in servers if s.get("language", "Unknown") != "Unknown")
    has_dl = sum(1 for s in servers if s.get("downloads_weekly", 0) > 0)
    has_updated = sum(1 for s in servers if s.get("last_updated"))
    gh_src = sum(1 for s in servers if "github.com" in s.get("url", ""))
    npm_src = sum(1 for s in servers if "npmjs" in s.get("url", ""))
    
    total_stars = sum(s.get("stars", 0) for s in servers)
    total_dl = sum(s.get("downloads_weekly", 0) for s in servers)
    
    print(f"\n📊 ENRICHMENT STATUS ({total} servers)")
    print(f"{'─'*50}")
    print(f"  Sources: {gh_src} GitHub, {npm_src} npm, {total-gh_src-npm_src} other")
    print(f"  GitHub stats: {has_gh}/{gh_src} ({has_gh*100//max(gh_src,1)}%)")
    print(f"  npm stats:    {has_npm}/{npm_src} ({has_npm*100//max(npm_src,1)}%)")
    print(f"  Stars:        {has_stars} servers ({total_stars:,} total)")
    print(f"  Downloads:    {has_dl} servers ({total_dl:,} total)")
    print(f"  Language:     {has_lang} servers")
    print(f"  Last updated: {has_updated} servers")

def main():
    if "--status" in sys.argv:
        servers = json.load(open(LISTINGS_FILE))
        show_status(servers)
        return
    
    test_count = None
    for arg in sys.argv:
        if arg.startswith("--test="):
            test_count = int(arg.split("=")[1])
    
    servers = json.load(open(LISTINGS_FILE))
    total = len(servers)
    
    # Filter to servers that need enrichment
    to_enrich = [s for s in servers 
                 if not (s.get("has_github_stats") and s.get("has_npm_stats"))
                 or "--force" in sys.argv]
    
    if test_count:
        to_enrich = to_enrich[:test_count]
    
    log(f"📊 Loaded {total} servers. {len(to_enrich)} need enrichment" + (f" (testing {test_count})" if test_count else ""))
    log(f"🔑 Token: {'YES (5K/hr)' if GITHUB_TOKEN else 'NO (60/hr)'}")
    
    if not to_enrich:
        log("✅ All servers already enriched!")
        show_status(servers)
        return
    
    # Run in parallel (10 workers)
    enriched = 0
    failed = 0
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(enrich_one, s): s for s in to_enrich}
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            try:
                result = future.result()
                if result.get("changed"):
                    enriched += 1
                msg = result.get("msg", "")
                if msg:
                    log(f"  {'✓' if result['changed'] else '-'} {msg}")
            except Exception as e:
                failed += 1
                log(f"  ✗ Error: {e}")
    
    # Save
    json.dump(servers, open(LISTINGS_FILE, "w"), indent=2)
    
    log(f"\n💾 Done! {enriched} enriched, {failed} failed")
    show_status(servers)

if __name__ == "__main__":
    main()
