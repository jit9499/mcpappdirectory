#!/usr/bin/env python3
"""
Grok Scoring v3 — Score all servers 0-100. Simpler, more robust.
"""
import json, os, sys, re, time, urllib.request, concurrent.futures
from datetime import datetime

BASE_DIR = "/root/mcpappdirectory"
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")

if not XAI_API_KEY:
    print("ERROR: XAI_API_KEY environment variable not set")
    sys.exit(1)

def log(msg):
    print(f"[{datetime.now().isoformat()}] {msg}")

def call_grok(name, url, desc):
    prompt = f"""Score this MCP server 0-100 on quality. Be critical. Only ~5% should get A (90-100) and ~15% B (70-89).

Server: {name}
URL: {url}
Description: {desc}

Return ONLY valid JSON: {{"score": 0-100, "breakdown": {{"recency": 0-20, "popularity": 0-25, "community_health": 0-15, "documentation": 0-15, "security": 0-5, "maintenance": 0-10}}, "reasoning": "1 sentence"}}"""
    data = json.dumps({"model": "grok-2-latest", "messages": [{"role": "user", "content": prompt}], "max_tokens": 200}).encode()
    req = urllib.request.Request("https://api.x.ai/v1/chat/completions", data=data, headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode())
            content = result["choices"][0]["message"]["content"]
            # Extract JSON from response
            m = re.search(r'\{.*\}', content, re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
                return parsed.get("score", 50), parsed.get("breakdown", {}), parsed.get("reasoning", "")
            return 50, {}, "parse error"
    except Exception as e:
        return None, {}, str(e)

def main():
    log("Loading listings...")
    with open(LISTINGS_FILE) as f:
        data = json.load(f)
    
    servers = data if isinstance(data, list) else data.get("servers", [])
    log(f"Loaded {len(servers)} servers")
    
    # Score only unscored or force-scored
    to_score = [s for s in servers if not s.get("score") or "--force" in sys.argv]
    log(f"To score: {len(to_score)}/{len(servers)}")
    
    if not to_score:
        log("All servers already scored")
        return
    
    # Score in parallel (5 workers to avoid rate limits)
    scored = 0
    errors = 0
    start = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for s in to_score:
            future = executor.submit(call_grok, s.get("name","?"), s.get("url",""), s.get("description",""))
            futures[future] = s
        
        for future in concurrent.futures.as_completed(futures):
            s = futures[future]
            score, breakdown, reason = future.result()
            if score is not None:
                s["score"] = score
                s["score_details"] = breakdown
                # Grade
                if score >= 90: s["grade"] = "A"
                elif score >= 70: s["grade"] = "B"
                elif score >= 50: s["grade"] = "C"
                elif score >= 30: s["grade"] = "D"
                else: s["grade"] = "F"
                scored += 1
            else:
                errors += 1
                log(f"  Error: {s.get('name','?')} -> {reason[:60]}")
            
            if (scored + errors) % 50 == 0:
                elapsed = time.time() - start
                log(f"  Progress: {scored} scored, {errors} errors ({elapsed:.0f}s)")
    
    # Save
    with open(LISTINGS_FILE, "w") as f:
        json.dump(data if isinstance(data, list) else servers, f, indent=2)
    
    grades = {}
    for s in servers:
        g = s.get("grade", "?")
        grades[g] = grades.get(g, 0) + 1
    ab = sum(1 for s in servers if s.get("grade") in ("A", "B"))
    
    elapsed = time.time() - start
    log(f"Done in {elapsed:.0f}s: {scored} scored, {errors} errors")
    log(f"Grades: {grades} | A+B: {ab}")

if __name__ == "__main__":
    main()
