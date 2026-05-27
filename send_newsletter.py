#!/usr/bin/env python3
"""MCP Weekly — Newsletter sender
Cron job that composes and sends the weekly MCP digest to all subscribers.
Runs: Every Monday 08:00 IST (02:30 UTC)

Usage: python3 send_newsletter.py [--dry-run]
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "subscribers.json")
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")
UNSUBSCRIBED_FILE = os.path.join(BASE_DIR, "unsubscribed.json")

def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def get_unsubscribed():
    unsub = load_json(UNSUBSCRIBED_FILE)
    return [u["email"] for u in unsub]

def send_email(to, subject, body):
    script = os.path.expanduser("~/.hermes/scripts/hermes_email_sender.py")
    cmd = ["python3", script, "--to", to, "--subject", subject, "--body", body]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        return False

def get_new_servers(days=7):
    """Get servers added in the last N days based on a rough heuristic.
    Since listings.json doesn't have timestamps, we pick the last 20 entries
    as 'recently added' — the server curator appends to the file.
    """
    servers = load_json(LISTINGS_FILE)
    if not servers:
        return [], 0
    
    # Take last 20 entries as "new this week" (assumes append-only curation)
    new_servers = servers[-20:]
    return new_servers, len(servers)

def get_featured_server(servers):
    """Pick a featured server — prefer one with a description or stars."""
    for s in reversed(servers):
        if s.get("description") and len(s["description"]) > 20:
            return s
    return servers[-1] if servers else None

def compose_newsletter(new_servers, total_count):
    """Compose the MCP Weekly newsletter."""
    now = datetime.now().strftime("%B %d, %Y")
    featured = get_featured_server(new_servers)
    
    # Top picks
    top_picks = []
    for s in new_servers[-5:]:
        name = s.get("name", "Unnamed")
        desc = s.get("description", "No description")
        url = s.get("url", "#")
        cat = s.get("category", "Other")
        stars = s.get("stars", 0)
        stars_str = f" ⭐{stars}" if stars else ""
        top_picks.append(f"• **{name}** ({cat}){stars_str}\n  {desc}\n  {url}")
    
    picks_section = "\n\n".join(top_picks) if top_picks else "No new servers this week."
    
    newsletter = f"""📬 **MCP Weekly** — {now}

The weekly roundup of everything happening in the MCP ecosystem.

---

**📊 By the Numbers**
• {total_count}+ servers listed on mcpappdirectory.com
• {len(new_servers)} new servers added this week
• 🎯 New subscribers: Growing daily

---

**🔥 Featured This Week**
{featured.get('name', 'MCP Server')} — {featured.get('description', '') if featured else ''}
View: https://mcpappdirectory.com

---

**🆕 New MCP Servers Added This Week**

{picks_section}

---

**💡 Pro Tip**
Bookmark mcpappdirectory.com to discover new MCP servers every day. Use the search + category filters to find exactly what you need.

---

**📣 Want to sponsor MCP Weekly?**
Reply to this email for sponsorship rates.

— MCP App Directory Team
https://mcpappdirectory.com

Unsubscribe: https://mcpappdirectory.com/api/unsubscribe?email={{email}}"""
    
    return newsletter

def main():
    dry_run = "--dry-run" in sys.argv
    
    print("📬 MCP Weekly Newsletter")
    print(f"{'⚡ DRY RUN' if dry_run else '🚀 SENDING'}")
    print("─" * 40)
    
    subscribers = load_json(SUBSCRIBERS_FILE)
    unsubscribed = get_unsubscribed()
    
    # Filter out unsubscribed and test emails
    valid = [s for s in subscribers 
             if s["email"] not in unsubscribed 
             and not s["email"].endswith("@example.com")
             and not s["email"].endswith("@test.com")]
    
    print(f"Subscribers: {len(subscribers)} total, {len(valid)} valid")
    
    new_servers, total_count = get_new_servers()
    print(f"New servers this week: {len(new_servers)}")
    print(f"Total servers: {total_count}")
    
    if not valid:
        print("No valid subscribers to send to.")
        return
    
    newsletter_body = compose_newsletter(new_servers, total_count)
    
    if dry_run:
        print("\n📄 NEWSLETTER PREVIEW:")
        print(newsletter_body[:500] + "...\n")
        print(f"Would send to {len(valid)} recipients")
        for s in valid:
            print(f"  → {s['email']}")
        return
    
    # Send
    subject = f"MCP Weekly — {datetime.now().strftime('%B %d, %Y')}"
    success = 0
    failed = 0
    
    for subscriber in valid:
        email = subscriber["email"]
        # Personalize with unsubscribe link
        personal_body = newsletter_body.replace("{{email}}", email)
        print(f"  Sending to {email}...", end=" ")
        if send_email(email, subject, personal_body):
            print("✅")
            success += 1
        else:
            print("❌")
            failed += 1
    
    print(f"\n✅ Sent to {success}{' ⚠️ ' + str(failed) + ' failed' if failed else ''}")

if __name__ == "__main__":
    main()
