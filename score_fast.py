#!/usr/bin/env python3
"""
Quick Grok Scoring v2 — Score all servers 0-100 with 8 parameters.
Uses xAI API. ~$4 for all 1900 servers.
"""
import json, os, sys, re, time, urllib.request, concurrent.futures
from datetime import datetime

BASE_DIR = "/root/mcpappdirectory"
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")

XAI_API_KEY = os.environ.get("XAI_API_KEY")
if not XAI_API_KEY:
    print("XAI_API_KEY not set")
    sys.exit(1)

LOG_FILE = os.path.join(BASE_DIR, "grok_scoring.log")

def log(msg):
    ts = datetime.now().isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def call_grok(name, url, desc, lang):
    system = """You are an MCP Server Quality Auditor. Analyze this server and return JSON with scores 0-100 total based on 8 parameters:

1. recency (0-20): When was last commit? How actively maintained?
2. documentation (0-15): Is README complete? Install instructions? Usage examples?
3. tests (0-10): Does it have test infrastructure? CI/CD?
4. auth (0-10): Does it handle auth/API keys/tokens properly?
5. star_velocity (0-15): Stars relative to age — is it growing?
6. security (0-15): Proper error handling? Input validation? No obvious vulns?
7. install_instructions (0-15): Clear setup steps? Config examples?

Return ONLY valid JSON:
{"score": <int 0-100>, "grade": "<A|B|C|D|F>", "breakdown": {"recency": <int>, "documentation": <int>, "tests": <int>, "auth": <int>, "star_velocity": <int>, "security": <int>, "install_instructions": <int>}, "summary": "<one line verdict>"}

A=90-100, B=70-89, C=50-69, D=30-49, F=0-29"""

    user = f"MCP Server: {name}\nURL: {url}\nDescription: {desc}\nLanguage: {lang}\n\nScore this server 0-100."
    
    data = json.dumps({"model": "grok-4.20-0309-non-reasoning", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "temperature": 0.1, "max_tokens": 400}).encode()
    req = urllib.request.Request("https://api.x.ai/v1/chat/completions", data=data, headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"})
    
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                text = result["choices"][0]["message"]["content"].strip()
                # Extract JSON
                m = re.search(r'\{[^{}]*\}', text, re.DOTALL)
                if m: return json.loads(m.group(0))
                return json.loads(text)
        except Exception as e:
            log(f"  ✗ Attempt {attempt+1} failed: {e}")
            time.sleep(2)
    return None

def score_server(server):
    name = server.get("name", "")
    url = server.get("url", "")
    desc = server.get("description", "")
    lang = server.get("language", "")
    
    result = call_grok(name, url, desc[:200], lang)
    if not result:
        server["score"] = 0
        server["grade"] = "F"
        server["score_details"] = {}
        return server
    
    score = result.get("score", 0)
    grade = result.get("grade", "F")
    breakdown = result.get("breakdown", {})
    
    server["score"] = max(0, min(100, score))
    server["grade"] = grade
    server["score_details"] = breakdown
    
    return server

def main():
    servers = json.load(open(LISTINGS_FILE))
    log(f"Scoring {len(servers)} servers with Grok...")
    
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(score_server, s): i for i, s in enumerate(servers)}
        done = 0
        for f in concurrent.futures.as_completed(futures):
            try:
                f.result()
                done += 1
                if done % 50 == 0:
                    grades = {}
                    for s in servers[:done]:
                        g = s.get("grade","")
                        grades[g] = grades.get(g, 0) + 1
                    log(f"  Progress: {done}/{len(servers)} — {grades}")
                    json.dump(servers, open(LISTINGS_FILE, "w"), indent=2)
            except Exception as e:
                log(f"  ✗ Error: {e}")
    
    json.dump(servers, open(LISTINGS_FILE, "w"), indent=2)
    
    grades = {}
    for s in servers:
        g = s.get("grade","")
        grades[g] = grades.get(g, 0) + 1
    ab = sum(1 for s in servers if s.get("grade") in ("A","B"))
    avg = sum(s.get("score",0) for s in servers) / max(len(servers),1)
    log(f"Done! Grades: {grades}")
    log(f"A+B: {ab}, Avg: {avg:.1f}/100")

if __name__ == "__main__":
    main()
