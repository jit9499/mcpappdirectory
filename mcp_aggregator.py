#!/usr/bin/env python3
"""
MCP Server Aggregator — Auto-populates mcpappdirectory.com from multiple sources.

Sources:
1. Official MCP Registry API (registry.modelcontextprotocol.io)
2. awesome-mcp-servers GitHub repo (punkpeye/awesome-mcp-servers)
3. npm packages tagged "mcp-server"
4. PyPI packages tagged "mcp-server"

Run: python3 mcp_aggregator.py
Schedule: Every 6 hours via cron
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")
ALL_SERVERS_FILE = os.path.join(BASE_DIR, "all_servers.json")
LOG_FILE = os.path.join(BASE_DIR, "aggregator.log")

KNOWN_CATEGORIES = [
    "Developer Tools", "Database", "Cloud Platforms", "AI & Machine Learning",
    "Browser Automation", "Search", "Communication", "Finance",
    "Security", "Monitoring", "Web Scraping", "Documentation Access",
    "Project Management", "Code Analysis", "Knowledge & Memory",
    "Research & Data", "App Automation", "Agent Orchestration",
    "Content Creation", "Productivity", "DevOps", "Other"
]

def log(msg):
    ts = datetime.utcnow().isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return default or []
    return default or []

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def fetch_url(url, retries=2, timeout=15):
    """Fetch URL with retry logic"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "MCPAppDirectory/1.0 (aggregator; +https://mcpappdirectory.com)",
                "Accept": "application/json"
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode()
        except Exception as e:
            log(f"  Attempt {attempt+1}/{retries} failed for {url}: {e}")
            if attempt < retries - 1:
                time.sleep(1)
    return None


def fetch_official_registry():
    """Fetch servers from the official MCP Registry API"""
    log("[Official Registry] Fetching from registry.modelcontextprotocol.io...")
    servers = {}
    cursor = None
    page = 0
    base_url = "https://registry.modelcontextprotocol.io/v0/servers?limit=100"
    
    while True:
        url = base_url
        if cursor:
            url += f"&cursor={cursor}"
        
        data = fetch_url(url)
        if not data:
            break
        
        try:
            result = json.loads(data)
        except json.JSONDecodeError:
            log(f"  Failed to parse response")
            break
        
        for entry in result.get("servers", []):
            srv = entry.get("server", {})
            name = srv.get("name", "")
            if not name:
                continue
            
            # Extract org/name from full name like "io.github.user/repo"
            display_name = name.split("/")[-1] if "/" in name else name
            display_name = display_name.replace("-mcp-server", "").replace("-mcp", "").replace("_", " ").title()
            
            repo_url = srv.get("repository", {}).get("url", "")
            description = srv.get("description", "")
            # Clean description
            if description and description.startswith("An MCP server that provides"):
                description = description.replace("An MCP server that provides", "").strip()
            if description and description.startswith("A Model Context Protocol server"):
                description = description.replace("A Model Context Protocol server", "").strip()
            
            servers[name] = {
                "name": display_name,
                "id": name,
                "url": repo_url if repo_url else f"https://github.com/{name}",
                "description": description or f"MCP server: {display_name}",
                "category": classify_server(display_name, description, repo_url),
                "source": "official-registry",
                "github_stars": 0,
                "language": detect_language(repo_url),
                "added_at": datetime.utcnow().isoformat()
            }
        
        metadata = result.get("metadata", {})
        cursor = metadata.get("nextCursor")
        page += 1
        
        if not cursor or page >= 50:  # Safety limit
            break
    
    log(f"  Found {len(servers)} servers from Official Registry")
    return servers


def fetch_awesome_list():
    """Fetch servers from the awesome-mcp-servers GitHub repo"""
    log("[Awesome List] Fetching from punkpeye/awesome-mcp-servers...")
    servers = {}
    
    raw_url = "https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md"
    content = fetch_url(raw_url)
    if not content:
        log("  Failed to fetch awesome list")
        return servers
    
    # Parse markdown tables - they contain server entries
    lines = content.split("\n")
    in_table = False
    headers = []
    
    for i, line in enumerate(lines):
        # Detect table rows
        if line.strip().startswith("|") and line.count("|") >= 3:
            cells = [c.strip() for c in line.split("|")[1:-1]]
            
            if not in_table:
                # Check if next line is separator
                if i + 1 < len(lines) and "---" in lines[i + 1]:
                    headers = cells
                    in_table = True
                continue
            
            if len(cells) >= 3:
                name = cells[0] if len(cells) > 0 else ""
                desc = cells[1] if len(cells) > 1 else ""
                url_cell = cells[2] if len(cells) > 2 else ""
                
                # Extract URL from markdown link [text](url)
                url_match = re.search(r'\(([^)]+)\)', url_cell)
                repo_url = url_match.group(1) if url_match else ""
                
                if name and repo_url:
                    sid = re.sub(r'[^a-zA-Z0-9_-]', '-', name.lower().strip())
                    desc_clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', desc)
                    
                    servers[sid] = {
                        "name": name.strip(),
                        "id": sid,
                        "url": repo_url,
                        "description": desc_clean.strip(),
                        "category": classify_server(name, desc_clean, repo_url),
                        "source": "awesome-list",
                        "github_stars": 0,
                        "language": detect_language(repo_url),
                        "added_at": datetime.utcnow().isoformat()
                    }
        else:
            in_table = False
    
    log(f"  Found {len(servers)} servers from awesome list")
    return servers


def fetch_npm_packages():
    """Fetch npm packages tagged with 'mcp-server'"""
    log("[npm] Fetching mcp-server tagged packages...")
    servers = {}
    
    url = "https://registry.npmjs.org/-/v1/search?text=keywords:mcp-server&size=250"
    data = fetch_url(url)
    if not data:
        return servers
    
    try:
        result = json.loads(data)
    except json.JSONDecodeError:
        return servers
    
    for pkg in result.get("objects", []):
        pkg_data = pkg.get("package", {})
        name = pkg_data.get("name", "")
        if not name:
            continue
        
        links = pkg_data.get("links", {})
        repo_url = links.get("repository", links.get("npm", ""))
        if not repo_url:
            continue
        
        description = pkg_data.get("description", "")
        sid = f"npm-{name.replace('/', '-')}"
        
        servers[sid] = {
            "name": name.split("/")[-1] if "/" in name else name,
            "id": sid,
            "url": repo_url,
            "description": description or f"npm package: {name}",
            "category": classify_server(name, description, repo_url),
            "source": "npm",
            "github_stars": 0,
            "language": "TypeScript",
            "added_at": datetime.utcnow().isoformat()
        }
    
    log(f"  Found {len(servers)} servers from npm")
    return servers


def fetch_pypi_packages():
    """Fetch PyPI packages tagged with 'mcp-server'"""
    log("[PyPI] Fetching mcp-server tagged packages...")
    servers = {}
    
    url = "https://pypi.org/simple/?format=json"
    data = fetch_url(url)
    if not data:
        return servers
    
    try:
        result = json.loads(data)
    except json.JSONDecodeError:
        return servers
    
    packages = result.get("packages", [])
    # PyPI doesn't have keyword search in simple API, so we look for mcp in names
    mcp_packages = [p for p in packages if "mcp" in p.lower() 
                    and any(kw in p.lower() for kw in ["mcp-server", "mcp-server", "mcp"])]
    
    # Limit to reduce API calls
    mcp_packages = mcp_packages[:200]
    
    for pkg_name in mcp_packages:
        url = f"https://pypi.org/pypi/{pkg_name}/json"
        data = fetch_url(url)
        if not data:
            continue
        
        try:
            info = json.loads(data).get("info", {})
        except json.JSONDecodeError:
            continue
        
        name = info.get("name", pkg_name)
        description = info.get("summary", "")
        repo_url = info.get("home_page", "") or info.get("project_urls", {}).get("Source", "")
        if not repo_url:
            repo_url = f"https://pypi.org/project/{name}/"
        
        sid = f"pypi-{name.lower()}"
        servers[sid] = {
            "name": name,
            "id": sid,
            "url": repo_url,
            "description": description or f"PyPI package: {name}",
            "category": classify_server(name, description, repo_url),
            "source": "pypi",
            "github_stars": 0,
            "language": "Python",
            "added_at": datetime.utcnow().isoformat()
        }
    
    log(f"  Found {len(servers)} servers from PyPI")
    return servers


def detect_language(repo_url):
    """Detect language from repo URL"""
    if not repo_url:
        return "Unknown"
    repo_url = repo_url.lower()
    if "typescript" in repo_url or "ts-" in repo_url or ".ts" in repo_url:
        return "TypeScript"
    if "python" in repo_url or "py-" in repo_url or ".py" in repo_url:
        return "Python"
    if "go-" in repo_url or "golang" in repo_url:
        return "Go"
    if "rust" in repo_url or "rs-" in repo_url:
        return "Rust"
    if "java" in repo_url:
        return "Java"
    if "kotlin" in repo_url:
        return "Kotlin"
    return "Unknown"


def classify_server(name, description, url):
    """Classify a server into a category based on name, description, and URL"""
    text = f"{name} {description} {url}".lower()
    
    category_map = [
        ("database", ["sql", "postgres", "mysql", "sqlite", "mongodb", "redis", "database", "db-", "couch", "dynamo"]),
        ("cloud", ["aws", "gcp", "azure", "cloud", "kubernetes", "k8s", "docker", "terraform", "digitalocean"]),
        ("browser", ["browser", "playwright", "puppeteer", "selenium", "chrome", "webdriver"]),
        ("search", ["search", "elastic", "algolia", "meilisearch", "whoosh"]),
        ("communication", ["slack", "discord", "telegram", "email", "gmail", "outlook", "teams", "whatsapp"]),
        ("finance", ["finance", "stock", "stripe", "payment", "coin", "crypto", "bank", "invoice"]),
        ("security", ["security", "auth", "oauth", "vault", "secret", "encrypt", "sso"]),
        ("monitoring", ["monitor", "grafana", "prometheus", "datadog", "sentry", "log", "observability"]),
        ("web-scraping", ["scrape", "crawl", "firecrawl", "jina", "extract", "html-to"]),
        ("docs", ["documentation", "docs", "wiki", "notion", "confluence", "readme"]),
        ("project-management", ["jira", "linear", "asana", "trello", "github-project", "clickup", "todo"]),
        ("code-analysis", ["code-analysis", "linter", "sonar", "codeql", "static-analysis"]),
        ("knowledge-memory", ["memory", "knowledge", "vector", "embeddings", "rag", "pinecone", "chroma"]),
        ("research", ["research", "arxiv", "paper", "scholar", "pubmed", "research"]),
        ("automation", ["automation", "zapier", "make.com", "n8n", "workflow", "pipelin"]),
        ("ai-ml", ["ai", "ml", "llm", "openai", "anthropic", "claude", "gpt", "huggingface", "replicate", "model"]),
        ("developer-tools", ["git", "github", "api", "rest", "graphql", "cli", "terminal", "ssh", "dev"]),
    ]
    
    for cat, keywords in category_map:
        if any(kw in text for kw in keywords):
            # Map to user-friendly category names
            cat_map = {
                "database": "Database", "cloud": "Cloud Platforms",
                "browser": "Browser Automation", "search": "Search",
                "communication": "Communication", "finance": "Finance",
                "security": "Security", "monitoring": "Monitoring",
                "web-scraping": "Web Scraping", "docs": "Documentation Access",
                "project-management": "Project Management",
                "code-analysis": "Code Analysis", "knowledge-memory": "Knowledge & Memory",
                "research": "Research & Data", "automation": "App Automation",
                "ai-ml": "AI & Machine Learning", "developer-tools": "Developer Tools"
            }
            return cat_map.get(cat, "Developer Tools")
    
    return "Developer Tools"


def deduplicate_and_merge(all_sources):
    """Merge servers from all sources, deduplicating by URL or name"""
    merged = {}
    
    for source_name, servers in all_sources.items():
        for sid, server in servers.items():
            url = server.get("url", "").lower().rstrip("/")
            name = server.get("name", "").lower()
            
            # Dedup key: use normalized URL if available, else name
            dup_key = None
            if url and "github.com" in url:
                # Extract org/repo from GitHub URL
                match = re.search(r'github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$', url)
                if match:
                    dup_key = f"github:{match.group(1).lower()}"
            
            if not dup_key:
                dup_key = f"name:{name}"
            
            if dup_key in merged:
                # Merge: prefer longer description, keep higher quality source
                existing = merged[dup_key]
                if len(server.get("description", "")) > len(existing.get("description", "")):
                    existing["description"] = server["description"]
                if server.get("source") == "official-registry" and existing.get("source") != "official-registry":
                    existing["source"] = "official-registry"
                if server.get("language") != "Unknown" and existing.get("language") == "Unknown":
                    existing["language"] = server["language"]
            else:
                merged[dup_key] = dict(server)
    
    return list(merged.values())


def main():
    log("=" * 60)
    log("MCP Aggregator — Starting collection")
    log("=" * 60)
    
    # Fetch from all sources
    sources = {}
    
    sources["official"] = fetch_official_registry()
    sources["awesome"] = fetch_awesome_list()
    sources["npm"] = fetch_npm_packages()
    sources["pypi"] = fetch_pypi_packages()
    
    total_raw = sum(len(s) for s in sources.values())
    log(f"\nTotal raw entries: {total_raw}")
    
    # Deduplicate and merge
    merged = deduplicate_and_merge(sources)
    log(f"After deduplication: {len(merged)} unique servers")
    
    # Save all servers
    save_json(ALL_SERVERS_FILE, {
        "count": len(merged),
        "last_updated": datetime.utcnow().isoformat(),
        "sources": {k: len(v) for k, v in sources.items()},
        "servers": merged
    })
    
    # Update listings.json (simplified for the website)
    simplified = []
    for s in merged:
        simplified.append({
            "name": s.get("name", ""),
            "url": s.get("url", ""),
            "description": s.get("description", ""),
            "category": s.get("category", "Developer Tools"),
            "language": s.get("language", "Unknown"),
            "stars": s.get("github_stars", 0)
        })
    
    save_json(LISTINGS_FILE, simplified)
    
    # Category breakdown
    categories = {}
    for s in merged:
        cat = s.get("category", "Other")
        categories[cat] = categories.get(cat, 0) + 1
    
    log("\nCategory breakdown:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        log(f"  {cat}: {count}")
    
    log(f"\n✓ Aggregation complete. {len(merged)} servers saved to listings.json")
    return merged


if __name__ == "__main__":
    merged = main()
    print(json.dumps({"count": len(merged)}, indent=2))
