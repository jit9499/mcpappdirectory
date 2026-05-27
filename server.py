#!/usr/bin/env python3
"""
MCP App Directory — Combined Server v2.0
Serves static site, API, user accounts, Cashfree payments, admin dashboard.
Runs on port 8082, proxied through Traefik on 443.
"""

import http.server
import json
import os
import re
import subprocess
import urllib.request
import time
import sqlite3
import hashlib
import secrets
import hmac
from datetime import datetime
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "mcpapp.db")
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")
SKILLS_FILE = os.path.join(BASE_DIR, "skills_data.json")
SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "subscribers.json")
SUBMISSIONS_FILE = os.path.join(BASE_DIR, "submissions.json")
UNSUBSCRIBED_FILE = os.path.join(BASE_DIR, "unsubscribed.json")
HTML_FILE = os.path.join(BASE_DIR, "index.html")
PORT = 8082

# ── Database Setup ──────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            skill_id TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            order_id TEXT,
            payment_id TEXT,
            amount_paise INTEGER,
            status TEXT DEFAULT 'completed',
            purchased_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_id TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            buyer_email TEXT,
            amount_paise INTEGER,
            gateway TEXT DEFAULT 'cashfree',
            order_id TEXT,
            payment_id TEXT,
            status TEXT DEFAULT 'completed',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Create admin user if not exists
    admin_email = "jit9499@gmail.com"
    c.execute("SELECT id FROM users WHERE email = ?", (admin_email,))
    if not c.fetchone():
        # Use bcrypt for admin password - default is a random one, JR sets it via /admin
        import bcrypt
        default_pw = "admin" + secrets.token_hex(4)
        pw_hash = bcrypt.hashpw(default_pw.encode(), bcrypt.gensalt()).decode()
        c.execute("INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)",
                  (admin_email, pw_hash, "JR"))
        print(f"⚠️  Admin created. Email: {admin_email}, Temp password: {default_pw}")
    conn.commit()
    conn.close()

init_db()

# ── Helper Functions ────────────────────────────────────────────────────

def get_db():
    return sqlite3.connect(DB_PATH)

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

def get_user_from_token(token):
    if not token:
        return None
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT u.id, u.email, u.name FROM users u
                 JOIN sessions s ON u.id = s.user_id
                 WHERE s.token = ?""", (token,))
    user = c.fetchone()
    conn.close()
    if user:
        return {"id": user[0], "email": user[1], "name": user[2]}
    return None

def send_email(to, subject, body):
    """Send email via Hermes email sender script."""
    try:
        script = os.path.expanduser("~/.hermes/scripts/hermes_email_sender.py")
        if os.path.exists(script):
            result = subprocess.run(
                ["python3", script, "--to", to, "--subject", subject, "--body", body],
                capture_output=True, text=True, timeout=30
            )
            return result.returncode == 0
    except:
        pass
    return False

def send_purchase_receipt(email, skill_name, amount_inr, install_cmd):
    subject = f"✅ Your Purchase: {skill_name} — MCP App Directory"
    body = f"""Thanks for purchasing {skill_name}!

Amount paid: ₹{amount_inr}

Install command:
{install_cmd}

For any issues, reply to this email.

— MCP App Directory
https://mcpappdirectory.com
"""
    return send_email(email, subject, body)

def send_welcome_email(email):
    subject = "Welcome to MCP App Directory! 🚀"
    body = f"""Thanks for subscribing to MCP App Directory!

You'll get the best MCP servers delivered to your inbox weekly.

— MCP App Directory Team
https://mcpappdirectory.com

Unsubscribe: https://mcpappdirectory.com/api/unsubscribe?email={email}"""
    return send_email(email, subject, body)

def serve_static(handler, path):
    if path == "/":
        path = HTML_FILE
    if not os.path.exists(path):
        handler.send_json({"error": "Not found"}, 404)
        return
    ext = os.path.splitext(path)[1].lower()
    content_types = {
        ".html": "text/html", ".css": "text/css", ".js": "application/javascript",
        ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
        ".svg": "image/svg+xml", ".ico": "image/x-icon",
    }
    with open(path, "rb") as f:
        content = f.read()
    handler.send_response(200)
    handler.send_header("Content-Type", content_types.get(ext, "text/plain"))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-cache, must-revalidate")
    handler.end_headers()
    handler.wfile.write(content)

