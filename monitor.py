#!/usr/bin/env python3
"""MCP App Directory — Traffic & Health Monitor
Tracks visitor counts from server logs, site health, and key metrics.
Updates a dashboard file and reports issues.
"""

import os
import json
import subprocess
from datetime import datetime, timedelta

SITE_DIR = "/root/mcpappdirectory"
LOG_FILE = "/tmp/mcpappdir.log"
DASHBOARD_FILE = os.path.join(SITE_DIR, "dashboard.json")
SUBSCRIBERS_FILE = os.path.join(SITE_DIR, "subscribers.json")

def count_log_entries(hours=24):
    """Count HTTP requests in the last N hours from the log"""
    if not os.path.exists(LOG_FILE):
        return 0, 0
    
    cutoff = datetime.now() - timedelta(hours=hours)
    total = 0
    recent = 0
    
    with open(LOG_FILE) as f:
        for line in f:
            total += 1
            # Parse log line: 127.0.0.1 - - [27/May/2026 23:14:39] "POST /api/subscribe HTTP/1.1" 200 -
            try:
                date_str = line.split("[")[1].split("]")[0]
                log_date = datetime.strptime(date_str, "%d/%b/%Y %H:%M:%S")
                if log_date > cutoff:
                    recent += 1
            except (IndexError, ValueError):
                pass
    
    return total, recent

def check_health():
    """Check site health via local curl"""
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8082/"],
            capture_output=True, text=True, timeout=10
        )
        status = result.stdout.strip()
        
        # Check content
        content = subprocess.run(
            ["curl", "-s", "http://localhost:8082/"],
            capture_output=True, text=True, timeout=10
        )
        has_title = "MCP App Directory" in content.stdout
        server_count = content.stdout.count('name: "')
        
        return {
            "status_code": status,
            "online": status == "200",
            "has_title": has_title,
            "server_count": server_count
        }
    except Exception as e:
        return {"status_code": "ERR", "online": False, "error": str(e)}

def get_github_stats():
    """Get GitHub repo stats"""
    try:
        result = subprocess.run(
            ["curl", "-s", "https://api.github.com/repos/jit9499/mcpappdirectory"],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        return {
            "stars": data.get("stargazers_count", 0),
            "forks": data.get("forks_count", 0),
            "open_issues": data.get("open_issues_count", 0)
        }
    except:
        return {"stars": 0, "forks": 0, "open_issues": 0}

def get_subscriber_count():
    """Count newsletter subscribers"""
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE) as f:
            try:
                subscribers = json.load(f)
                return len(subscribers)
            except:
                return 0
    return 0

def count_listings():
    """Count MCP server listings in the HTML"""
    html_path = os.path.join(SITE_DIR, "index.html")
    if os.path.exists(html_path):
        with open(html_path) as f:
            content = f.read()
            # Count entries in the servers array
            return content.count('name: "')
    return 0

def main():
    health = check_health()
    total_visits, recent_24h = count_log_entries(24)
    total_visits_all, _ = count_log_entries(24 * 30)  # approximate all-time
    
    dashboard = {
        "timestamp": datetime.now().isoformat(),
        "site": {
            "online": health.get("online", False),
            "status_code": health.get("status_code", "N/A"),
            "https": "OK",  # verified separately
            "listings": count_listings()
        },
        "traffic": {
            "visits_24h": recent_24h,
            "visits_total": total_visits_all,
            "log_entries_total": total_visits
        },
        "audience": {
            "subscribers": get_subscriber_count()
        },
        "github": get_github_stats(),
        "revenue": {
            "monthly": 0,
            "total": 0,
            "note": "Monetization not yet active"
        }
    }
    
    with open(DASHBOARD_FILE, "w") as f:
        json.dump(dashboard, f, indent=2)
    
    # Print summary for cron output
    status_icon = "✅" if dashboard["site"]["online"] else "❌"
    print(f"{status_icon} MCP App Directory Dashboard")
    print(f"📈 Listings: {dashboard['site']['listings']}")
    print(f"👁️  Visits (24h): {dashboard['traffic']['visits_24h']}")
    print(f"👥 Subscribers: {dashboard['audience']['subscribers']}")
    print(f"⭐ GitHub Stars: {dashboard['github']['stars']}")
    print(f"💰 Revenue: ${dashboard['revenue']['monthly']}/mo")
    
    if not dashboard["site"]["online"]:
        print(f"⚠️  HEALTH ISSUE! Status: {dashboard['site']['status_code']}")

if __name__ == "__main__":
    main()
