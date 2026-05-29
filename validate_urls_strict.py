#!/usr/bin/env python3
"""
MCP App Directory — URL Validation (Quality-First)
Strict approach: only accept GitHub API confirmed repos or very strong search matches.
Weak/no matches → strip the broken link, mark as unverified.

Run: python3 validate_urls_strict.py
"""
import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.parse

BASE_DIR = "/root/mcpappdirectory"
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")
BACKUP_FILE = os.path.join(BASE_DIR, f"listings.backup.{int(time.time())}.json")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GITHUB_API_KEY", "") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")

def log(msg):
    print(f"[{datetime.utcnow().isoformat()}] {msg}")

def gh(url):
    """GitHub API call."""
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
        if e.code == 403:
            return None
        return None
    except Exception:
        return None

def extract_gh(url):
    """Extract (owner, repo) from GitHub URL."""
    if not url:
        return None
    m = re.search(r'github\.com[/:]([^/]+)/([^/#?\.]+)', url)
    if not m:
        return None
    owner = m.group(1).strip()
    repo = m.group(2).strip().rstrip('.git')
    return (owner, repo) if owner and repo else None

def has_dot_owner(url):
    gh_info = extract_gh(url)
    return gh_info and '.' in gh_info[0]

def check_repo(owner, repo):
    """Check if repo exists on GitHub."""
    return gh(f"https://api.github.com/repos/{owner}/{repo}")

def search_mcp(name):
    """Search GitHub with strict MCP focus. Returns repo data or None."""
    terms = name.strip().lower()
    if not terms:
        return None
    
    # Try with MCP keyword in query
    for query_text in [f"{terms} mcp-server", f"{terms} mcp server", terms]:
        q = urllib.parse.quote(query_text)
        data = gh(f"https://api.github.com/search/repositories?q={q}&per_page=5&sort=stars")
        if not data or not data.get("items"):
            continue
        
        items = data["items"]
        # Score each result by name similarity
        scored = []
        for item in items:
            item_name = item.get("name", "").lower()
            item_desc = (item.get("description") or "").lower()
            topics = [t.lower() for t in item.get("topics", [])]
            
            score = 0
            # Exact name match = 10
            if item_name == terms:
                score += 10
            elif terms in item_name or item_name in terms:
                score += 6
            # Partial match = 3
            elif any(word in item_name for word in terms.replace("-", " ").split()):
                score += 3
            
            # Bonus for MCP keyword
            if "mcp" in item_name or "mcp" in topics:
                score += 4
            if "mcp-server" in topics:
                score += 3
            
            # Bonus for stars (popular = more trustworthy match)
            stars = item.get("stargazers_count", 0)
            if stars > 50:
                score += 2
            elif stars > 10:
                score += 1
            
            # Penalize if name is too generic
            if item_name in ("n8n", "awesome-list", "mcp", "mcp-server"):
                score -= 5
            
            scored.append((score, item))
        
        scored.sort(key=lambda x: -x[0])
        best_score, best = scored[0]
        
        if best_score >= 6:
            return best
        # Score 4-5: borderline, only accept if it's clearly MCP-related
        if best_score >= 4:
            topics = [t.lower() for t in best.get("topics", [])]
            if "mcp-server" in topics or "mcp" in topics:
                return best
    
    return None

