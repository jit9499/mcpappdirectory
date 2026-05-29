#!/usr/bin/env python3
"""
MCP App Directory — URL Validation (Final)
Do NOT try to search/replace broken URLs (causes false matches).
Instead:
- Verify valid-looking GitHub URLs against the GitHub API
- For confirmed repos → mark verified
- For non-existent repos → strip the URL, mark unverified
- This produces clean data: verified repos with real links, and placeholder entries without links

No search-based guessing. A clean "unverified" is better than a wrong link.
"""
import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime
import urllib.parse

LISTINGS_FILE = "/root/mcpappdirectory/listings.json"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GITHUB_API_KEY", "") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")

def log(msg):
    print(f"[{datetime.utcnow().isoformat()}] {msg}", flush=True)

def gh(url):
    """Call GitHub API."""
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
        if e.code in (404, 422):
            return None
        return None
    except Exception:
        return None

def extract_gh(url):
    """Extract (owner, repo) from GitHub URL or return None."""
    if not url:
        return None
    m = re.search(r'github\.com[/:]([^/]+)/([^/#?\.]+)', url)
    if not m:
        return None
    owner = m.group(1).strip()
    repo = m.group(2).strip().rstrip('.git')
    return (owner, repo) if owner and repo else None

def valid_owner(owner):
    """GitHub users/orgs cannot have dots in their names."""
    return '.' not in owner

def main():
    log("=" * 60)
    log("MCP App Directory — URL Validation (Final Pass)")
    log(f"Token: {'✅' if GITHUB_TOKEN else '❌'}")
    log("=" * 60)
    
    with open(LISTINGS_FILE) as f:
        servers = json.load(f)
    
    total = len(servers)
    log(f"Total: {total}")
    
    # Categorize
    has_valid_owner = 0
    has_invalid_owner = 0
    not_gh_url = 0
    no_url = 0
    
    for s in servers:
        url = s.get("url", "")
        gh_info = extract_gh(url)
        if not gh_info:
            if url:
                not_gh_url += 1
            else:
                no_url += 1
        elif not valid_owner(gh_info[0]):
            has_invalid_owner += 1
        else:
            has_valid_owner += 1
    
    log(f"\nBreakdown:")
    log(f"  Valid-looking GitHub URL: {has_valid_owner} → API check")
    log(f"  Invalid owner (dots):    {has_invalid_owner} → strip link, mark unverified")
    log(f"  Non-GitHub URL:          {not_gh_url} → strip link, mark unverified")
    log(f"  No URL:                  {no_url} → mark unverified")
    
    # Phase 1: API-verify entries with valid-looking owners
    log(f"\n{'='*60}")
    log(f"Phase 1: API verification ({has_valid_owner} entries)")
    log(f"{'='*60}")
    
    api_ok = 0
    api_fail = 0
    api_skip = 0
    
    for i, s in enumerate(servers):
        url = s.get("url", "")
        gh_info = extract_gh(url)
        
        if not gh_info or not valid_owner(gh_info[0]):
            continue  # handled in phase 2
        
        owner, repo = gh_info
        
        # Already previously verified — skip API call
        if s.get("verified") and s.get("has_github_stats"):
            api_skip += 1
            continue
        
        # Call GitHub API
        data = gh(f"https://api.github.com/repos/{owner}/{repo}")
        time.sleep(0.05)  # 20/sec = ~50s for ~1000 entries
        
        if data and data.get("id"):
            s["url"] = data["html_url"]
            s["stars"] = data.get("stargazers_count", 0)
            s["language"] = data.get("language", "") or s.get("language", "")
            s["forks"] = data.get("forks_count", 0)
            s["description"] = data.get("description", "") or s.get("description", "")
            s["updated_at"] = data.get("updated_at", "")
            s["has_github_stats"] = True
            s["verified"] = True
            s["verification_note"] = "API-verified"
            api_ok += 1
        else:
            # Repo doesn't exist — strip link
            s["url_display"] = url
            s["url"] = ""
            s["has_github_stats"] = False
            s["verified"] = False
            s["verification_note"] = "Repo not found on GitHub"
            api_fail += 1
        
        if (api_ok + api_fail) % 100 == 0:
            log(f"  Progress: {api_ok + api_fail}/{has_valid_owner} (✅ {api_ok} ❌ {api_fail} ⏭ {api_skip})")
    
    log(f"  API done: {api_ok} OK, {api_fail} failed, {api_skip} skipped")
    
    # Phase 2: Mark invalid entries
    log(f"\n{'='*60}")
    log(f"Phase 2: Mark unverified entries ({has_invalid_owner + not_gh_url + no_url})")
    log(f"{'='*60}")
    
    stripped = 0
    for s in servers:
        if s.get("verified"):
            continue
        url = s.get("url", "")
        gh_info = extract_gh(url)
        
        if not gh_info or not valid_owner(gh_info[0]):
            # Strip broken link
            if url:
                s["url_display"] = url
            s["url"] = ""
            s["has_github_stats"] = False
            s["verified"] = False
            if not s.get("verification_note"):
                if not gh_info:
                    s["verification_note"] = "Not a GitHub repository"
                else:
                    s["verification_note"] = "Invalid GitHub owner"
            stripped += 1
    
    log(f"  Stripped broken links: {stripped}")
    
    # Final results
    true_verified = sum(1 for s in servers if s.get("verified") and s.get("has_github_stats"))
    pending = sum(1 for s in servers if not s.get("verified"))
    with_live_link = sum(1 for s in servers if s.get("url") and s.get("has_github_stats"))
    
    log(f"\n{'='*60}")
    log(f"FINAL RESULTS")
    log(f"  ✅ Verified (live GitHub repos): {true_verified}/{total}")
    log(f"  ⏳ Pending verification:        {pending}/{total}")
    log(f"  🔗 With live links:              {with_live_link}/{total}")
    log(f"  📊 Coverage:                     {round(with_live_link/total*100,1)}% verified")
    log(f"{'='*60}")
    
    # Save
    with open(LISTINGS_FILE, "w") as f:
        json.dump(servers, f, indent=2)
    log(f"Saved: {LISTINGS_FILE}")

if __name__ == "__main__":
    main()
