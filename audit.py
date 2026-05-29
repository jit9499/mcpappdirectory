#!/usr/bin/env python3
"""
MCP App Directory — Full QA Audit Script
Tests every endpoint, page, link, and data quality metric.
Returns exit code 0 if all pass, 1 if any fail.
"""
import json
import urllib.request
import urllib.error
import sys
import hashlib
import re
import os

BASE = os.environ.get("AUDIT_BASE", "http://localhost:8082")
results = []
failures = []

def test(path, name=None, expect_status=200):
    n = name or path
    try:
        req = urllib.request.Request(BASE + path)
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            ok = resp.status == expect_status
            status = "PASS" if ok else f"FAIL (expected {expect_status}, got {resp.status})"
            results.append((n, status, ok))
            return body if ok else None
    except urllib.error.HTTPError as e:
        msg = f"FAIL ({e.code}: {e.read().decode()[:80]})"
        results.append((n, msg, False))
        return None
    except Exception as e:
        results.append((n, f"FAIL ({e})", False))
        return None

def make_slug(name, url):
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = s.strip('-')[:50]
    if not s:
        s = "server"
    h = hashlib.md5(url.encode()).hexdigest()[:6]
    return f"{s}-{h}"

print("=" * 50)
print("MCP App Directory — QA Audit")
print(f"Base URL: {BASE}")
print("=" * 50)

# ── 1. Core Pages ──
print("\n📄 Core Pages")
core_pages = ["/", "/about", "/how-it-works", "/scoring", "/submit", 
              "/leaderboard", "/dashboard", "/sitemap.xml"]
for p in core_pages:
    test(p, name=f"  {p}")

# ── 2. API Endpoints ──
print("\n🔌 API Endpoints")
api_tests = [
    ("/api/stats", "  /api/stats"),
    ("/api/servers?limit=5", "  /api/servers?limit=5"),
    ("/api/servers?grade=A", "  /api/servers?grade=A"),
    ("/api/servers?category=ai-ml", "  /api/servers?category=ai-ml"),
    ("/api/servers?search=brave", "  /api/servers?search=brave"),
    ("/api/auth/me", "  /api/auth/me"),
    ("/api/compare?ids=0,1", "  /api/compare?ids=0,1"),
]
for path, name in api_tests:
    test(path, name=name)

# ── 3. Category Pages ──
print("\n📂 Category Pages")
cats = ["development", "ai-ml", "data", "productivity", "communication",
        "cloud-devops", "browser", "security", "search", "database"]
for c in cats:
    test(f"/category/{c}", name=f"  /category/{c}")