def main():
    log("=" * 60)
    log("MCP App Directory — Strict URL Validation")
    log(f"Token: {'✅' if GITHUB_TOKEN else '❌'}")
    log("=" * 60)
    
    # Backup
    with open(LISTINGS_FILE) as f:
        servers = json.load(f)
    with open(BACKUP_FILE, "w") as f:
        json.dump(servers, f)
    log(f"Backup: {BACKUP_FILE}")
    
    total = len(servers)
    
    # Phase 0: Mark pre-verified entries (has_github_stats was already true)
    pre_verified = 0
    for s in servers:
        if s.get("has_github_stats") and not s.get("verified"):
            # Double-check these still exist
            pass  # Will verify in phase 1
            pre_verified += 1
    log(f"Total: {total} | Pre-verified: {pre_verified}")
    
    # Stats
    verified_ok = 0
    api_fixed = 0
    search_fixed = 0
    not_found = 0
    skipped = 0
    
    # Phase 1: Check all entries with valid-looking owners
    log(f"\n{'='*60}")
    log(f"Phase 1: API verification & search")
    log(f"{'='*60}")
    
    i = 0
    for s in servers:
        i += 1
        url = s.get("url", "")
        
        # Already verified
        if s.get("verified") and s.get("has_github_stats"):
            verified_ok += 1
            continue
        
        # No URL at all — can't verify
        if not url:
            s["verified"] = False
            s["has_github_stats"] = False
            s["verification_note"] = "No URL provided"
            not_found += 1
            continue
        
        # Check if URL has a valid GitHub owner
        gh_info = extract_gh(url)
        
        if gh_info and not has_dot_owner(url):
            owner, repo = gh_info
            data = check_repo(owner, repo)
            time.sleep(0.05)
            
            if data and data.get("id"):
                # Repo exists!
                s["url"] = data["html_url"]
                s["stars"] = data.get("stargazers_count", 0)
                s["language"] = data.get("language", "") or s.get("language", "")
                s["forks"] = data.get("forks_count", 0)
                s["description"] = data.get("description", "") or s.get("description", "")
                s["updated_at"] = data.get("updated_at", "")
                s["has_github_stats"] = True
                s["verified"] = True
                s["verification_note"] = "API-verified"
                if not s.get("score_details"):
                    s["score_details"] = {}
                api_fixed += 1
                if api_fixed % 100 == 0:
                    log(f"  ✅ {api_fixed} API-verified...")
                continue
            
            # Repo doesn't exist — search
            log(f"  🔍 [{s['name']}] Repo {owner}/{repo} not found — searching...")
            found = search_mcp(s["name"])
            time.sleep(0.1)
            
            if found:
                s["url"] = found["html_url"]
                s["stars"] = found.get("stargazers_count", 0)
                s["language"] = found.get("language", "") or s.get("language", "")
                s["forks"] = found.get("forks_count", 0)
                s["description"] = found.get("description", "") or s.get("description", "")
                s["updated_at"] = found.get("updated_at", "")
                s["has_github_stats"] = True
                s["verified"] = True
                s["verification_note"] = "Search-found"
                search_fixed += 1
                log(f"    ✅ -> {found['html_url']}")
                continue
        else:
            # Dot in owner or not a GitHub URL — try search
            log(f"  🔍 [{s['name']}] Invalid URL ({url[:60] if url else 'empty'}) — searching...")
            found = search_mcp(s["name"])
            time.sleep(0.1)
            
            if found:
                s["url"] = found["html_url"]
                s["stars"] = found.get("stargazers_count", 0)
                s["language"] = found.get("language", "") or s.get("language", "")
                s["forks"] = found.get("forks_count", 0)
                s["description"] = found.get("description", "") or s.get("description", "")
                s["updated_at"] = found.get("updated_at", "")
                s["has_github_stats"] = True
                s["verified"] = True
                s["verification_note"] = "Search-replaced"
                search_fixed += 1
                log(f"    ✅ -> {found['html_url']}")
                continue
        
        # Nothing worked — strip broken link
        s["url_display"] = url
        s["url"] = ""
        s["has_github_stats"] = False
        s["verified"] = False
        s["verification_note"] = "No valid GitHub repo"
        not_found += 1
        log(f"    ❌ Stripped (no valid GitHub repo found)")
    
    log(f"\n{'='*60}")
    log("RESULTS")
    log(f"  Already OK:     {verified_ok}")
    log(f"  API-verified:   {api_fixed}")
    log(f"  Search-found:   {search_fixed}")
    log(f"  Not found (link stripped): {not_found}")
    
    final_verified = sum(1 for s in servers if s.get("verified") and s.get("has_github_stats"))
    final_unverified = sum(1 for s in servers if not s.get("verified") or not s.get("has_github_stats"))
    log(f"\n  ✅ Final verified: {final_verified}/{total} ({round(final_verified/total*100,1)}%)")
    log(f"  ⏳ Pending:       {final_unverified}/{total} ({round(final_unverified/total*100,1)}%)")
    
    # Save
    with open(LISTINGS_FILE, "w") as f:
        json.dump(servers, f, indent=2)
    log(f"✅ Saved to {LISTINGS_FILE}")

if __name__ == "__main__":
    main()
