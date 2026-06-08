#!/usr/bin/env python3
"""Quick test: verify a few repos exist on GitHub API."""
import os, json, urllib.request, sys

def get_token():
    env_path = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GITHUB_API_KEY="):
                    return line.split("=",1)[1].strip().strip('"').strip("'")
    return ""

token = get_token()
print(f"Token: {'✅' if token else '❌'} (len={len(token)})", flush=True)

headers = {"User-Agent":"MCPAppDir/1.0","Accept":"application/vnd.github.v3+json"}
if token:
    headers["Authorization"] = f"Bearer {token}"

# Test repos
repos = [
    ("n8n-io", "n8n"),
    ("google-gemini", "gemini-cl"),
    ("d4vinci", "scraplin"),
    ("bytedance", "ui-tars-desktop"),
    ("github", "github-mcp-server"),
]

for owner, repo in repos:
    url = f"https://api.github.com/repos/{owner}/{repo}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read())
            print(f"  {owner}/{repo}: ✅ stars={d.get('stargazers_count',0)}", flush=True)
    except urllib.error.HTTPError as e:
        print(f"  {owner}/{repo}: ❌ HTTP {e.code}", flush=True)
    except Exception as e:
        print(f"  {owner}/{repo}: ❌ {type(e).__name__}: {e}", flush=True)
