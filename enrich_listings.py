#!/usr/bin/env python3
"""Enrich listings.json with GitHub stars, language, last_updated, license, forks, topics.
This script is idempotent — skips already-enriched servers unless --force passed.
Uses 10 parallel workers for speed.
Run after every aggregation (listings.json rewrite) to restore enrichment data.
"""

import json
import os
import sys
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

LISTINGS_PATH = "/root/mcpappdirectory/listings.json"
GITHUB_API = "https://api.github.com"

def get_token():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if token:
        return token
    # Try from git remote
    try:
        import subprocess
        result = subprocess.run(
            ["git", "-C", "/root/mcpappdirectory", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5
        )
        remote = result.stdout.strip()
        if "github.com" in remote and "github_pat_" in remote:
            m = re.search(r"github_pat_[a-zA-Z0-9]+", remote)
            if m:
                return m.group(0)
    except:
        pass
    return None

def github_request(url, token):
    req = Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "mcpappdirectory-enrich/1.0")
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        if e.code == 403:
            print(f"  ⚠ Rate limited at {url}")
        elif e.code == 404:
            pass  # Repo not found
        elif e.code == 409:
            pass  # Empty repo
        return None
    except (URLError, OSError, json.JSONDecodeError) as e:
        return None

def extract_github_info(url):
    """Extract owner/repo from various GitHub URL formats."""
    if not url or "github.com" not in url.lower():
        return None
    # Handle: https://github.com/owner/repo or git@github.com:owner/repo
    m = re.search(r"github\.com[/:]([^/]+)/([^/\s#?]+)", url)
    if m:
        return m.group(1), m.group(2).replace(".git", "")
    return None

def enrich_server(server, token, force=False):
    """Enrich a single server with GitHub data."""
    name = server.get("name", "?")
    
    # Skip if already enriched
    if server.get("has_github_stats") and not force:
        return server
    
    # Try to find GitHub URL
    url = server.get("url", "") or ""
    gh_info = extract_github_info(url)
    
    if not gh_info:
        server["language"] = server.get("language", "Unknown")
        server["stars"] = server.get("stars", 0)
        server["last_updated"] = server.get("last_updated", "")
        server["has_github_stats"] = False
        return server
    
    owner, repo = gh_info
    api_url = f"{GITHUB_API}/repos/{owner}/{repo}"
    
    data = github_request(api_url, token)
    if data is None:
        server["has_github_stats"] = False
        return server
    
    server["stars"] = data.get("stargazers_count", 0)
    server["forks"] = data.get("forks_count", 0)
    server["language"] = data.get("language") or server.get("language", "Unknown")
    server["last_updated"] = data.get("pushed_at", "")
    server["license"] = data["license"]["spdx_id"] if data.get("license") else None
    server["topics"] = data.get("topics", [])
    server["has_github_stats"] = True
    
    return server

def main():
    force = "--force" in sys.argv
    status_only = "--status" in sys.argv
    
    token = get_token()
    if not token:
        print("⚠ No GitHub token found. Set GITHUB_TOKEN env var.")
        print("  Will still work but at 60 req/hr rate limit.")
    
    with open(LISTINGS_PATH) as f:
        listings = json.load(f)
    
    servers = listings if isinstance(listings, list) else listings.get("servers", [])
    print(f"Loaded {len(servers)} servers from {LISTINGS_PATH}")
    
    already_enriched = sum(1 for s in servers if s.get("has_github_stats"))
    needs_enrichment = [s for s in servers if not s.get("has_github_stats") or force]
    
    print(f"Already enriched: {already_enriched}")
    print(f"Need enrichment: {len(needs_enrichment)}")
    
    if status_only:
        return
    
    if not needs_enrichment:
        print("✓ All servers already enriched.")
        return
    
    # Enrich in parallel
    start = time.time()
    enriched_count = 0
    failed_count = 0
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(enrich_server, s, token, force): s for s in needs_enrichment}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            if result.get("has_github_stats"):
                enriched_count += 1
            else:
                failed_count += 1
            if (i + 1) % 100 == 0:
                elapsed = time.time() - start
                print(f"  Progress: {i+1}/{len(needs_enrichment)} ({enriched_count} enriched, {failed_count} failed) — {elapsed:.1f}s")
    
    elapsed = time.time() - start
    
    # Save
    with open(LISTINGS_PATH, "w") as f:
        json.dump(listings, f, indent=2)
    
    # Stats
    with_stars = sum(1 for s in servers if s.get("stars") and s.get("stars", 0) > 0)
    with_lang = sum(1 for s in servers if s.get("language") and s.get("language") != "Unknown")
    with_date = sum(1 for s in servers if s.get("last_updated"))
    
    print(f"\n✓ Enrichment complete in {elapsed:.1f}s")
    print(f"  Newly enriched: {enriched_count}")
    print(f"  Failed (no GitHub/private): {failed_count}")
    print(f"  Total with stars: {with_stars}")
    print(f"  Total with language: {with_lang}")
    print(f"  Total with last_updated: {with_date}")
    print(f"  Saved to {LISTINGS_PATH}")

    # Show top 10
    with_stars_list = [(s.get("name", "?"), s.get("stars", 0)) for s in servers if s.get("stars")]
    with_stars_list.sort(key=lambda x: -x[1])
    if with_stars_list:
        print(f"\nTop 10 by stars:")
        for n, st in with_stars_list[:10]:
            print(f"  ⭐ {st} — {n}")

if __name__ == "__main__":
    main()
