#!/usr/bin/env python3
"""
MCP App Directory — URL Validation
Validates GitHub URLs efficiently:
1. Obvious-fake owners (contain dots) → search only
2. Valid-looking owners → API check
3. Already verified entries → skip
4. Nothing found → strip broken link

Runs in ~3-5 minutes with a GitHub token.
"""
import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime

BASE_DIR = "/root/mcpappdirectory"
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GITHUB_API_KEY", "") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")

log_lines = []
def log(msg):
    ts = datetime.utcnow().isoformat()
    line = f"[{ts}] {msg}"
    log_lines.append(line)
    print(line)

def extract_gh(url):
    """Extract (owner, repo) from any GitHub URL."""
    if not url:
        return None
    m = re.search(r'github\.com[/:]([^/]+)/([^/#?\.]+)', url)
    if not m:
        return None
    owner = m.group(1).strip()
    repo = m.group(2).strip().rstrip('.git')
    return (owner, repo) if owner and repo else None

def has_dot_in_owner(url):
    """Quick check: does the owner part contain a dot (definitely fake for GitHub)"""
    gh = extract_gh(url)
    return gh and '.' in gh[0]

def gh_request(url):
    """Low-level GitHub API call."""
    headers = {
        "User-Agent": "MCPAppDirectory/1.0",
        "Accept": "application/vnd.github.v3+json"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        return None
    except Exception:
        return None

def check_repo(owner, repo):
    """Check if a GitHub repo exists and return its data."""
    return gh_request(f"https://api.github.com/repos/{owner}/{repo}")

def search_repo(name, retry_broad=False):
    """Search GitHub for a repo matching name."""
    terms = name.strip()
    # First try with mcp-server topic
    q = urllib.parse.quote(f"{terms} topic:mcp-server")
    data = gh_request(
        f"https://api.github.com/search/repositories?q={q}&per_page=3&sort=stars"
    )
    if data and data.get("items"):
        best = max(data["items"], key=lambda x: x.get("stargazers_count", 0))
        bn = best.get("name", "").lower()
        sn = terms.lower()
        if sn in bn or bn in sn or best.get("stargazers_count", 0) > 5:
            return best
    
    if retry_broad:
        q = urllib.parse.quote(terms)
        data = gh_request(
            f"https://api.github.com/search/repositories?q={q}&per_page=3&sort=stars"
        )
        if data and data.get("items"):
            return data["items"][0]
    return None

def update_from_data(s, data):
    """Update server entry from GitHub API data."""
    s["url"] = data["html_url"]
    s["stars"] = data.get("stargazers_count", 0)
    s["language"] = data.get("language") or s.get("language", "")
    s["forks"] = data.get("forks_count", 0)
    s["description"] = data.get("description") or s.get("description", "")
    s["updated_at"] = data.get("updated_at", "")
    s["has_github_stats"] = True
    s["verified"] = True

def main():
    log("=" * 60)
    log("MCP App Directory — URL Validation")
    log(f"Token: {'✅' if GITHUB_TOKEN else '❌'} {'5000/hr' if GITHUB_TOKEN else '60/hr limit'}")
    log("=" * 60)
    
    with open(LISTINGS_FILE) as f:
        servers = json.load(f)
    
    total = len(servers)
    # Phase 0: Mark pre-verified entries
    pre_verified = 0
    for s in servers:
        if s.get("has_github_stats") and not s.get("verified"):
            s["verified"] = True
            s["verification_note"] = "Pre-verified"
            pre_verified += 1
    
    already_ok = sum(1 for s in servers if s.get("verified"))
    need_check = [s for s in servers if not s.get("verified")]
    
    log(f"Total: {total} | Already OK: {already_ok} | Need check: {len(need_check)}")
    
    # Phase 1: Batch classify
    api_calls_needed = 0
    searches_needed = 0
    dot_owners = 0
    already_have_stats = 0
    
    for s in need_check:
        url = s.get("url", "")
        if has_dot_in_owner(url):
            dot_owners += 1
        elif s.get("has_github_stats"):
            already_have_stats += 1
            gh = extract_gh(url)
            if gh:
                api_calls_needed += 1  # need to verify
            else:
                searches_needed += 1
        else:
            gh = extract_gh(url)
            if gh and '.' not in gh[0]:
                api_calls_needed += 1
            else:
                searches_needed += 1
    
    log(f"\n📊 Breakdown of {len(need_check)} unchecked:")
    log(f"  Dot-in-owner (fake): {dot_owners} → search needed")
    log(f"  Has stats, unverified: {already_have_stats} → quick API check")
    log(f"  API calls needed: {api_calls_needed}")
    log(f"  Searches needed: {searches_needed}")
    
    # Phase 2: API verify repos with valid-looking owners
    log(f"\n{'='*60}")
    log(f"PHASE 1: API verification ({api_calls_needed} repos)")
    log(f"{'='*60}")
    
    api_verified = 0
    api_failed = 0
    rate_limited = False
    
    for i, s in enumerate(servers):
        if rate_limited:
            break
        if s.get("verified"):
            continue
        
        url = s.get("url", "")
        gh = extract_gh(url)
        if not gh or '.' in gh[0]:
            continue  # handled in phase 2
        
        owner, repo = gh
        
        data = check_repo(owner, repo)
        time.sleep(0.05)  # 20/sec
        
        if data and data.get("id"):
            update_from_data(s, data)
            s["verification_note"] = "API-verified"
            api_verified += 1
            if api_verified % 100 == 0:
                log(f"  ✅ {api_verified} verified...")
        else:
            # Repo doesn't exist — try search
            log(f"  🔍 Repo {owner}/{repo} not found — searching for '{s['name']}'...")
            found = search_repo(s["name"], retry_broad=True)
            if found:
                update_from_data(s, found)
                s["verification_note"] = "Verified-search"
                api_verified += 1
                log(f"    ✅ Found: {found['html_url']}")
            else:
                s["has_github_stats"] = False
                s["verified"] = False
                s["verification_note"] = "No GitHub repo found"
                s["url_display"] = url
                s["url"] = ""
                api_failed += 1
                log(f"    ❌ No match found")
    
    log(f"\nAPI results: {api_verified} verified, {api_failed} failed")
    
    # Phase 3: Search for servers with fake owners
    log(f"\n{'='*60}")
    log(f"PHASE 2: Search for entries with fake owners ({dot_owners} servers)")
    log(f"{'='*60}")
    
    search_found = 0
    search_failed = 0
    
    for s in servers:
        if s.get("verified"):
            continue
        url = s.get("url", "")
        if not has_dot_in_owner(url):
            continue
        
        log(f"  🔍 Searching for '{s['name']}' (fake owner: {extract_gh(url)[0] if extract_gh(url) else '?'})...")
        
        # Show what the fake URL was
        fake_owner = extract_gh(url)
        if fake_owner:
            log(f"     Old: {url}")
        
        found = search_repo(s["name"], retry_broad=True)
        if found:
            update_from_data(s, found)
            s["verification_note"] = "Search-replaced"
            search_found += 1
            log(f"    ✅ Found: {found['html_url']} (⭐{found.get('stargazers_count', 0)})")
        else:
            s["has_github_stats"] = False
            s["verified"] = False
            s["verification_note"] = "No valid GitHub repo"
            s["url_display"] = url  # keep original for reference
            s["url"] = ""  # remove broken link so UI doesn't link to 404
            search_failed += 1
            log(f"    ❌ No match found")
    
    log(f"\nSearch results: {search_found} found, {search_failed} not found")
    
    # Final stats
    final_ok = sum(1 for s in servers if s.get("has_github_stats"))
    final_verified = sum(1 for s in servers if s.get("verified"))
    final_broken = sum(1 for s in servers if not s.get("has_github_stats"))
    
    log(f"\n{'='*60}")
    log("FINAL RESULTS")
    log(f"  With GitHub stats: {final_ok}/{total} ({round(final_ok/total*100,1)}%)")
    log(f"  Verified badge:    {final_verified}/{total} ({round(final_verified/total*100,1)}%)")
    log(f"  No repo (stripped links): {final_broken}")
    
    # Save
    with open(LISTINGS_FILE, "w") as f:
        json.dump(servers, f, indent=2)
    log(f"\n✅ Saved to {LISTINGS_FILE}")

if __name__ == "__main__":
    main()