# ── 4. Server Detail Pages (sample 100) ──
print("\n🔗 Server Detail Pages (sample 100)")
api_body = test("/api/servers?limit=100", name="  Fetch 100 servers")
if api_body:
    data = json.loads(api_body)
    servers = data.get("servers", [])
    server_ok = 0
    server_fail = 0
    for s in servers:
        slug = make_slug(s["name"], s.get("url", ""))
        # Just check via HEAD-like test
        try:
            req = urllib.request.Request(BASE + f"/servers/{slug}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    server_ok += 1
                else:
                    server_fail += 1
                    results.append((f"  /servers/{slug} ({s['name']})", f"FAIL (status {resp.status})", False))
        except Exception as e:
            server_fail += 1
            results.append((f"  /servers/{slug} ({s['name']})", f"FAIL ({e})", False))
    results.append((f"  Server pages: {server_ok}/{server_ok+server_fail} OK", 
                    f"{'PASS' if server_fail == 0 else 'FAIL'}", 
                    server_fail == 0))

# ── 5. SEO Quality Check (sample 5) ──
print("\n🎯 SEO Quality Check (sample 5)")
if api_body:
    data = json.loads(api_body)
    servers = data.get("servers", [])
    for s in servers[:5]:
        slug = make_slug(s["name"], s.get("url", ""))
        html = test(f"/servers/{slug}", name=f"  SEO: {s['name']}")
        if html:
            checks = [
                ("title", "<title>" in html),
                ("meta desc", 'name="description"' in html),
                ("og:title", 'og:title' in html),
                ("og:description", 'og:description' in html),
                ("canonical", 'rel="canonical"' in html),
                ("ld+json", 'application/ld+json' in html),
                ("schema SoftwareApplication", 'SoftwareApplication' in html),
                ("AggregateRating", 'AggregateRating' in html),
                ("score", '/100' in html),
                ("grade badge", 'grade-badge' in html),
                ("GitHub link", 'github.com' in html),
                ("install", 'install' in html.lower() or 'npm' in html.lower() or 'git clone' in html),
            ]
            all_ok = all(v for _, v in checks)
            for check_name, ok in checks:
                if not ok:
                    results.append((f"  SEO/{s['name']}: {check_name}", f"FAIL (missing)", False))
            if all_ok:
                results.append((f"  SEO/{s['name']}: all 12/12 checks", "PASS", True))

# ── 6. Data Quality ──
print("\n📊 Data Quality")
api_body = test("/api/stats", name="  Fetch stats")
if api_body:
    stats = json.loads(api_body).get("stats", {})
    total = stats.get("total_servers", 0)
    avg = stats.get("average_score", 0)
    grades = stats.get("grade_distribution", {})
    
    results.append((f"  Total servers: {total}", "PASS" if total >= 800 else "FAIL (too few)", total >= 800))
    results.append((f"  Average score: {avg}", "PASS" if avg > 30 else "FAIL (too low)", avg > 30))
    results.append((f"  Grade A: {grades.get('A',0)}", "PASS" if grades.get('A',0) > 0 else "FAIL (no A)", grades.get('A',0) > 0))
    
    # Check data quality
    body = test("/api/servers?limit=1917", name="  Fetch all servers")
    if body:
        all_data = json.loads(body)
        all_servers = all_data.get("servers", [])
        
        no_score = sum(1 for s in all_servers if not s.get("score"))
        no_name = sum(1 for s in all_servers if not s.get("name"))
        no_grade = sum(1 for s in all_servers if not s.get("grade"))
        no_cat = sum(1 for s in all_servers if not s.get("category"))
        with_gh = sum(1 for s in all_servers if s.get("has_github_stats"))
        with_stars = sum(1 for s in all_servers if s.get("stars", 0) > 0)
        
        results.append((f"  All have name: {no_name} missing", "PASS" if no_name == 0 else "FAIL", no_name == 0))
        results.append((f"  All have score: {no_score} missing", "PASS" if no_score == 0 else "FAIL", no_score == 0))
        results.append((f"  All have grade: {no_grade} missing", "PASS" if no_grade == 0 else "FAIL", no_grade == 0))
        results.append((f"  All have category: {no_cat} missing", "PASS" if no_cat == 0 else "FAIL", no_cat == 0))
        results.append((f"  With GitHub stats: {with_gh}/{total}", "PASS" if with_gh > total/3 else "WARN (low)", with_gh > total/3))
        results.append((f"  With stars > 0: {with_stars}/{total}", "PASS" if with_stars > 100 else "WARN (low)", with_stars > 100))

# ── 7. Auth System ──
print("\n🔐 Auth System")
# Test auth redirect manually via a raw HTTP request
import http.client
try:
    parsed = urllib.parse.urlparse(BASE)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=10)
    conn.request("GET", "/api/auth/github")
    resp = conn.getresponse()
    code = resp.status
    loc = resp.getheader("Location", "")
    conn.close()
    ok = code == 302
    results.append((f"  /api/auth/github redirects (got {code})", 
                    "PASS" if ok else f"FAIL (expected 302)", ok))
    results.append((f"  Redirects to GitHub OAuth", 
                   "PASS" if "github.com/login/oauth" in loc else f"FAIL (wrong: {loc[:60]})", 
                   "github.com/login/oauth" in loc))
except Exception as e:
    results.append((f"  /api/auth/github redirects", f"FAIL ({e})", False))
me_body = test("/api/auth/me", name="  /api/auth/me (unauthenticated)")
if me_body:
    me_data = json.loads(me_body)
    is_unauthenticated = me_data.get("authenticated") == False
    results.append((f"  /api/auth/me returns authenticated=false", 
                    "PASS" if is_unauthenticated else "FAIL", 
                    is_unauthenticated))

# ── Results ──
print("\n" + "=" * 50)
passed = sum(1 for _, _, ok in results if ok)
warned = sum(1 for _, msg, ok in results if not ok and "WARN" in msg)
failed = sum(1 for _, msg, ok in results if not ok and "FAIL" in msg)
print(f"RESULTS: {passed} passed, {warned} warnings, {failed} failures")
print("=" * 50)

print("\n📋 Detail:")
for name, status, ok in results:
    icon = "✅" if ok else ("⚠️" if "WARN" in str(status) else "❌")
    print(f"  {icon} {name}: {status}")

print(f"\n{'✅ ALL CHECKS PASSED' if failed == 0 else '❌ SOME CHECKS FAILED'}")
sys.exit(0 if failed == 0 else 1)
