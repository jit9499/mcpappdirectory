#!/usr/bin/env python3
"""
MCP App Directory — Combined Server that serves both the static site and API.
Runs on port 8082, proxied through Traefik on 443.
"""

import http.server
import json
import os
import re
import subprocess
import urllib.request
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")
SKILLS_FILE = os.path.join(BASE_DIR, "skills_data.json")
SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "subscribers.json")
SUBMISSIONS_FILE = os.path.join(BASE_DIR, "submissions.json")
UNSUBSCRIBED_FILE = os.path.join(BASE_DIR, "unsubscribed.json")
HTML_FILE = os.path.join(BASE_DIR, "index.html")
PORT = 8082

def load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except:
            return default or []
    return default or []

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def is_valid_email(email):
    return bool(re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email))

def send_welcome_email(email):
    try:
        script = os.path.expanduser("~/.hermes/scripts/hermes_email_sender.py")
        subject = "Welcome to MCP App Directory! 🚀"
        body = f"""Thanks for subscribing to MCP App Directory!

You'll get the best MCP servers delivered to your inbox.

— MCP App Directory Team
https://mcpappdirectory.com

Unsubscribe: https://mcpappdirectory.com/api/unsubscribe?email={email}"""
        if os.path.exists(script):
            result = subprocess.run(
                ["python3", script, "--to", email, "--subject", subject, "--body", body],
                capture_output=True, text=True, timeout=30
            )
            return result.returncode == 0
    except:
        pass
    return False

