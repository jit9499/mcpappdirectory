#!/usr/bin/env python3
"""MCP App Directory — Newsletter Subscription API Server
Simple POST endpoint that stores emails and sends welcome email via AgentMail.
Runs alongside the static HTML server on port 8083.
"""

import http.server
import json
import os
import re
import subprocess
from datetime import datetime

SUBSCRIBERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscribers.json")
SUBMISSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "submissions.json")
UNSUBSCRIBED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unsubscribed.json")
AGENTMAIL_EMAIL = os.environ.get("AGENTMAIL_EMAIL", "jituhermes@agentmail.to")
PORT = 8083

def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def send_welcome_email(email):
    """Send welcome email via AgentMail"""
    subject = "Welcome to MCP Weekly! 🎉"
    body = f"""Hi there,

Thanks for subscribing to MCP Weekly!

You'll get the best MCP (Model Context Protocol) servers delivered to your inbox every Monday.

What to expect:
- Featured MCP server of the week
- New additions to the directory
- Tips and tricks for using MCP tools
- Community highlights

Stay tuned for your first issue!

— MCP App Directory Team
https://mcpappdirectory.com

Unsubscribe: https://mcpappdirectory.com/api/unsubscribe?email={email}"""

    try:
        script = os.path.expanduser("~/.hermes/scripts/hermes_email_sender.py")
        if os.path.exists(script):
            result = subprocess.run(
                ["python3", script, "--to", email, "--subject", subject, "--body", body],
                capture_output=True, text=True, timeout=30
            )
            return result.returncode == 0
        else:
            # Fallback: use AgentMail MCP via CLI
            result = subprocess.run(
                ["python3", "-c", f"""
import json, urllib.request
payload = {json.dumps({"to": email, "subject": subject, "body": body})}
req = urllib.request.Request(
    "https://api.agentmail.to/v1/send",
    data=json.dumps({{"to": email, "subject": subject, "body": body}}).encode(),
    headers={{"Content-Type": "application/json"}}
)
urllib.request.urlopen(req, timeout=10)
"""],
                capture_output=True, text=True, timeout=30
            )
            return result.returncode == 0
    except Exception as e:
        print(f"Welcome email failed for {email}: {e}")
        return False

class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"success": False, "error": "Invalid JSON"}, 400)
            return
        
        path = self.path
        
        # --- Newsletter Subscribe ---
        if path == "/api/subscribe":
            email = data.get("email", "").strip().lower()
            
            if not email or not is_valid_email(email):
                self.send_json({"success": False, "error": "Invalid email address"}, 400)
                return
            
            subscribers = load_json(SUBSCRIBERS_FILE)
            
            if any(s["email"] == email for s in subscribers):
                self.send_json({"success": True, "message": "Already subscribed!"})
                return
            
            unsubscribed = load_json(UNSUBSCRIBED_FILE)
            if any(s["email"] == email for s in unsubscribed):
                unsubscribed = [s for s in unsubscribed if s["email"] != email]
                save_json(UNSUBSCRIBED_FILE, unsubscribed)
            
            subscribers.append({
                "email": email,
                "subscribed_at": datetime.utcnow().isoformat(),
                "source": data.get("source", "website")
            })
            save_json(SUBSCRIBERS_FILE, subscribers)
            send_welcome_email(email)
            self.send_json({"success": True, "message": "Subscribed! Check your inbox."})
            return
        
        # --- Server Submission ---
        if path == "/api/submit-server":
            name = data.get("name", "").strip()
            url = data.get("url", "").strip()
            description = data.get("description", "").strip()
            category = data.get("category", "").strip()
            
            if not name or not url:
                self.send_json({"success": False, "error": "Name and URL are required"}, 400)
                return
            
            submissions = load_json(SUBMISSIONS_FILE)
            submissions.append({
                "name": name,
                "url": url,
                "description": description,
                "category": category,
                "submitted_at": datetime.utcnow().isoformat(),
                "status": "pending"
            })
            save_json(SUBMISSIONS_FILE, submissions)
            self.send_json({"success": True, "message": "Submitted! We'll review and add it shortly."})
            return
        
        self.send_json({"success": False, "error": "Not found"}, 404)
    
    def do_GET(self):
        """Handle unsubscribe links"""
        if self.path.startswith("/api/unsubscribe"):
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            email = params.get("email", [""])[0].strip().lower()
            
            if email:
                subscribers = load_json(SUBSCRIBERS_FILE)
                unsubscribed = load_json(UNSUBSCRIBED_FILE)
                
                subscribers = [s for s in subscribers if s["email"] != email]
                unsubscribed.append({
                    "email": email,
                    "unsubscribed_at": datetime.utcnow().isoformat()
                })
                
                save_json(SUBSCRIBERS_FILE, subscribers)
                save_json(UNSUBSCRIBED_FILE, unsubscribed)
                
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h1>Unsubscribed successfully.</h1><p>You won't receive any more emails.</p>")
                return
        
        # Serve subscriber count for the dashboard
        if self.path == "/api/subscribers/count":
            subscribers = load_json(SUBSCRIBERS_FILE)
            self.send_json({"count": len(subscribers)})
            return
        
        # Serve submission count
        if self.path == "/api/submissions/count":
            submissions = load_json(SUBMISSIONS_FILE)
            self.send_json({"count": len(submissions)})
            return
        
        self.send_json({"error": "Not found"}, 404)
    
    def do_OPTIONS(self):
        """CORS preflight"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Newsletter API running on port {PORT}")
    server.serve_forever()