# ── Razorpay Config ──────────────────────────────────────────────────────
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "rzp_test_SuVirWwOV6Loq4")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "ahq5aBUhdKNlR5XgWMZe1YAV")

def razorpay_auth():
    import base64
    return base64.b64encode(f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}".encode()).decode()

# ── HTTP Handler ────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path

        # ── API Endpoints ──
        if path == "/api/servers":
            servers = load_json(LISTINGS_FILE, [])
            page = int(self.headers.get("X-Page", 1))
            per_page = 50
            start = (page - 1) * per_page
            end = start + per_page
            self.send_json({"count": len(servers), "page": page, "servers": servers[start:end]})
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
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM users")
            user_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM sales")
            sales_count = c.fetchone()[0]
            c.execute("SELECT COALESCE(SUM(amount_paise), 0) FROM sales WHERE status='completed'")
            total_revenue_paise = c.fetchone()[0]
            conn.close()

            cats = {}
            for s in servers:
                c = s.get("category", "Other")
                cats[c] = cats.get(c, 0) + 1

            self.send_json({
                "servers": len(servers),
                "skills": len(skills),
                "subscribers": len(subs),
                "categories": len(cats),
                "users": user_count,
                "sales": sales_count,
                "total_revenue_inr": total_revenue_paise / 100,
                "category_breakdown": sorted(cats.items(), key=lambda x: -x[1])
            })
            return

        if path == "/api/subscribers/count":
            subs = load_json(SUBSCRIBERS_FILE, [])
            self.send_json({"count": len(subs)})
            return

        if path == "/api/me":
            token = self.get_cookie("session")
            user = get_user_from_token(token)
            if user:
                self.send_json({"logged_in": True, "email": user["email"], "name": user["name"]})
            else:
                self.send_json({"logged_in": False})
            return

        if path == "/api/my-purchases":
            token = self.get_cookie("session")
            user = get_user_from_token(token)
            if not user:
                self.send_json({"error": "Not logged in"}, 401)
                return
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT skill_id, skill_name, amount_paise, purchased_at FROM purchases WHERE user_id=? ORDER BY purchased_at DESC", (user["id"],))
            purchases = [{"skill_id": r[0], "skill_name": r[1], "amount_inr": r[2]/100 if r[2] else 0, "purchased_at": r[3]} for r in c.fetchall()]
            conn.close()
            self.send_json({"purchases": purchases})
            return

        if path == "/api/admin/sales":
            token = self.get_cookie("session")
            user = get_user_from_token(token)
            if not user or user["email"] != "jit9499@gmail.com":
                self.send_json({"error": "Unauthorized"}, 403)
                return
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT skill_name, buyer_email, amount_paise, gateway, status, created_at FROM sales ORDER BY created_at DESC LIMIT 100")
            sales = [{"skill_name": r[0], "buyer_email": r[1], "amount_inr": r[2]/100 if r[2] else 0, "gateway": r[3], "status": r[4], "date": r[5]} for r in c.fetchall()]
            c.execute("SELECT COALESCE(SUM(amount_paise), 0) FROM sales WHERE status='completed'")
            total = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM sales WHERE status='completed'")
            count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM users")
            users = c.fetchone()[0]
            conn.close()
            self.send_json({"sales": sales, "total_revenue_inr": total/100, "total_sales": count, "total_users": users})
            return

        if path.startswith("/api/unsubscribe"):
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

        # Serve static / admin page
        if path.startswith("/api/"):
            self.send_json({"error": "Not found"}, 404)
            return

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

        # ── User Auth ──
        if path == "/api/register":
            email = data.get("email", "").strip().lower()
            password = data.get("password", "")
            name = data.get("name", "").strip()
            if not email or not is_valid_email(email):
                self.send_json({"success": False, "error": "Valid email required"}, 400)
                return
            if len(password) < 6:
                self.send_json({"success": False, "error": "Password must be 6+ characters"}, 400)
                return
            import bcrypt
            pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            conn = get_db()
            c = conn.cursor()
            try:
                c.execute("INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)",
                          (email, pw_hash, name))
                conn.commit()
                user_id = c.lastrowid
                # Auto-login
                token = secrets.token_hex(32)
                c.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
                conn.commit()
                conn.close()
                self.send_json({"success": True, "token": token, "email": email, "name": name})
            except sqlite3.IntegrityError:
                conn.close()
                self.send_json({"success": False, "error": "Email already registered"}, 400)
            return

        if path == "/api/login":
            email = data.get("email", "").strip().lower()
            password = data.get("password", "")
            if not email or not password:
                self.send_json({"success": False, "error": "Email and password required"}, 400)
                return
            import bcrypt
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT id, password_hash, name FROM users WHERE email = ?", (email,))
            row = c.fetchone()
            if row and bcrypt.checkpw(password.encode(), row[1].encode()):
                token = secrets.token_hex(32)
                c.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, row[0]))
                conn.commit()
                conn.close()
                self.send_json({"success": True, "token": token, "email": email, "name": row[2]})
            else:
                conn.close()
                self.send_json({"success": False, "error": "Invalid email or password"}, 401)
            return

        if path == "/api/logout":
            token = self.get_cookie("session")
            if token:
                conn = get_db()
                c = conn.cursor()
                c.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
                conn.close()
            self.send_json({"success": True})
            return

        if path == "/api/update-admin-password":
            token = self.get_cookie("session")
            user = get_user_from_token(token)
            if not user or user["email"] != "jit9499@gmail.com":
                self.send_json({"success": False, "error": "Unauthorized"}, 403)
                return
            new_pw = data.get("password", "")
            if len(new_pw) < 6:
                self.send_json({"success": False, "error": "Password must be 6+ characters"}, 400)
                return
            import bcrypt
            pw_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
            conn = get_db()
            c = conn.cursor()
            c.execute("UPDATE users SET password_hash = ? WHERE email = ?", (pw_hash, user["email"]))
            conn.commit()
            conn.close()
            self.send_json({"success": True, "message": "Admin password updated"})
            return

        # ── Subscribe ──
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

        # ── Submit Server ──
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

        # ── Razorpay Order Creation ──
        if path == "/api/create-order":
            skill_id = data.get("skill_id", "").strip()
            token = self.get_cookie("session")
            user = get_user_from_token(token)
            
            if not skill_id:
                self.send_json({"success": False, "error": "Skill ID required"}, 400)
                return
            
            skills = load_json(SKILLS_FILE, [])
            skill = next((s for s in skills if s["id"] == skill_id), None)
            if not skill:
                self.send_json({"success": False, "error": "Skill not found"}, 404)
                return

            price_inr = skill.get("price_inr", 99)
            receipt = f"skill_{skill_id}_{int(time.time())}"

            try:
                # Create order with Razorpay
                order_data = json.dumps({
                    "amount": price_inr * 100,  # paise
                    "currency": "INR",
                    "receipt": receipt,
                    "notes": {"skill_id": skill_id, "skill_name": skill["name"]}
                }).encode()

                req = urllib.request.Request(
                    "https://api.razorpay.com/v1/orders",
                    data=order_data,
                    headers={
                        "Authorization": f"Basic {razorpay_auth()}",
                        "Content-Type": "application/json"
                    }
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    rzp_response = json.loads(resp.read().decode())

                self.send_json({
                    "success": True,
                    "order_id": rzp_response["id"],
                    "amount": rzp_response["amount"],
                    "currency": rzp_response["currency"],
                    "razorpay_key": RAZORPAY_KEY_ID,
                    "skill_name": skill["name"]
                })
            except urllib.error.HTTPError as e:
                error_body = e.read().decode() if e.fp else str(e)
                self.send_json({"success": False, "error": f"Razorpay error: {error_body[:200]}"}, 500)
            except Exception as e:
                self.send_json({"success": False, "error": f"Payment error: {str(e)[:200]}"}, 500)
            return

        # ── Verify Payment (Razorpay HMAC-SHA256) ──
        if path == "/api/verify-payment":
            razorpay_order_id = data.get("razorpay_order_id", "")
            razorpay_payment_id = data.get("razorpay_payment_id", "")
            razorpay_signature = data.get("razorpay_signature", "")
            skill_id = data.get("skill_id", "")
            
            if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature, skill_id]):
                self.send_json({"success": False, "error": "Missing payment details"}, 400)
                return

            # Verify HMAC-SHA256 signature
            import hmac, hashlib
            expected_sig = hmac.new(
                RAZORPAY_KEY_SECRET.encode(),
                f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
                hashlib.sha256
            ).hexdigest()

            if expected_sig != razorpay_signature:
                self.send_json({"success": False, "error": "Invalid payment signature"}, 400)
                return

            # Signature verified — payment is legit
            skills = load_json(SKILLS_FILE, [])
            skill = next((s for s in skills if s["id"] == skill_id), None)

            token = self.get_cookie("session")
            user = get_user_from_token(token)
            amount_paise = (skill["price_inr"] * 100) if skill else 0

            # Record in DB
            conn = get_db()
            c = conn.cursor()
            if user:
                c.execute("""INSERT INTO purchases (user_id, skill_id, skill_name, order_id, payment_id, amount_paise)
                             VALUES (?, ?, ?, ?, ?, ?)""",
                          (user["id"], skill_id, skill["name"] if skill else "Unknown",
                           razorpay_order_id, razorpay_payment_id, amount_paise))
            c.execute("""INSERT INTO sales (skill_id, skill_name, buyer_email, amount_paise, gateway, order_id, payment_id)
                         VALUES (?, ?, ?, ?, 'razorpay', ?, ?)""",
                      (skill_id, skill["name"] if skill else "Unknown",
                       user["email"] if user else "guest@razorpay",
                       amount_paise, razorpay_order_id, razorpay_payment_id))
            conn.commit()
            conn.close()

            # Send receipt email
            if user and skill:
                send_purchase_receipt(user["email"], skill["name"], amount_paise // 100,
                                     skill.get("install_cmd", ""))

            self.send_json({
                "success": True,
                "install_cmd": skill["install_cmd"] if skill else "",
                "skill_name": skill["name"] if skill else "Unknown"
            })
            return

        # ── Payment Return (for redirect-based gateways) ──
        if path == "/api/payment-return":
            order_id = data.get("order_id", "") or self.headers.get("X-Order-Id", "")
            if order_id:
                html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
                <title>Payment Status — MCP App Directory</title>
                <meta http-equiv="refresh" content="3;url=https://mcpappdirectory.com/#purchases">
                <style>body{{font-family:sans-serif;background:#0a0a0f;color:#e8e8f0;display:flex;justify-content:center;align-items:center;height:100vh;text-align:center;}}
                .card{{background:#14141f;padding:40px;border-radius:12px;border:1px solid #2a2a3f;}}
                h1{{color:#22c55e;}} p{{color:#8888a0;}}</style></head><body>
                <div class="card"><h1>✅ Payment Successful!</h1>
                <p>Redirecting to your purchases...</p>
                <p><a href="https://mcpappdirectory.com/#purchases" style="color:#6366f1;">Click here if not redirected</a></p></div></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())
                return
            self.send_json({"success": False, "error": "No order ID"})
            return

        self.send_json({"success": False, "error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def get_cookie(self, name):
        cookie_header = self.headers.get("Cookie", "")
        for cookie in cookie_header.split("; "):
            if cookie.startswith(name + "="):
                return cookie[len(name)+1:]
        return None

    def log_message(self, format, *args):
        pass  # Suppress default logging

if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"MCP App Directory v2.0 running on port {PORT}")
    print(f"Razorpay: {'LIVE' if 'live' in RAZORPAY_KEY_ID else 'TEST'} mode")
    print(f"Database: {DB_PATH}")
    server.serve_forever()