def serve_static(handler, path):
    """Serve a static file with proper content type"""
    if path == "/":
        path = HTML_FILE
    
    if not os.path.exists(path):
        handler.send_response(404)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(json.dumps({"error": "Not found"}).encode())
        return
    
    ext = os.path.splitext(path)[1].lower()
    content_types = {
        ".html": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
    }
    
    with open(path, "rb") as f:
        content = f.read()
    
    handler.send_response(200)
    handler.send_header("Content-Type", content_types.get(ext, "text/plain"))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-cache, must-revalidate")
    handler.end_headers()
    handler.wfile.write(content)

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path
        
        # API endpoints
        if path == "/api/servers":
            servers = load_json(LISTINGS_FILE, [])
            page = int(self.headers.get("X-Page", 1))
            per_page = 50
            start = (page - 1) * per_page
            end = start + per_page
            self.send_json({
                "count": len(servers),
                "page": page,
                "servers": servers[start:end]
            })
            return
        
        if path == "/api/servers/all":
            servers = load_json(LISTINGS_FILE, [])
            self.send_json({"count": len(servers), "servers": servers})
            return
        
        if path == "/api/skills":
            skills = load_json(SKILLS_FILE, [])
            self.send_json({"count": len(skills), "skills": skills})
            return
        
        if path == "/api/stats":
            servers = load_json(LISTINGS_FILE, [])
            skills = load_json(SKILLS_FILE, [])
            subs = load_json(SUBSCRIBERS_FILE, [])
            cats = {}
            for s in servers:
                c = s.get("category", "Other")
                cats[c] = cats.get(c, 0) + 1
            sources = {}
            for s in servers:
                src = s.get("source", "unknown")
                sources[src] = sources.get(src, 0) + 1
            self.send_json({
                "servers": len(servers),
                "skills": len(skills),
                "subscribers": len(subs),
                "categories": len(cats),
                "category_breakdown": sorted(cats.items(), key=lambda x: -x[1]),
                "sources": sources
            })
            return
        
        if path == "/api/subscribers/count":
            subs = load_json(SUBSCRIBERS_FILE, [])
            self.send_json({"count": len(subs)})
            return
        
        if path.startswith("/api/unsubscribe"):
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(path).query)
            email = params.get("email", [""])[0].strip().lower()
            if email:
                subs = load_json(SUBSCRIBERS_FILE)
                unsub = load_json(UNSUBSCRIBED_FILE)
                subs = [s for s in subs if s["email"] != email]
                unsub.append({"email": email, "unsubscribed_at": datetime.utcnow().isoformat()})
                save_json(SUBSCRIBERS_FILE, subs)
                save_json(UNSUBSCRIBED_FILE, unsub)
                html = "<h1>Unsubscribed successfully</h1><p>You won't receive further emails from MCP App Directory.</p>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())
                return
        
        # Serve static files
        if path.startswith("/api/"):
            self.send_json({"error": "Not found"}, 404)
            return
        
        # Serve the main HTML for any other path
        serve_static(self, HTML_FILE if path == "/" else os.path.join(BASE_DIR, path.lstrip("/")))
    
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        
        try:
            data = json.loads(body)
        except:
            self.send_json({"success": False, "error": "Invalid JSON"}, 400)
            return
        
        path = self.path
        
        if path == "/api/subscribe":
            email = data.get("email", "").strip().lower()
            if not email or not is_valid_email(email):
                self.send_json({"success": False, "error": "Invalid email"}, 400)
                return
            subs = load_json(SUBSCRIBERS_FILE)
            if any(s["email"] == email for s in subs):
                self.send_json({"success": True, "message": "Already subscribed!"})
                return
            subs.append({"email": email, "subscribed_at": datetime.utcnow().isoformat()})
            save_json(SUBSCRIBERS_FILE, subs)
            send_welcome_email(email)
            self.send_json({"success": True, "message": "Subscribed!"})
            return
        
        if path == "/api/submit-server":
            name = data.get("name", "").strip()
            url = data.get("url", "").strip()
            desc = data.get("description", "").strip()
            cat = data.get("category", "").strip()
            if not name or not url:
                self.send_json({"success": False, "error": "Name and URL required"}, 400)
                return
            subs = load_json(SUBMISSIONS_FILE)
            subs.append({
                "name": name, "url": url, "description": desc,
                "category": cat, "status": "pending",
                "submitted_at": datetime.utcnow().isoformat()
            })
            save_json(SUBMISSIONS_FILE, subs)
            self.send_json({"success": True, "message": "Submitted! We'll review shortly."})
            return
        
        if path == "/api/purchase-skill":
            skill_id = data.get("skill_id", "").strip()
            email = data.get("email", "").strip().lower()
            if not skill_id or not email:
                self.send_json({"success": False, "error": "Skill ID and email required"}, 400)
                return
            skills = load_json(SKILLS_FILE, [])
            skill = next((s for s in skills if s["id"] == skill_id), None)
            if not skill:
                self.send_json({"success": False, "error": "Skill not found"}, 404)
                return
            # For now, record purchase and send install instructions
            purchases_file = os.path.join(BASE_DIR, "purchases.json")
            purchases = load_json(purchases_file, [])
            purchases.append({
                "skill_id": skill_id,
                "skill_name": skill["name"],
                "email": email,
                "price_inr": skill.get("price_inr", 0),
                "purchased_at": datetime.utcnow().isoformat()
            })
            save_json(purchases_file, purchases)
            self.send_json({
                "success": True,
                "message": f"Purchase recorded! Install with: {skill.get('install_cmd', '')}",
                "install_cmd": skill.get("install_cmd", ""),
                "skill_name": skill["name"]
            })
            return
        
        if path == "/api/create-order":
            skill_id = data.get("skill_id", "").strip()
            if not skill_id:
                self.send_json({"success": False, "error": "Skill ID required"}, 400)
                return
            skills = load_json(SKILLS_FILE, [])
            skill = next((s for s in skills if s["id"] == skill_id), None)
            if not skill:
                self.send_json({"success": False, "error": "Skill not found"}, 404)
                return
            
            price_inr = skill.get("price_inr", 0)
            if price_inr <= 0:
                price_inr = 99  # fallback
            
            try:
                import base64
                razorpay_key = os.environ.get("RAZORPAY_KEY_ID", "rzp_test_SuTqU166wx70Tb")
                razorpay_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
                auth = base64.b64encode(f"{razorpay_key}:{razorpay_secret}".encode()).decode()
                
                # Create Razorpay order
                order_data = json.dumps({
                    "amount": price_inr * 100,  # paise
                    "currency": "INR",
                    "receipt": f"skill_{skill_id}_{int(time.time())}",
                    "notes": {"skill_id": skill_id, "skill_name": skill["name"]}
                }).encode()
                
                req = urllib.request.Request(
                    "https://api.razorpay.com/v1/orders",
                    data=order_data,
                    headers={
                        "Authorization": f"Basic {auth}",
                        "Content-Type": "application/json"
                    }
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    order = json.loads(resp.read().decode())
                
                self.send_json({
                    "success": True,
                    "order_id": order["id"],
                    "amount": order["amount"],
                    "currency": order["currency"],
                    "razorpay_key": razorpay_key,
                    "skill_name": skill["name"]
                })
            except Exception as e:
                self.send_json({"success": False, "error": f"Payment error: {str(e)[:100]}"}, 500)
            return
        
        if path == "/api/verify-payment":
            razorpay_order_id = data.get("razorpay_order_id", "")
            razorpay_payment_id = data.get("razorpay_payment_id", "")
            razorpay_signature = data.get("razorpay_signature", "")
            skill_id = data.get("skill_id", "")
            
            if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature, skill_id]):
                self.send_json({"success": False, "error": "Missing payment details"}, 400)
                return
            
            # Verify signature
            import hmac, hashlib
            razorpay_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
            expected_sig = hmac.new(
                razorpay_secret.encode(),
                f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
                hashlib.sha256
            ).hexdigest()
            
            if expected_sig != razorpay_signature:
                self.send_json({"success": False, "error": "Invalid signature"}, 400)
                return
            
            # Payment verified - get skill details
            skills = load_json(SKILLS_FILE, [])
            skill = next((s for s in skills if s["id"] == skill_id), None)
            
            # Record purchase
            purchases_file = os.path.join(BASE_DIR, "purchases.json")
            purchases = load_json(purchases_file, [])
            purchases.append({
                "skill_id": skill_id,
                "skill_name": skill["name"] if skill else "Unknown",
                "order_id": razorpay_order_id,
                "payment_id": razorpay_payment_id,
                "price_inr": skill["price_inr"] if skill else 0,
                "purchased_at": datetime.utcnow().isoformat()
            })
            save_json(purchases_file, purchases)
            
            self.send_json({
                "success": True,
                "install_cmd": skill["install_cmd"] if skill else "",
                "skill_name": skill["name"] if skill else "Unknown"
            })
            return
        
        self.send_json({"success": False, "error": "Not found"}, 404)
    
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def log_message(self, format, *args):
        pass  # Suppress default logging

if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"MCP App Directory server running on port {PORT}")
    print(f"Stats: {len(load_json(LISTINGS_FILE, []))} servers")
    server.serve_forever()
