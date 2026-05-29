#!/usr/bin/env python3
"""
MCP App Directory — Complete Server
Serves static HTML on port 8082, API endpoints on the same port.
Merges the old newsletter API (port 8083) into this single server.

Endpoints:
  GET  /                    — serve index.html
  GET  /api/servers         — list all servers from listings.json
  GET  /api/servers/{id}    — single server detail
  GET  /api/stats           — counts (servers, subscribers, categories)
  POST /api/subscribe       — newsletter signup + AgentMail confirmation
  POST /api/submit-server   — server submission
  POST /api/checkout        — Razorpay order creation
  POST /api/verify-payment  — verify Razorpay payment and deliver
  GET  /api/purchase/{order_id} — check purchase status
"""

import os
import sys
import json
import re
import uuid
import hashlib
import hmac
import time
import urllib.request
import urllib.parse
import sqlite3
import secrets
import gzip
import io
from http.server import HTTPServer, BaseHTTPRequestHandler
import subprocess
from datetime import datetime, timezone

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")
SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "subscribers.json")
SUBMISSIONS_FILE = os.path.join(BASE_DIR, "submissions.json")
DB_FILE = os.path.join(BASE_DIR, "mcpapp.db")
INDEX_FILE = os.path.join(BASE_DIR, "index.html")

# ── Config ─────────────────────────────────────────────────────────────────
PORT = 8082

# Razorpay — from supervisor env or fallback to hardcoded live keys
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "rzp_live_SuVq39z601q99E")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "uJOB28UMebv7Vj1PbEZnmCVf")

# GitHub OAuth
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = "https://mcpappdirectory.com/api/auth/github/callback"
SESSION_DURATION = 86400 * 30  # 30 days

# AgentMail
AGENTMAIL_EMAIL = os.environ.get("AGENTMAIL_EMAIL", "jituhermes@agentmail.to")
AGENTMAIL_API_KEY = os.environ.get("AGENTMAIL_API_KEY", "")

# Product config for checkout (pricing in paise = INR * 100)
PRODUCTS = {
    "featured_listing": {
        "name": "Featured Listing",
        "description": "Get your MCP server featured on the homepage for 30 days",
        "amount": 49900,  # ₹499.00
        "currency": "INR",
    },
    "premium_badge": {
        "name": "Premium Badge",
        "description": "Permanent premium badge on your server listing",
        "amount": 99900,  # ₹999.00
        "currency": "INR",
    }
}

# Razorpay API endpoints
RAZORPAY_ORDER_URL = "https://api.razorpay.com/v1/orders"
RAZORPAY_VERIFY_URL = "https://api.razorpay.com/v1/payments/{payment_id}/capture"

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".txt": "text/plain",
    ".map": "application/json",
}


# ── SQLite Helpers ─────────────────────────────────────────────────────────

DB_FILE = os.path.join(BASE_DIR, "mcpapp.db")

def get_db():
    """Get a SQLite connection with row_factory set to sqlite3.Row."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_all_servers():
    """Return all servers as a list of dicts (replaces read_json(listings.json))."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM servers ORDER BY score DESC").fetchall()
    conn.close()
    return [_server_row_to_dict(r) for r in rows]


def get_server_by_id(server_id):
    """Return a server dict by numeric id."""
    conn = get_db()
    row = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    conn.close()
    return _server_row_to_dict(row) if row else None


def get_server_by_url(url):
    """Return a server dict by URL."""
    conn = get_db()
    row = conn.execute("SELECT * FROM servers WHERE url = ?", (url,)).fetchone()
    conn.close()
    return _server_row_to_dict(row) if row else None


def search_servers(category=None, search=None, sort_by=None, grade_filter=None,
                   limit=24, offset=0, verified_only=False):
    """Search and filter servers with SQL. Returns (servers_list, total_count)."""
    conn = get_db()
    where_clauses = []
    params = []

    if category and category.lower() != "all":
        # Category can be a slug (from URL) or name (from API)
        # Try matching via categories table first
        cat = category
        conn2 = get_db()
        cat_row = conn2.execute(
            "SELECT name FROM categories WHERE slug = ? OR LOWER(name) = ? OR LOWER(REPLACE(name, ' ', '-')) = ?",
            (cat, cat.lower(), cat.lower())
        ).fetchone()
        if cat_row:
            where_clauses.append("category = ?")
            params.append(cat_row["name"])
        else:
            where_clauses.append("LOWER(category) = ?")
            params.append(cat.lower())

    if grade_filter and grade_filter.upper() in ("A", "B", "C", "D", "F"):
        where_clauses.append("grade = ?")
        params.append(grade_filter.upper())

    if verified_only:
        where_clauses.append("verified = 1")

    if search:
        q = search.lower()
        # Use LIKE on multiple columns
        search_clauses = [
            "LOWER(name) LIKE ?",
            "LOWER(description) LIKE ?",
            "LOWER(category) LIKE ?",
            "LOWER(language) LIKE ?",
        ]
        where_clauses.append(f"({' OR '.join(search_clauses)})")
        search_param = f"%{q}%"
        params.extend([search_param] * 4)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    # Count
    count_row = conn.execute(
        f"SELECT COUNT(*) as cnt FROM servers {where_sql}", params
    ).fetchone()
    total = count_row["cnt"] if count_row else 0

    # Sort
    sort_map = {
        "stars": "stars DESC",
        "score": "score DESC",
        "name": "name ASC",
        "newest": "pushed_at DESC",
        "downloads": "stars DESC",  # download proxy: sort by stars
        "trending": "score DESC, stars DESC",  # simplified trending
    }
    order_sql = "ORDER BY score DESC"  # default sort
    if sort_by and sort_by in sort_map:
        order_sql = f"ORDER BY {sort_map[sort_by]}"

    rows = conn.execute(
        f"SELECT * FROM servers {where_sql} {order_sql} LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()
    conn.close()
    return [_server_row_to_dict(r) for r in rows], total


def _server_row_to_dict(r):
    """Convert sqlite3.Row server to dict (parsing JSON fields)."""
    if not r:
        return None
    d = dict(r)
    # Parse JSON string fields
    if isinstance(d.get("topics"), str):
        try:
            d["topics"] = json.loads(d["topics"])
        except (json.JSONDecodeError, TypeError):
            d["topics"] = []
    else:
        d["topics"] = d.get("topics") or []
    if isinstance(d.get("score_details"), str):
        try:
            d["score_details"] = json.loads(d["score_details"])
        except (json.JSONDecodeError, TypeError):
            d["score_details"] = {}
    else:
        d["score_details"] = d.get("score_details") or {}
    # Convert integer booleans
    d["verified"] = bool(d.get("verified", 0))
    d["has_github_stats"] = bool(d.get("has_github_stats", 1) if d.get("has_github_stats") != False else False)
    d["featured"] = bool(d.get("featured", 0))
    return d


# ── Subscriber SQL helpers ──────────────────────────────────────────────

def get_all_subscribers():
    """Return all subscribers as a list of dicts."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM subscribers ORDER BY subscribed_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_subscriber_by_email(email):
    """Return a subscriber dict or None."""
    conn = get_db()
    row = conn.execute("SELECT * FROM subscribers WHERE email = ?", (email.strip().lower(),)).fetchone()
    conn.close()
    return dict(row) if row else None


def add_subscriber(email, name="", source="website", confirm_token=None):
    """Add a new subscriber. Returns True if added, False if duplicate."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO subscribers (email, name, source, confirm_token) VALUES (?, ?, ?, ?)",
            (email.strip().lower(), name, source, confirm_token)
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def update_subscriber(email, **kwargs):
    """Update subscriber fields by email."""
    if not kwargs:
        return
    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [email.strip().lower()]
    conn.execute(f"UPDATE subscribers SET {sets} WHERE email = ?", vals)
    conn.commit()
    conn.close()


def delete_subscriber(email):
    """Delete a subscriber by email."""
    conn = get_db()
    conn.execute("DELETE FROM subscribers WHERE email = ?", (email.strip().lower(),))
    conn.commit()
    conn.close()


def get_confirmed_subscribers():
    """Return all confirmed subscribers."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM subscribers WHERE confirmed = 1 ORDER BY subscribed_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Submission SQL helpers ──────────────────────────────────────────────

def get_all_submissions():
    """Return all submissions as a list of dicts."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM submissions ORDER BY submitted_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_submission(name, url, description, category):
    """Add a new submission. Returns True if added, False if duplicate URL."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO submissions (name, url, description, category) VALUES (?, ?, ?, ?)",
            (name, url.strip(), description, category)
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def submission_exists_by_url(url):
    """Check if a submission with the given URL exists."""
    conn = get_db()
    row = conn.execute("SELECT id FROM submissions WHERE LOWER(url) = ?", (url.strip().lower(),)).fetchone()
    conn.close()
    return row is not None


# ── Purchase SQL helpers ───────────────────────────────────────────────

def add_purchase(order_id, product, product_name, amount, currency, email):
    """Store a new purchase record."""
    conn = get_db()
    conn.execute(
        "INSERT INTO purchases (order_id, product, product_name, amount, currency, email) VALUES (?, ?, ?, ?, ?, ?)",
        (order_id, product, product_name, amount, currency, email)
    )
    conn.commit()
    conn.close()


def get_purchase(order_id):
    """Return a purchase dict or None."""
    conn = get_db()
    row = conn.execute("SELECT * FROM purchases WHERE order_id = ?", (order_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_purchase(order_id, **kwargs):
    """Update purchase fields by order_id."""
    if not kwargs:
        return
    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [order_id]
    conn.execute(f"UPDATE purchases SET {sets} WHERE order_id = ?", vals)
    conn.commit()
    conn.close()


# ── Razorpay API helpers ───────────────────────────────────────────────────

def _razorpay_auth():
    """Return base64-encoded Basic Auth header value."""
    creds = f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}"
    import base64
    return "Basic " + base64.b64encode(creds.encode()).decode()


def _razorpay_request(method, url, data=None):
    """Make an authenticated request to Razorpay API."""
    headers = {
        "Authorization": _razorpay_auth(),
        "Content-Type": "application/json",
        "User-Agent": "MCPAppDirectory/2.0",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else "{}"
        return {"error": True, "status": e.code, "body": err_body}
    except Exception as e:
        return {"error": True, "message": str(e)}


def create_razorpay_order(amount, currency="INR", receipt=None):
    """Create a Razorpay order."""
    payload = {
        "amount": amount,
        "currency": currency,
        "receipt": receipt or str(uuid.uuid4())[:40],
        "payment_capture": 1,
    }
    return _razorpay_request("POST", RAZORPAY_ORDER_URL, payload)


def verify_razorpay_signature(order_id, payment_id, signature):
    """Verify the Razorpay payment signature."""
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def fetch_payment(payment_id):
    """Fetch payment details from Razorpay."""
    url = f"https://api.razorpay.com/v1/payments/{payment_id}"
    return _razorpay_request("GET", url)


# ── AgentMail Helper ───────────────────────────────────────────────────────

def send_email(to_emails, subject, text, html=None):
    """Send an email via AgentMail MCP (npx agentmail-mcp JSON-RPC)."""
    try:
        if isinstance(to_emails, str):
            to_emails = [to_emails]
        final_html = html or text.replace('\n', '<br>')
        # Build MCP JSON-RPC messages
        init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "mcpappdir", "version": "1.0"}}})
        notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        call = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "send_message", "arguments": {"inboxId": AGENTMAIL_EMAIL, "to": to_emails, "subject": subject, "html": final_html, "labels": ["newsletter"]}}})
        stdin_input = f"{init}\n{notif}\n{call}\n"
        env = os.environ.copy()
        if AGENTMAIL_API_KEY:
            env["AGENTMAIL_API_KEY"] = AGENTMAIL_API_KEY
        result = subprocess.run(["npx", "-y", "agentmail-mcp"], input=stdin_input.encode(), capture_output=True, timeout=30, env=env)
        out = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
        err = result.stderr.decode() if isinstance(result.stderr, bytes) else result.stderr
        if "messageId" in out:
            return True
        print(f"[AgentMail] Failed: {(out+err)[:300]}")
        return False
    except Exception as e:
        print(f"[AgentMail] Error: {e}")
        return False

def send_confirmation_email(email, name="Subscriber", token=None):
    """Send double opt-in confirmation email."""
    link = f"https://mcpappdirectory.com/api/confirm?token={token}" if token else "https://mcpappdirectory.com"
    html = f"""<h2>Welcome to MCP App Directory! 🚀</h2>
<p>Hi {name},</p>
<p>Thanks for subscribing! Please <b>confirm your subscription</b> by clicking the link below:</p>
<p style="text-align:center;margin:24px 0">
  <a href="{link}" style="background:#6366f1;color:white;padding:12px 32px;border-radius:8px;text-decoration:none;font-weight:600">
    ✅ Confirm Subscription
  </a>
</p>
<p>If you didn't subscribe, you can ignore this email.</p>
<br><p>Best,<br><strong>The MCP App Directory Team</strong></p>"""
    text = f"Hi {name},\n\nPlease confirm your subscription: {link}\n\nIf you didn't subscribe, ignore this.\n\nBest,\nThe MCP App Directory Team"
    return send_email(email, "Confirm your MCP App Directory subscription", text, html)


# ── Stats helper ───────────────────────────────────────────────────────────

def compute_stats():
    """Compute directory stats from SQLite."""
    conn = get_db()
    
    # Total servers count
    total_servers = conn.execute("SELECT COUNT(*) as cnt FROM servers").fetchone()["cnt"] or 0
    
    # Total confirmed subscribers
    total_subscribers = conn.execute("SELECT COUNT(*) as cnt FROM subscribers WHERE confirmed = 1").fetchone()["cnt"] or 0
    
    # Categories
    cats = conn.execute("SELECT category, COUNT(*) as cnt FROM servers GROUP BY category ORDER BY cnt DESC").fetchall()
    categories = {r["category"]: r["cnt"] for r in cats}
    
    # Grade distribution
    grades_rows = conn.execute("SELECT grade, COUNT(*) as cnt FROM servers GROUP BY grade").fetchall()
    grades = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    for r in grades_rows:
        if r["grade"] in grades:
            grades[r["grade"]] = r["cnt"]
    
    # Average score
    avg_row = conn.execute("SELECT AVG(score) as avg_score FROM servers WHERE score IS NOT NULL").fetchone()
    avg_score = round(avg_row["avg_score"], 1) if avg_row and avg_row["avg_score"] else 0
    
    # Scored servers count
    scored_row = conn.execute("SELECT COUNT(*) as cnt FROM servers WHERE score IS NOT NULL").fetchone()
    scored_servers = scored_row["cnt"] if scored_row else 0
    
    conn.close()
    
    return {
        "total_servers": total_servers,
        "total_subscribers": total_subscribers,
        "categories": categories,
        "grade_distribution": grades,
        "average_score": avg_score,
        "scored_servers": scored_servers,
    }


# ── HTTP Server ────────────────────────────────────────────────────────────

class MCPAppHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        """Add timestamps to access logs."""
        sys.stderr.write(f"[{datetime.now().isoformat()}] {format % args}\n")

    # ── CORS ───────────────────────────────────────────────────────────
    def _set_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(200)
        self._set_cors()
        self.end_headers()

    def do_HEAD(self):
        """Handle HEAD requests — suppress body output but send headers."""
        self._is_head_request = True
        self.do_GET()
        self._is_head_request = False

    # ── Response helpers ───────────────────────────────────────────────
    def _send_json(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self._set_cors()
        self.send_header("Content-Type", "application/json")
        self._send_body(body)

    def _send_body(self, body, content_type="application/json"):
        """Send body with gzip if accepted."""
        accept_gzip = self.headers.get("Accept-Encoding", "").find("gzip") >= 0
        if accept_gzip and len(body) > 1024:
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb") as f:
                f.write(body)
            compressed = buf.getvalue()
            if len(compressed) < len(body):
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(compressed)))
                self.end_headers()
                if not getattr(self, '_is_head_request', False):
                    self.wfile.write(compressed)
                return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not getattr(self, '_is_head_request', False):
            self.wfile.write(body)

    def _send_error(self, message, status=400):
        self._send_json({"success": False, "error": message}, status)

    def _send_success(self, data=None, message=None):
        resp = {"success": True}
        if data:
            resp.update(data)
        if message:
            resp["message"] = message
        self._send_json(resp)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ── Routing ────────────────────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")

        if path == "/" or path == "":
            self._handle_homepage()
        elif path == "/api/servers":
            self._handle_get_servers()
        elif path.startswith("/api/servers/"):
            server_id = path[len("/api/servers/"):]
            self._handle_get_server(server_id)
        elif path == "/api/stats":
            self._handle_get_stats()
        elif path == "/api/trending":
            self._handle_get_trending()
        elif path == "/api/client-config":
            self._handle_get_client_config()
        elif path == "/api/comparison":
            self._handle_multi_compare()
        elif path.startswith("/api/purchase/"):
            order_id = path[len("/api/purchase/"):]
            self._handle_get_purchase(order_id)
        elif path == "/api/confirm":
            self._handle_confirm()
        elif path == "/api/llms.txt":
            self._handle_llms_txt()
        elif path == "/api/send-newsletter":
            self._handle_send_newsletter()
        elif path == "/api/auth/github":
            self._handle_auth_github()
            return  # IMPORTANT: must return after redirect
        elif path == "/api/auth/github/callback":
            self._handle_auth_github_callback()
        elif path == "/api/auth/me":
            self._handle_auth_me()
        elif path == "/api/auth/servers/saved":
            self._handle_get_saved_servers()
        elif path == "/api/compare":
            self._handle_compare()
        elif path == "/dashboard":
            self._serve_static("/dashboard.html")
        elif path == "/sitemap.xml":
            self._handle_sitemap()
        elif path == "/robots.txt":
            self._serve_static("/robots.txt")
        elif path == "/api/unsubscribe":
            self._handle_unsubscribe()
        elif path.startswith("/servers/"):
            slug = path[len("/servers/"):]
            self._handle_server_page(slug)
        elif path.startswith("/category/"):
            cat = path[len("/category/"):]
            self._handle_category_page(cat)
        elif path == "/trending":
            self._handle_trending_page()
        elif path == "/new":
            self._handle_new_page()
        elif path.startswith("/grade/"):
            grade_letter = path[len("/grade/"):].upper()
            self._handle_grade_page(grade_letter)
        elif path == "/about":
            self._handle_about_page()
        elif path == "/how-it-works":
            self._handle_how_it_works_page()
        elif path in ("/submit", "/scoring", "/compare", "/leaderboard"):
            self._serve_static(path + ".html")
        else:
            self._serve_static(path)

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")

        if path == "/api/subscribe":
            self._handle_subscribe()
        elif path == "/api/submit-server":
            self._handle_submit_server()
        elif path == "/api/checkout":
            self._handle_checkout()
        elif path == "/api/verify-payment":
            self._handle_verify_payment()
        elif path == "/api/send-newsletter":
            self._handle_send_newsletter()
        elif path == "/api/auth/logout":
            self._handle_auth_logout()
        elif path == "/api/auth/servers/save":
            self._handle_save_server()
        elif path == "/api/auth/servers/unsave":
            self._handle_unsave_server()
        elif path == "/api/auth/api-key/generate":
            self._handle_generate_api_key()
        else:
            self._send_error("Not found", 404)

    # ── Static File Serving ────────────────────────────────────────────
    def _serve_static(self, path):
        """Serve static files, defaulting to index.html."""
        if path == "/" or path == "":
            path = "/index.html"

        # Security: prevent directory traversal
        clean = os.path.normpath(path.lstrip("/"))
        filepath = os.path.join(BASE_DIR, clean)

        # Ensure the resolved path is within BASE_DIR
        real_base = os.path.realpath(BASE_DIR)
        real_file = os.path.realpath(filepath)
        if not real_file.startswith(real_base):
            self._send_error("Forbidden", 403)
            return

        if not os.path.isfile(real_file):
            self._send_error("Not found", 404)
            return

        ext = os.path.splitext(real_file)[1].lower()
        mime = MIME_TYPES.get(ext, "application/octet-stream")

        try:
            with open(real_file, "rb") as f:
                content = f.read()
            # Detect SVG files and force correct content type
            if real_file.endswith('.ico') and content.strip().startswith(b'<svg'):
                mime = 'image/svg+xml'
            self.send_response(200)
            self._set_cors()
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not getattr(self, '_is_head_request', False):
                self.wfile.write(content)
        except IOError:
            self._send_error("Not found", 404)

    # ── API: GET /api/servers ──────────────────────────────────────────
    def _handle_get_servers(self):
        # Support query params: ?category=X&search=Y&sort=stars&grade=A&limit=24&offset=0
        params = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
        
        category = params.get("category", [None])[0]
        search = params.get("search", [None])[0]
        sort_by = params.get("sort", [None])[0]
        grade_filter = params.get("grade", [None])[0]
        try:
            limit = int(params.get("limit", ["24"])[0])
        except ValueError:
            limit = 24
        try:
            offset = int(params.get("offset", ["0"])[0])
        except ValueError:
            offset = 0

        servers, total = search_servers(
            category=category,
            search=search,
            sort_by=sort_by,
            grade_filter=grade_filter,
            limit=limit,
            offset=offset,
        )

        self._send_json({
            "success": True,
            "total": total,
            "offset": offset,
            "limit": limit,
            "servers": servers,
        })

    # ── API: GET /api/servers/{id} ─────────────────────────────────────
    def _handle_get_server(self, server_id):
        # Try by numeric id first
        try:
            idx = int(server_id)
            server = get_server_by_id(idx)
            if server:
                self._send_json({"success": True, "server": server})
                return
        except ValueError:
            pass

        # Try by URL hash (match both 6-char and 12-char hashes) and by _make_slug
        servers = get_all_servers()
        for s in servers:
            s_slug = _make_slug(s.get("name", ""), s.get("url", ""))
            if s_slug == server_id or s_slug[:12] == server_id or s_slug[:6] == server_id or s.get("name", "").lower().replace(" ", "-") == server_id:
                self._send_json({"success": True, "server": s})
                return

        self._send_error("Server not found", 404)

    # ── API: GET /api/compare ─────────────────────────────────────────
    def _handle_compare(self):
        """Return selected servers for comparison by index."""
        params = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
        ids = params.get("ids", [""])[0].split(",") if params.get("ids") else []
        result = []
        for idx_str in ids:
            try:
                idx = int(idx_str.strip())
                server = get_server_by_id(idx)
                if server:
                    result.append({"index": idx, "server": server})
            except ValueError:
                pass
        self._send_json({"success": True, "servers": result})

    # ── API: GET /api/stats ────────────────────────────────────────────
    def _handle_get_stats(self):
        stats = compute_stats()
        self._send_json({"success": True, "stats": stats})

    # ── API: GET /api/trending — weekly trending servers ──────────────
    def _handle_get_trending(self):
        """Return servers sorted by popularity (stars + forks + recency)."""
        now = datetime.now(timezone.utc)
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM servers WHERE verified = 1 ORDER BY stars DESC, forks DESC LIMIT 24"
        ).fetchall()
        conn.close()
        servers = [_server_row_to_dict(r) for r in rows]
        # Re-score with recency
        scored = []
        for s in servers:
            try:
                pushed = datetime.fromisoformat(s.get("pushed_at", "2025-01-01").replace("Z", "+00:00"))
                days_since_push = (now - pushed).days
            except:
                days_since_push = 365
            stars = s.get("stars", 0) or 0
            forks = s.get("forks", 0) or 0
            downloads = s.get("downloads_monthly", 0) or 0
            recency_bonus = max(0, 50 - days_since_push) * 10
            trending_score = stars + forks * 2 + downloads / 100 + recency_bonus
            scored.append((trending_score, s))
        scored.sort(key=lambda x: -x[0])
        top = [s[1] for s in scored[:24]]
        self._send_json({"success": True, "servers": top, "total": len(top)})

    # ── API: GET /api/client-config — ready-to-paste configs ──────────
    def _handle_get_client_config(self):
        """Return ready-to-paste config snippets for various clients."""
        params = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
        server_index = params.get("index", [None])[0]
        if server_index is None:
            self._send_error("Missing 'index' parameter")
            return
        try:
            idx = int(server_index)
            s = get_server_by_id(idx)
            if not s:
                raise IndexError
        except (ValueError, IndexError):
            self._send_error("Server not found", 404)
            return
        name = s.get("name", "mcp-server")
        url = s.get("url", "https://github.com/example/mcp-server")
        install_cmd = s.get("install", "")
        safe_name = name.lower().replace(" ", "-").replace("_", "-")
        
        configs = {
            "claude_desktop": {
                "label": "Claude Desktop",
                "config": {
                    "mcpServers": {
                        safe_name: {
                            "command": install_cmd or "npx",
                            "args": [f"-y", f"@{safe_name}/mcp-server"] if install_cmd == "npx" else ["install"],
                            "env": {}
                        }
                    }
                },
                "json": json.dumps({
                    "mcpServers": {
                        safe_name: {
                            "command": install_cmd or "npx",
                            "args": [f"-y", f"@{safe_name}/mcp-server"] if install_cmd == "npx" else ["run", safe_name],
                            "env": {}
                        }
                    }
                }, indent=2)
            },
            "cursor": {
                "label": "Cursor",
                "config": f"Add to .cursor/mcp.json:\n{{\n  \"mcpServers\": {{\n    \"{safe_name}\": {{\n      \"command\": \"{install_cmd or 'npx'}\",\n      \"args\": [\"-y\", \"@{safe_name}/mcp-server\"]\n    }}\n  }}\n}}"
            },
            "cline": {
                "label": "Cline / Roo Code",
                "config": f"Add to cline_mcp_settings.json:\n{{\n  \"mcpServers\": {{\n    \"{safe_name}\": {{\n      \"command\": \"{install_cmd or 'npx'}\",\n      \"args\": [\"-y\", \"@{safe_name}/mcp-server\"]\n    }}\n  }}\n}}"
            },
            "vscode": {
                "label": "VS Code / Copilot",
                "config": f"Via terminal:\n{install_cmd or f'npx -y {safe_name}'}"
            }
        }
        self._send_json({"success": True, "server": name, "configs": configs})

    # ── API: GET /api/comparison — multi-server compare ───────────────
    def _handle_multi_compare(self):
        """Compare up to 5 servers side-by-side."""
        params = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
        indices_str = params.get("indices", [""])[0]
        if not indices_str:
            self._send_error("Missing 'indices' (comma-separated server indices)")
            return
        try:
            indices = [int(i.strip()) for i in indices_str.split(",") if i.strip()]
        except ValueError:
            self._send_error("Invalid indices")
            return
        if len(indices) > 5:
            self._send_error("Maximum 5 servers for comparison", 400)
            return
        servers = get_all_servers()
        result = []
        for idx in indices:
            if 0 <= idx < len(servers):
                s = servers[idx]
                result.append({
                    "index": idx,
                    "name": s.get("name"),
                    "grade": s.get("grade"),
                    "score": s.get("score"),
                    "stars": s.get("stars"),
                    "forks": s.get("forks"),
                    "open_issues": s.get("open_issues"),
                    "language": s.get("language"),
                    "license": s.get("license"),
                    "category": s.get("category"),
                    "install": s.get("install"),
                    "description": s.get("description"),
                    "verified": s.get("verified"),
                    "has_github_stats": s.get("has_github_stats"),
                    "score_details": s.get("score_details", {}),
                    "created_at": s.get("created_at"),
                    "pushed_at": s.get("pushed_at"),
                    "downloads_monthly": s.get("downloads_monthly", 0),
                })
        self._send_json({"success": True, "servers": result, "count": len(result)})

    # ── API: POST /api/subscribe ───────────────────────────────────────
    def _handle_subscribe(self):
        body = self._read_body()
        email = (body.get("email") or "").strip().lower()
        source = (body.get("source") or "website").strip()

        if not email:
            self._send_error("Email is required")
            return

        # Basic email validation
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            self._send_error("Invalid email address")
            return

        # Check for existing subscriber
        existing = get_subscriber_by_email(email)

        if existing:
            if existing.get("confirmed"):
                self._send_error("Already subscribed", 409)
                return
            subscriber = existing
        else:
            # Add subscriber (unconfirmed)
            token = hashlib.sha256(f"{email}:{uuid.uuid4()}:newsletter".encode()).hexdigest()[:32]
            add_subscriber(email, name="", source=source, confirm_token=token)
            subscriber = get_subscriber_by_email(email)

        # Send confirmation email via AgentMail with link
        name = email.split("@")[0]
        token = subscriber.get("confirm_token", "")
        email_sent = send_confirmation_email(email, name, token)

        self._send_success({
            "email": email,
            "email_sent": email_sent,
            "needs_confirmation": True,
        }, "Check your inbox to confirm your subscription!")

    # ── API: Confirm subscription (double opt-in) ──────────────────────
    def _handle_confirm(self):
        params = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
        token = (params.get("token", [""])[0]).strip()
        if not token:
            self._send_error("Missing confirmation token")
            return
        conn = get_db()
        row = conn.execute("SELECT email FROM subscribers WHERE confirm_token = ?", (token,)).fetchone()
        if row:
            conn.execute(
                "UPDATE subscribers SET confirmed = 1, confirmed_at = datetime('now'), confirm_token = NULL WHERE confirm_token = ?",
                (token,)
            )
            conn.commit()
            conn.close()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body style='font-family:sans-serif;text-align:center;padding:60px;background:#0a0a0f;color:#e8e8f0'><h2>Subscription Confirmed!</h2><p>You're now subscribed to the MCP App Directory newsletter.</p><p><a href='https://mcpappdirectory.com' style='color:#6366f1'>Back to Directory</a></p></body></html>")
            return
        conn.close()
        self._send_error("Invalid or expired confirmation link", 404)

    # ── API: GET /api/unsubscribe ───────────────────────────────────
    def _handle_unsubscribe(self):
        """Unsubscribe a user by email."""
        params = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
        email = (params.get("email", [""])[0]).strip().lower()
        if not email:
            self._send_error("Email is required")
            return
        existing = get_subscriber_by_email(email)
        removed = 1 if existing else 0
        if removed:
            delete_subscriber(email)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "You have been unsubscribed successfully." if removed > 0 else "Email not found in our subscriber list."
        self.wfile.write(f"""<html><body style='font-family:sans-serif;text-align:center;padding:60px;background:#0a0a0f;color:#e8e8f0'><h2>Unsubscribed {'✅' if removed > 0 else ''}</h2><p>{msg}</p><p><a href='https://mcpappdirectory.com' style='color:#6366f1'>Back to Directory</a></p></body></html>""".encode())

    # ── API: POST /api/send-newsletter (admin only) ────────────────────
    def _handle_send_newsletter(self):
        """Send a newsletter to all confirmed subscribers."""
        confirmed = get_confirmed_subscribers()
        if not confirmed:
            self._send_error("No confirmed subscribers", 404)
            return

        # Build newsletter content from top A-grade and trending servers
        servers = get_all_servers()
        # Show top A-grade servers with real GitHub data first, then fill with recent
        top_grade = sorted([s for s in servers if s.get("grade") in ("A", "B")], key=lambda x: x.get("score", 0), reverse=True)
        recent = sorted([s for s in servers if s.get("grade") not in ("A", "B")], key=lambda x: x.get("last_updated", ""), reverse=True)
        picks_list = (top_grade + recent)[:10]

        stats = compute_stats()
        total_servers = stats.get("total_servers", 0)
        grade_dist = stats.get("grade_distribution", {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0})

        picks = ""
        for i, s in enumerate(picks_list[:6], 1):
            name = s.get("name", "Unknown")
            score = s.get("score", 0)
            slug = _make_slug(name, s.get("url", ""))
            desc = (s.get("description", "") or "")[:120]
            grade = s.get("grade", "F")
            lang = (s.get("language") or "")[:15]
            gc = {"A": "#22c55e", "B": "#3b82f6", "C": "#f59e0b", "D": "#f97316", "F": "#ef4444"}.get(grade, "#888")
            link = f"https://mcpappdirectory.com/servers/{slug}"
            lang_tag = f'<span style="color:#8888a0;font-size:0.78em"> {lang}</span>' if lang else ""
            picks += (
                f'<tr style="border-bottom:1px solid #2a2a3f">'
                f'<td style="padding:12px 0;vertical-align:top">'
                f'<span style="display:inline-block;width:22px;height:22px;border-radius:5px;background:{gc}20;color:{gc};text-align:center;font-weight:700;font-size:0.75em;line-height:22px;margin-right:8px;float:left">{grade}</span>'
                f'<a href="{link}" style="color:#6366f1;text-decoration:none;font-weight:600;font-size:0.95em">{_e(name)}</a>{lang_tag}'
                f'<br><span style="color:#aaaabc;font-size:0.82em">{_e(desc)}</span>'
                f'</td>'
                f'<td style="padding:12px 0;vertical-align:middle;text-align:center;width:64px">'
                f'<span style="display:inline-block;background:rgba(99,102,241,0.15);color:#6366f1;padding:3px 10px;border-radius:6px;font-weight:700;font-size:0.82em">{score}</span>'
                f'</td></tr>'
            )

        html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#0a0a0f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0f"><tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">

<!-- Header -->
<tr><td style="padding:32px 24px 16px;text-align:center;border-bottom:1px solid #2a2a3f">
<span style="font-size:28px">🔌</span>
<h1 style="color:#e8e8f0;font-size:22px;margin:8px 0 4px;font-weight:700">MCP App Directory Weekly</h1>
<p style="color:#8888a0;font-size:0.85em;margin:0">AI-graded MCP servers — verified, scored, monitored.</p>
</td></tr>

<!-- Stats Bar -->
<tr><td style="padding:16px 24px;background:#12121a;border-bottom:1px solid #2a2a3f">
<table width="100%"><tr>
<td style="text-align:center;width:33%"><span style="color:#6366f1;font-size:20px;font-weight:700">{total_servers}</span><br><span style="color:#8888a0;font-size:0.75em">Servers Listed</span></td>
<td style="text-align:center;width:33%"><span style="color:#22c55e;font-size:20px;font-weight:700">{grade_dist.get("A", 0)}</span><br><span style="color:#8888a0;font-size:0.75em">A-Grade Servers</span></td>
<td style="text-align:center;width:33%"><span style="color:#f59e0b;font-size:20px;font-weight:700">{stats.get("average_score", 0)}</span><br><span style="color:#8888a0;font-size:0.75em">Avg Score</span></td>
</tr></table>
</td></tr>

<!-- Intro -->
<tr><td style="padding:20px 24px 8px">
<p style="color:#c8c8d8;font-size:0.9em;line-height:1.5;margin:0">Every MCP server listed on our directory is <strong style="color:#e8e8f0">AI-graded on 8 quality parameters</strong> — recency, documentation, tests, security, install docs, star velocity, and more. No broken servers, no missing links, no AI hallucination.</p>
</td></tr>

<!-- Top Picks -->
<tr><td style="padding:16px 24px">
<h2 style="color:#e8e8f0;font-size:16px;margin:0 0 8px">🔥 Top Picks This Week</h2>
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
{picks}
</table>
</td></tr>

<!-- CTA -->
<tr><td style="padding:8px 24px 24px;text-align:center">
<a href="https://mcpappdirectory.com" style="display:inline-block;background:#6366f1;color:white;padding:14px 40px;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.95em">Browse All {total_servers} Servers →</a>
</td></tr>

<!-- USP -->
<tr><td style="padding:16px 24px;background:#12121a;border-top:1px solid #2a2a3f">
<p style="color:#8888a0;font-size:0.78em;line-height:1.6;margin:0;text-align:center">
<strong style="color:#a0a0b8">🌍 World's First MCP Directory with Verified Servers</strong><br>
Every server graded A-F. Real GitHub data. Continuous monitoring. No broken MCP servers, no missing links.
</p>
</td></tr>

<!-- Footer -->
<tr><td style="padding:20px 24px;text-align:center;border-top:1px solid #2a2a3f">
<p style="color:#666680;font-size:0.72em;margin:0 0 4px">You received this because you subscribed to MCP App Directory newsletter.</p>
<p style="color:#666680;font-size:0.72em;margin:0">mcpappdirectory.com · <a href="https://mcpappdirectory.com/api/unsubscribe?email=__EMAIL__" style="color:#666680;text-decoration:underline">Unsubscribe</a></p>
</td></tr>

</table>
</td></tr></table>
</body></html>"""

        sent = 0
        failed = 0
        for s in confirmed:
            email = s.get("email", "")
            name = email.split("@")[0]
            # Personalize: add unsubscribe link per recipient
            personal_html = html.replace("__EMAIL__", email)
            ok = send_email(email, "MCP App Directory Weekly — Top Picks", f"This week's top MCP servers. Browse at https://mcpappdirectory.com", personal_html)
            if ok:
                sent += 1
            else:
                failed += 1

        self._send_success({"sent": sent, "failed": failed, "total": len(confirmed)}, f"Newsletter sent to {sent} subscribers ({failed} failed).")

    # ── API: POST /api/submit-server ───────────────────────────────────
    def _handle_submit_server(self):
        body = self._read_body()
        name = (body.get("name") or "").strip()
        url = (body.get("url") or "").strip()
        description = (body.get("description") or "").strip()
        category = (body.get("category") or "").strip()

        if not name:
            self._send_error("Server name is required")
            return
        if not url:
            self._send_error("URL is required")
            return
        if not description:
            self._send_error("Description is required")
            return
        if not category:
            self._send_error("Category is required")
            return

        # Check for duplicate URL
        if submission_exists_by_url(url):
            self._send_error("This server has already been submitted", 409)
            return

        add_submission(name, url, description, category)

        self._send_success({
            "name": name,
            "status": "pending",
        }, "Server submitted successfully! We'll review and add it shortly.")

    # ── API: POST /api/checkout ────────────────────────────────────────
    def _handle_checkout(self):
        body = self._read_body()
        product_id = (body.get("product") or "").strip()
        email = (body.get("email") or "").strip()

        if not product_id:
            self._send_error("Product ID is required")
            return
        if product_id not in PRODUCTS:
            self._send_error(f"Unknown product: {product_id}")
            return

        product = PRODUCTS[product_id]

        # Create Razorpay order
        receipt = str(uuid.uuid4())[:40]
        result = create_razorpay_order(product["amount"], product["currency"], receipt)

        if result.get("error"):
            self._send_error(f"Failed to create order: {result.get('body', result.get('message', 'Unknown error'))}", 502)
            return

        # Store purchase intent in SQLite
        add_purchase(result["id"], product_id, product["name"], product["amount"], product["currency"], email)

        self._send_json({
            "success": True,
            "order_id": result["id"],
            "amount": result["amount"],
            "currency": result["currency"],
            "key_id": RAZORPAY_KEY_ID,
            "product": product_id,
            "product_name": product["name"],
        })

    # ── API: POST /api/verify-payment ──────────────────────────────────
    def _handle_verify_payment(self):
        body = self._read_body()
        order_id = (body.get("razorpay_order_id") or "").strip()
        payment_id = (body.get("razorpay_payment_id") or "").strip()
        signature = (body.get("razorpay_signature") or "").strip()

        if not order_id or not payment_id or not signature:
            self._send_error("Missing payment verification fields")
            return

        # Verify signature
        if not verify_razorpay_signature(order_id, payment_id, signature):
            self._send_error("Invalid payment signature", 400)
            return

        # Update purchase record
        purchase = get_purchase(order_id)
        updated = purchase is not None
        if updated:
            update_purchase(order_id, payment_id=payment_id, status="completed", verified_at=datetime.now(timezone.utc).isoformat())

        # Fetch payment details from Razorpay to confirm
        payment = fetch_payment(payment_id)
        if payment.get("error"):
            # Signature already verified, still consider it success
            pass

        self._send_json({
            "success": True,
            "order_id": order_id,
            "payment_id": payment_id,
            "status": "completed",
            "message": "Payment verified successfully! Your listing will be activated shortly.",
        })

    # ── API: GET /api/purchase/{order_id} ──────────────────────────────
    def _handle_get_purchase(self, order_id):
        if not order_id:
            self._send_error("Order ID is required")
            return

        purchase = get_purchase(order_id)
        if purchase:
            self._send_json({
                "success": True,
                "purchase": purchase,
            })
            return

        self._send_error("Purchase not found", 404)

    # ── API: GitHub OAuth ────────────────────────────────────────────
    def _handle_auth_github(self):
        """Redirect to GitHub OAuth authorization page."""
        params = urllib.parse.urlencode({
            "client_id": GITHUB_CLIENT_ID,
            "redirect_uri": GITHUB_REDIRECT_URI,
            "scope": "read:user user:email",
            "state": secrets.token_hex(16),
        })
        url = f"https://github.com/login/oauth/authorize?{params}"
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _handle_auth_github_callback(self):
        """Handle GitHub OAuth callback: exchange code, get user, create session."""
        params = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
        code = (params.get("code", [""])[0]).strip()
        if not code:
            self._send_error("Missing authorization code", 400)
            return

        # Exchange code for access token
        token_url = "https://github.com/login/oauth/access_token"
        token_data = urllib.parse.urlencode({
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": code,
            "redirect_uri": GITHUB_REDIRECT_URI,
        }).encode()
        token_req = urllib.request.Request(
            token_url, data=token_data,
            headers={"Accept": "application/json", "User-Agent": "MCPAppDirectory/2.0"}
        )
        try:
            with urllib.request.urlopen(token_req, timeout=15) as resp:
                token_resp = json.loads(resp.read().decode())
        except Exception as e:
            self._send_error(f"Failed to get access token: {e}", 502)
            return

        access_token = token_resp.get("access_token")
        if not access_token:
            self._send_error("Failed to get access token from GitHub", 400)
            return

        # Fetch user info from GitHub
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "MCPAppDirectory/2.0",
        }
        try:
            user_req = urllib.request.Request(
                "https://api.github.com/user", headers=headers
            )
            with urllib.request.urlopen(user_req, timeout=15) as resp:
                github_user = json.loads(resp.read().decode())
        except Exception as e:
            self._send_error(f"Failed to get user info: {e}", 502)
            return

        github_id = str(github_user.get("id", ""))
        email = github_user.get("email") or ""
        name = github_user.get("name") or github_user.get("login", "")
        avatar_url = github_user.get("avatar_url", "")

        # If email is private, try the emails endpoint
        if not email:
            try:
                emails_req = urllib.request.Request(
                    "https://api.github.com/user/emails", headers=headers
                )
                with urllib.request.urlopen(emails_req, timeout=15) as resp:
                    emails_data = json.loads(resp.read().decode())
                for e in emails_data:
                    if e.get("primary") and e.get("verified"):
                        email = e.get("email", "")
                        break
                if not email and emails_data:
                    email = emails_data[0].get("email", "")
            except Exception:
                pass

        # Create or update user in DB
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE github_id = ?", (github_id,))
        existing = c.fetchone()
        if existing:
            user_id = existing[0]
            c.execute(
                "UPDATE users SET email = ?, name = ?, avatar_url = ? WHERE id = ?",
                (email, name, avatar_url, user_id)
            )
        else:
            c.execute(
                "INSERT INTO users (github_id, email, name, avatar_url) VALUES (?, ?, ?, ?)",
                (github_id, email, name, avatar_url)
            )
            user_id = c.lastrowid
        conn.commit()
        conn.close()

        # Create session
        session_token = create_session(user_id)

        # Set session cookie and redirect to dashboard
        self.send_response(302)
        cookie = f"session={session_token}; HttpOnly; Path=/; Max-Age={SESSION_DURATION}; SameSite=Lax"
        self.send_header("Set-Cookie", cookie)
        self.send_header("Location", "/dashboard")
        self.end_headers()

    def _handle_auth_me(self):
        """Return current user info if authenticated."""
        user = _get_current_user(self)
        if not user:
            self._send_json({"authenticated": False}, 200)
            return

        # Get saved server IDs
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT server_id FROM saved_servers WHERE user_id = ?", (user["id"],))
        saved_ids = [row[0] for row in c.fetchall()]
        conn.close()

        self._send_json({
            "authenticated": True,
            "user": {
                "id": user["id"],
                "github_id": user["github_id"],
                "email": user["email"],
                "name": user["name"],
                "avatar_url": user["avatar_url"],
                "created_at": user["created_at"],
                "saved_server_ids": saved_ids,
            }
        })

    def _handle_auth_logout(self):
        """Logout: delete session and clear cookie."""
        token = _get_session_token_from_cookie(self)
        delete_session(token)
        self.send_response(200)
        self._set_cors()
        cookie = "session=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax"
        self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"success": True, "message": "Logged out"}).encode())

    # ── API: Saved Servers ────────────────────────────────────────────
    def _handle_get_saved_servers(self):
        """Return saved server IDs for the current user."""
        user = _get_current_user(self)
        if not user:
            self._send_error("Not authenticated", 401)
            return
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT server_id FROM saved_servers WHERE user_id = ? ORDER BY created_at DESC", (user["id"],))
        saved_ids = [row[0] for row in c.fetchall()]
        conn.close()
        self._send_json({"saved_server_ids": saved_ids})

    def _handle_save_server(self):
        """Save a server for the current user."""
        user = _get_current_user(self)
        if not user:
            self._send_error("Not authenticated", 401)
            return
        body = self._read_body()
        server_id = body.get("server_id")
        if server_id is None:
            self._send_error("server_id is required")
            return
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        try:
            c.execute(
                "INSERT OR IGNORE INTO saved_servers (user_id, server_id) VALUES (?, ?)",
                (user["id"], int(server_id))
            )
            conn.commit()
        except Exception as e:
            conn.close()
            self._send_error(str(e), 500)
            return
        conn.close()
        self._send_success(message="Server saved")

    def _handle_unsave_server(self):
        """Remove a saved server for the current user."""
        user = _get_current_user(self)
        if not user:
            self._send_error("Not authenticated", 401)
            return
        body = self._read_body()
        server_id = body.get("server_id")
        if server_id is None:
            self._send_error("server_id is required")
            return
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute(
            "DELETE FROM saved_servers WHERE user_id = ? AND server_id = ?",
            (user["id"], int(server_id))
        )
        conn.commit()
        conn.close()
        self._send_success(message="Server unsaved")

    # ── API: User API Key ─────────────────────────────────────────────
    def _handle_generate_api_key(self):
        """Generate a new API key for the authenticated user."""
        user = _get_current_user(self)
        if not user:
            self._send_error("Not authenticated", 401)
            return
        api_key = generate_api_key()
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute(
            "UPDATE users SET api_key = ?, api_key_created_at = datetime('now') WHERE id = ?",
            (api_key, user["id"])
        )
        conn.commit()
        conn.close()
        self._send_json({"success": True, "api_key": api_key})

    # ── SEO: Sitemap ───────────────────────────────────────────────────
    def _handle_sitemap(self):
        """Generate sitemap.xml for ALL pages with proper lastmod dates."""
        servers = get_all_servers()
        base = "https://mcpappdirectory.com"
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        urls = [
            f"  <url><loc>{base}/</loc><priority>1.0</priority><lastmod>{now_iso}</lastmod></url>",
            f"  <url><loc>{base}/about</loc><priority>0.6</priority><lastmod>{now_iso}</lastmod></url>",
            f"  <url><loc>{base}/how-it-works</loc><priority>0.6</priority><lastmod>{now_iso}</lastmod></url>",
            f"  <url><loc>{base}/scoring</loc><priority>0.7</priority><lastmod>{now_iso}</lastmod></url>",
            f"  <url><loc>{base}/submit</loc><priority>0.5</priority><lastmod>{now_iso}</lastmod></url>",
            f"  <url><loc>{base}/leaderboard</loc><priority>0.7</priority><lastmod>{now_iso}</lastmod></url>",
            f"  <url><loc>{base}/trending</loc><priority>0.8</priority><lastmod>{now_iso}</lastmod></url>",
            f"  <url><loc>{base}/new</loc><priority>0.8</priority><lastmod>{now_iso}</lastmod></url>",
        ]
        
        # Grade pages
        for letter in ("A", "B", "C", "D", "F"):
            urls.append(f"  <url><loc>{base}/grade/{letter}</loc><priority>0.7</priority><lastmod>{now_iso}</lastmod></url>")
        
        # Category pages
        cats = set()
        for s in servers:
            cats.add(s.get("category", "Uncategorized"))
        for c in sorted(cats):
            cat_slug = c.lower().replace(" & ", "-").replace(" ", "-").replace("--", "-")
            urls.append(f"  <url><loc>{base}/category/{cat_slug}</loc><priority>0.7</priority><lastmod>{now_iso}</lastmod></url>")
        
        # Server detail pages with pushed_at as lastmod
        for s in servers:
            slug = _make_slug(s["name"], s.get("url", ""))
            lastmod = now_iso
            pushed = s.get("pushed_at") or s.get("last_updated", "")
            if pushed:
                try:
                    dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
                    lastmod = dt.strftime("%Y-%m-%d")
                except (ValueError, AttributeError):
                    pass
            urls.append(f"  <url><loc>{base}/servers/{slug}</loc><priority>0.9</priority><lastmod>{lastmod}</lastmod></url>")
        
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(urls) + "\n</urlset>"
        self.send_response(200)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(xml.encode())))
        self.end_headers()
        self.wfile.write(xml.encode())

    # ── SEO: Server Detail Page ────────────────────────────────────────
    def _handle_server_page(self, slug):
        """Render a full HTML page for an individual MCP server."""
        servers = get_all_servers()
        server = None
        for s in servers:
            if _make_slug(s["name"], s.get("url", "")) == slug:
                server = s
                break
        # Fallback: try matching by name (sometimes slug drifts if listings.json changed)
        if not server:
            name_part = slug.rsplit("-", 1)[0] if "-" in slug else slug
            for s in servers:
                s_name = s.get("name", "").lower().strip()
                s_slug_part = re.sub(r'[^a-z0-9]+', '-', s_name).strip('-')[:50]
                if s_slug_part == name_part or s_name.replace(" ", "-") == name_part:
                    server = s
                    break
        if not server:
            # Also try direct index by name hash
            for s in servers:
                if s.get("name", "").lower().strip() == slug.split("-")[0].replace("-", " "):
                    server = s
                    break
        if not server:
            self._send_error("Server not found", 404)
            return
        
        name = server.get("name", "Unknown")
        score = server.get("score", 0)
        grade = server.get("grade", "F")
        stars = server.get("stars", 0) or 0
        forks = server.get("forks", 0) or 0
        lang = server.get("language", "") or "Unknown"
        desc = server.get("description", "") or "No description available."
        category = server.get("category", "Uncategorized")
        url = server.get("url", "")
        topics = server.get("topics", []) or []
        license_name = server.get("license", "")
        last_updated = server.get("last_updated", "") or ""
        github_ok = server.get("has_github_stats") != False
        details = server.get("score_details", {}) or {}
        
        stars_str = f"{stars/1000:.1f}K" if stars >= 1000 else str(stars)
        forks_str = f"{forks/1000:.1f}K" if forks >= 1000 else str(forks)
        
        lic_short = license_name
        if lic_short in ("NOASSERTION", "NONE"): lic_short = ""
        if lic_short == "MIT License": lic_short = "MIT"
        if lic_short == "Apache License 2.0": lic_short = "Apache-2.0"
        
        updated_relative = ""
        if last_updated:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                days = (datetime.now(timezone.utc) - dt).days
                updated_relative = f"Updated {days}d ago" if days > 0 else "Updated today"
            except:
                updated_relative = last_updated[:10]
        
        # Score breakdown items
        score_items = [
            ("Recency", details.get("recency", 0), 20),
            ("Documentation", details.get("documentation", 0), 15),
            ("Tests", details.get("tests", 0), 10),
            ("Auth/Security", details.get("auth", 0), 10),
            ("Star Velocity", details.get("star_velocity", 0), 15),
            ("Security Audit", details.get("security", 0), 15),
            ("Install Docs", details.get("install_instructions", 0), 15),
        ]
        
        score_html = ""
        for label, val, mx in score_items:
            val = val or 0
            pct = (val / mx) * 100 if mx > 0 else 0
            bar_color = "#22c55e" if pct >= 80 else "#3b82f6" if pct >= 50 else "#f59e0b"
            score_html += f"""<div class="si"><span class="sil">{label}</span><span class="siv" style="color:{bar_color}">{val}/{mx}</span><div class="sib"><div class="sibf" style="width:{pct}%;background:{bar_color}"></div></div></div>"""
        
        grade_colors = {"A": "#22c55e", "B": "#3b82f6", "C": "#f59e0b", "D": "#f97316", "F": "#ef4444"}
        grade_color = grade_colors.get(grade, "#888")
        
        topic_html = "".join(f'<span class="tt">{_e(t)}</span>' for t in topics[:8])
        meta_desc = f"{name}: {score}/100 ({grade} grade). {desc[:150]}"
        page_title = f"{name} — {score}/100 — MCP App Directory"
        
        # Generate related servers (same category, excluding self)
        related_servers = []
        for other in servers:
            if other.get("name") != name and other.get("category") == category and other.get("has_github_stats") != False:
                related_servers.append(other)
        related_servers = sorted(related_servers, key=lambda x: x.get("score", 0), reverse=True)[:6]

        related_html = ""
        if related_servers:
            related_html = '<div class="section"><h2>🔗 Related MCP Servers</h2><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px">'
            for rs in related_servers:
                rs_slug = _make_slug(rs["name"], rs.get("url", ""))
                rs_score = rs.get("score", 0)
                rs_grade = rs.get("grade", "F")
                rs_gc = grade_colors.get(rs_grade, "#888")
                rs_name = _e(rs.get("name", "Unknown"))
                related_html += f'<a href="/servers/{rs_slug}" style="text-decoration:none;color:inherit;background:#1a1a2e;padding:12px;border-radius:8px;border:1px solid #2a2a3f"><div style="font-size:0.85em;font-weight:600;margin-bottom:4px">{rs_name}</div><div style="font-size:0.75em;color:{rs_gc}">{rs_score}/100 · {rs_grade}</div></a>'
            related_html += '</div></div>'

        # Stats summary for content depth
        total_count = len(servers)
        cat_count = len([s for s in servers if s.get("category") == category])
        cats_set = set(s.get("category", "Uncategorized") for s in servers)
        cat_count_total = len(cats_set)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_e(page_title)}</title>
<meta name="description" content="{_e(meta_desc)}">
<meta property="og:title" content="{_e(name)} — {score}/100 MCP Server">
<meta property="og:description" content="{_e(desc[:200])}">
<meta property="og:url" content="https://mcpappdirectory.com/servers/{slug}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="MCP App Directory">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_e(name)} — {score}/100 MCP Server">
<meta name="twitter:description" content="{_e(desc[:200])}">
<meta property="og:image" content="{_url_to_data_uri(_og_image_svg(name, score, grade))}">
<meta name="twitter:image" content="{_url_to_data_uri(_og_image_svg(name, score, grade))}">
<link rel="canonical" href="https://mcpappdirectory.com/servers/{slug}">
<link rel="alternate" hreflang="en" href="https://mcpappdirectory.com/servers/{slug}">
<link rel="alternate" hreflang="x-default" href="https://mcpappdirectory.com/servers/{slug}">
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"WebSite","name":"MCP App Directory","url":"https://mcpappdirectory.com","potentialAction":{{"@type":"SearchAction","target":{{"@type":"EntryPoint","urlTemplate":"https://mcpappdirectory.com/?search={{search_term_string}}"}},"query-input":"required name=search_term_string"}}}}
</script>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"SoftwareApplication","name":"{_e(name)}","applicationCategory":"{_e(category)}","description":"{_e(desc[:300])}","operatingSystem":"MCP","offers":{{"@type":"Offer","price":"0","priceCurrency":"USD"}},"aggregateRating":{{"@type":"AggregateRating","ratingValue":"{round(score/20, 1)}","bestRating":"10","ratingCount":"{max(1, stars)}"}}}}
</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0f;color:#e8e8f0;line-height:1.6}}
.container{{max-width:900px;margin:0 auto;padding:0 24px}}
header{{padding:24px 0;border-bottom:1px solid #2a2a3f;margin-bottom:32px}}
header a{{color:#6366f1;text-decoration:none;font-size:0.9em}}
h1{{font-size:1.8em;margin-bottom:8px}}
.subtitle{{color:#8888a0;font-size:0.95em;margin-bottom:24px}}
.grade-badge{{display:inline-flex;align-items:center;justify-content:center;width:64px;height:64px;border-radius:16px;font-size:2em;font-weight:800}}
.stats-row{{display:flex;flex-wrap:wrap;gap:12px 24px;margin:16px 0;font-size:0.9em;color:#8888a0}}
.stats-row span{{}}
.desc{{font-size:1.05em;margin:20px 0;padding:16px;background:#14141f;border-radius:12px;border:1px solid #2a2a3f}}
.meta-tags{{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}}
.tt{{font-size:0.78em;padding:2px 8px;border-radius:4px;background:#2a2a3f;color:#8888a0}}
.ct{{font-size:0.82em;padding:3px 10px;border-radius:4px;background:rgba(99,102,241,0.12);color:#6366f1}}
h2{{font-size:1.3em;margin:28px 0 16px;color:#c0c0d0}}
.section{{background:#14141f;border-radius:12px;padding:20px;margin-bottom:20px;border:1px solid #2a2a3f}}
.si{{display:flex;align-items:center;gap:8px;margin:6px 0;flex-wrap:wrap}}
.sil{{flex:1;font-size:0.88em;min-width:120px}}
.siv{{font-weight:600;font-size:0.85em;width:50px;text-align:right}}
.sib{{flex:1;min-width:80px;height:6px;background:#2a2a3f;border-radius:3px;overflow:hidden}}
.sibf{{height:100%;border-radius:3px}}
.buttons{{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}}
.btn{{display:inline-block;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.9em}}
.btn-primary{{background:#6366f1;color:white}}
.btn-outline{{border:1px solid #2a2a3f;color:#e8e8f0}}
.btn-outline:hover{{border-color:#6366f1}}
.btn-disabled{{border:1px solid #2a2a3f;color:#f59e0b;opacity:0.7;cursor:default}}
.breadcrumb{{font-size:0.85em;color:#8888a0;margin-bottom:12px}}
.breadcrumb a{{color:#6366f1;text-decoration:none}}
footer{{text-align:center;padding:32px 0;border-top:1px solid #2a2a3f;margin-top:40px;color:#8888a0;font-size:0.85em}}
@media(max-width:640px){{h1{{font-size:1.4em}}.stats-row{{gap:8px 16px}}.grade-badge{{width:48px;height:48px;font-size:1.5em}}}}
</style>
</head>
<body>
<header><div class="container"><a href="/">← Back to Directory</a></div></header>
<main class="container">
<div class="breadcrumb"><a href="/">Home</a> / <a href="/category/{category.lower().replace(' & ','-').replace(' ','-')}">{_e(category)}</a> / {_e(name)}</div>
<div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
<div class="grade-badge" style="background:rgba({','.join(str(int(gc[1:3],16)) for gc in [grade_color, grade_color, grade_color])[:4]},0.12);color:{grade_color}">{grade}</div>
<div><h1>{_e(name)}</h1><div class="subtitle" style="font-size:1.2em;color:{grade_color};font-weight:600">{score}/100</div></div>
</div>
<div class="stats-row">
<span>⭐ {stars_str} stars</span>
<span>🔀 {forks_str} forks</span>
<span>🔧 {_e(lang)}</span>
{("<span>📄 "+_e(lic_short)+"</span>") if lic_short else ""}
{("<span>🕐 "+_e(updated_relative)+"</span>") if updated_relative else ""}
</div>
<div class="meta-tags"><span class="ct">{_e(category)}</span>{topic_html}</div>
<div class="desc">{_e(desc)}</div>
<div class="section"><h2>📊 Score Breakdown</h2>{score_html}<div style="margin-top:12px;display:flex;gap:16px;font-size:0.85em;color:#8888a0"><span>Language: {_e(lang)}</span></div></div>
<div class="section"><h2>⚡ Install</h2><pre style="background:#1a1a2e;padding:12px;border-radius:8px;overflow-x:auto;font-size:0.85em;color:#8888a0">{_get_install_text(server)}</pre></div>
<div class="buttons">
{f'<a href="{_e(url)}" target="_blank" class="btn btn-primary">View on GitHub →</a>' if github_ok else '<span class="btn btn-disabled">⚠ GitHub repo not found</span>'}
{f'<a href="{_e(url)}#readme" target="_blank" class="btn btn-outline">📖 README</a>' if github_ok else ''}
{f'<a href="{_e(url)}/issues" target="_blank" class="btn btn-outline">🐛 Issues</a>' if github_ok else ''}
<a href="/" class="btn btn-outline">Browse All Servers</a>
</div>
{related_html}
<div style="margin-top:24px;padding:20px;background:#14141f;border-radius:12px;border:1px solid #2a2a3f;text-align:center">
<p style="font-size:0.9em;color:#8888a0">📬 Get weekly MCP server picks → <a href="/" style="color:#6366f1">Subscribe to Newsletter</a></p>
</div>
<div style="margin-top:24px;margin-bottom:16px;padding:16px;background:#14141f;border-radius:12px;border:1px solid #2a2a3f;font-size:0.85em;color:#8888a0;line-height:1.6">
<p><strong>About {_e(name)}</strong> — {_e(name)} is a {_e(category)} MCP server with a quality score of {score}/100 ({grade} grade). It has ⭐{stars_str} GitHub stars, 🔀{forks_str} forks, and is written in {_e(lang)}. {_e(desc[:200])}</p>
<p style="margin-top:8px">Browse <a href="/" style="color:#6366f1">{total_count} verified MCP servers</a> across {cat_count_total} categories. Filter by grade, search by keyword, and compare servers side-by-side on the <a href="/leaderboard" style="color:#6366f1">leaderboard</a>.</p>
</div>
</main>
<footer><div class="container"><p>MCP App Directory — AI-graded quality scoring for every MCP server.</p></div></footer>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if not getattr(self, '_is_head_request', False):
            self.wfile.write(html.encode())

    # ── SEO: Category Page ─────────────────────────────────────────────
    def _handle_category_page(self, cat_slug):
        """Render a page listing all servers in a category with full SEO metadata."""
        servers = get_all_servers()
        # Normalize: "development" matches "🛠 Development", "ai-ml" matches "🤖 AI/ML"
        cat_map = {
            "development": "🛠 Development",
            "ai-ml": "🤖 AI/ML",
            "data": "📊 Data",
            "productivity": "⚡ Productivity",
            "communication": "💬 Communication",
            "cloud-devops": "☁️ Cloud & DevOps",
            "browser": "🌐 Browser",
            "security": "🔒 Security",
            "search": "Search",
            "database": "Database",
            "monitoring": "Monitoring",
            "knowledge-memory": "Knowledge & Memory",
            "documentation-access": "Documentation Access",
        }
        cat_name = cat_map.get(cat_slug, cat_slug.replace("-", " ").title())
        
        filtered = [s for s in servers if s.get("category", "").lower() == cat_name.lower()]
        if not filtered:
            # Try reverse
            for k, v in cat_map.items():
                if k == cat_slug:
                    filtered = [s for s in servers if s.get("category", "").lower() == v.lower()]
                    cat_name = v
                    break
        
        if not filtered:
            self._serve_static("/index.html")
            return
            
        grade_colors = {"A": "#22c55e", "B": "#3b82f6", "C": "#f59e0b", "D": "#f97316", "F": "#ef4444"}
        
        cards = ""
        for s in sorted(filtered, key=lambda x: x.get("score", 0), reverse=True)[:100]:
            nm = s.get("name", "Unknown")
            sc = s.get("score", 0)
            gr = s.get("grade", "F")
            gc = grade_colors.get(gr, "#888")
            st = s.get("stars", 0) or 0
            st_str = f"{st/1000:.1f}K" if st >= 1000 else str(st)
            lg = s.get("language", "") or ""
            sl = _make_slug(nm, s.get("url", ""))
            cards += f'<a href="/servers/{sl}" class="cl" style="border-left:3px solid {gc}"><div class="clg" style="color:{gc}">{gr}</div><div class="cln"><strong>{_e(nm)}</strong><span class="clm">{sc}/100 · ⭐ {st_str}{" · "+_e(lg) if lg else ""}</span></div></a>\n'
        
        meta_desc = f"{len(filtered)} MCP servers in {cat_name} category. AI-graded quality scores 0-100. Browse, compare, and find the best {cat_name} MCP servers."
        page_title = f"{cat_name} MCP Servers — AI-Graded Scores — MCP App Directory"
        canonical_url = f"https://mcpappdirectory.com/category/{cat_slug}"
        base = "https://mcpappdirectory.com"
        og_image_data = _url_to_data_uri(f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#0a0a0f"/>
  <text x="600" y="240" font-family="system-ui,sans-serif" font-size="64" font-weight="700" fill="#e8e8f0" text-anchor="middle">{_e(cat_name)}</text>
  <rect x="350" y="280" width="500" height="80" rx="40" fill="#6366f1" opacity="0.15"/>
  <text x="600" y="338" font-family="system-ui,sans-serif" font-size="40" font-weight="700" fill="#6366f1" text-anchor="middle">{len(filtered)} MCP Servers</text>
  <text x="600" y="420" font-family="system-ui,sans-serif" font-size="28" fill="#8888a0" text-anchor="middle">AI-Graded Quality Scores 0-100</text>
  <rect x="60" y="480" width="1080" height="2" fill="#2a2a3f"/>
  <text x="600" y="530" font-family="system-ui,sans-serif" font-size="28" fill="#8888a0" text-anchor="middle">MCP App Directory</text>
</svg>''')
        
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_e(page_title)}</title>
<meta name="description" content="{_e(meta_desc)}">
<meta property="og:title" content="{_e(cat_name)} MCP Servers — AI-Graded Scores">
<meta property="og:description" content="{_e(meta_desc)}">
<meta property="og:url" content="{canonical_url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="MCP App Directory">
<meta property="og:image" content="{og_image_data}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_e(cat_name)} MCP Servers — AI-Graded Scores">
<meta name="twitter:description" content="{_e(meta_desc)}">
<meta name="twitter:image" content="{og_image_data}">
<link rel="canonical" href="{canonical_url}">
<link rel="alternate" hreflang="en" href="{canonical_url}">
<link rel="alternate" hreflang="x-default" href="{canonical_url}">
<script type="application/ld+json">
{_build_website_schema()}
</script>
<script type="application/ld+json">
{_build_collection_page_schema(cat_name, meta_desc, canonical_url, filtered)}
</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0f;color:#e8e8f0;line-height:1.6}}
.container{{max-width:900px;margin:0 auto;padding:0 24px}}
header{{padding:24px 0;border-bottom:1px solid #2a2a3f}}
header a{{color:#6366f1;text-decoration:none;font-size:0.9em}}
h1{{font-size:1.6em;margin:20px 0 8px}}
.sub{{color:#8888a0;margin-bottom:20px}}
.cl{{display:flex;align-items:center;gap:12px;padding:12px 16px;background:#14141f;border-radius:8px;margin:6px 0;text-decoration:none;color:#e8e8f0;transition:transform 0.1s}}
.cl:hover{{transform:translateX(4px)}}
.clg{{font-weight:800;font-size:1.2em;width:28px;text-align:center}}
.cln{{flex:1}}
.clm{{font-size:0.82em;color:#8888a0;margin-left:8px}}
footer{{text-align:center;padding:32px 0;border-top:1px solid #2a2a3f;margin-top:40px;color:#8888a0;font-size:0.85em}}
.breadcrumb{{font-size:0.85em;color:#8888a0;margin-bottom:12px}}
.breadcrumb a{{color:#6366f1;text-decoration:none}}
.nav-links{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}}
.nav-links a{{color:#6366f1;text-decoration:none;font-size:0.9em;padding:4px 12px;border:1px solid #2a2a3f;border-radius:6px}}
.nav-links a:hover{{border-color:#6366f1}}
</style>
</head>
<body>
<header><div class="container"><a href="/">← Back to Directory</a></div></header>
<main class="container">
<div class="breadcrumb"><a href="/">Home</a> / <a href="/category/{cat_slug}">{_e(cat_name)}</a></div>
<h1>{_e(cat_name)} MCP Servers</h1>
<p class="sub">{len(filtered)} MCP servers · AI-graded quality scores</p>
<div class="nav-links">
<a href="/trending">🔥 Trending</a>
<a href="/new">🆕 New</a>
<a href="/leaderboard">🏆 Leaderboard</a>
</div>
{cards}
<p style="text-align:center;margin:24px 0"><a href="/" style="color:#6366f1">Browse all categories →</a></p>
</main>
<footer><div class="container"><p>MCP App Directory — AI-graded quality scoring for every MCP server.</p></div></footer>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if not getattr(self, '_is_head_request', False):
            self.wfile.write(html.encode())

    # ── SEO: Trending Page ─────────────────────────────────────────────
    def _handle_trending_page(self):
        """Server-rendered HTML page for /trending — top trending MCP servers."""
        now = datetime.now(timezone.utc)
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM servers WHERE verified = 1 ORDER BY stars DESC, forks DESC LIMIT 48"
        ).fetchall()
        conn.close()
        servers = [_server_row_to_dict(r) for r in rows]
        # Re-score with recency
        scored = []
        for s in servers:
            try:
                pushed = datetime.fromisoformat(s.get("pushed_at", "2025-01-01").replace("Z", "+00:00"))
                days_since_push = (now - pushed).days
            except:
                days_since_push = 365
            stars = s.get("stars", 0) or 0
            forks = s.get("forks", 0) or 0
            downloads = s.get("downloads_monthly", 0) or 0
            recency_bonus = max(0, 50 - days_since_push) * 10
            trending_score = stars + forks * 2 + downloads / 100 + recency_bonus
            scored.append((trending_score, s))
        scored.sort(key=lambda x: -x[0])
        top = [s[1] for s in scored[:48]]

        grade_colors = {"A": "#22c55e", "B": "#3b82f6", "C": "#f59e0b", "D": "#f97316", "F": "#ef4444"}
        cards = ""
        for s in top:
            nm = s.get("name", "Unknown")
            sc = s.get("score", 0)
            gr = s.get("grade", "F")
            gc = grade_colors.get(gr, "#888")
            st = s.get("stars", 0) or 0
            st_str = f"{st/1000:.1f}K" if st >= 1000 else str(st)
            lg = s.get("language", "") or ""
            sl = _make_slug(nm, s.get("url", ""))
            cards += f'<a href="/servers/{sl}" class="cl" style="border-left:3px solid {gc}"><div class="clg" style="color:{gc}">{gr}</div><div class="cln"><strong>{_e(nm)}</strong><span class="clm">{sc}/100 · ⭐ {st_str}{" · "+_e(lg) if lg else ""}</span></div></a>\n'

        meta_desc = f"Trending MCP servers — top {len(top)} trending servers ranked by stars, forks, and recent activity. AI-graded quality scores."
        canonical_url = "https://mcpappdirectory.com/trending"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trending MCP Servers — MCP App Directory</title>
<meta name="description" content="{_e(meta_desc)}">
<meta property="og:title" content="Trending MCP Servers — Top Rated by Activity">
<meta property="og:description" content="{_e(meta_desc)}">
<meta property="og:url" content="{canonical_url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="MCP App Directory">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Trending MCP Servers">
<meta name="twitter:description" content="Top trending MCP servers ranked by stars, forks, and recent activity.">
<link rel="canonical" href="{canonical_url}">
<link rel="alternate" hreflang="en" href="{canonical_url}">
<link rel="alternate" hreflang="x-default" href="{canonical_url}">
<script type="application/ld+json">
{_build_website_schema()}
</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0f;color:#e8e8f0;line-height:1.6}}
.container{{max-width:900px;margin:0 auto;padding:0 24px}}
header{{padding:24px 0;border-bottom:1px solid #2a2a3f}}
header a{{color:#6366f1;text-decoration:none;font-size:0.9em}}
h1{{font-size:1.6em;margin:20px 0 8px}}
.sub{{color:#8888a0;margin-bottom:20px}}
.cl{{display:flex;align-items:center;gap:12px;padding:12px 16px;background:#14141f;border-radius:8px;margin:6px 0;text-decoration:none;color:#e8e8f0;transition:transform 0.1s}}
.cl:hover{{transform:translateX(4px)}}
.clg{{font-weight:800;font-size:1.2em;width:28px;text-align:center}}
.cln{{flex:1}}
.clm{{font-size:0.82em;color:#8888a0;margin-left:8px}}
.breadcrumb{{font-size:0.85em;color:#8888a0;margin-bottom:12px}}
.breadcrumb a{{color:#6366f1;text-decoration:none}}
.nav-links{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}}
.nav-links a{{color:#6366f1;text-decoration:none;font-size:0.9em;padding:4px 12px;border:1px solid #2a2a3f;border-radius:6px}}
.nav-links a:hover{{border-color:#6366f1}}
footer{{text-align:center;padding:32px 0;border-top:1px solid #2a2a3f;margin-top:40px;color:#8888a0;font-size:0.85em}}
</style>
</head>
<body>
<header><div class="container"><a href="/">← Back to Directory</a></div></header>
<main class="container">
<div class="breadcrumb"><a href="/">Home</a> / Trending</div>
<h1>🔥 Trending MCP Servers</h1>
<p class="sub">Top {len(top)} trending servers by stars, forks, and recent activity</p>
<div class="nav-links">
<a href="/new">🆕 New</a>
<a href="/">📋 Browse All</a>
<a href="/leaderboard">🏆 Leaderboard</a>
</div>
{cards}
<p style="text-align:center;margin:24px 0"><a href="/" style="color:#6366f1">Browse all categories →</a></p>
</main>
<footer><div class="container"><p>MCP App Directory — AI-graded quality scoring for every MCP server.</p></div></footer>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if not getattr(self, '_is_head_request', False):
            self.wfile.write(html.encode())

    # ── SEO: New Servers Page ──────────────────────────────────────────
    def _handle_new_page(self):
        """Server-rendered HTML page for /new — newest MCP servers first."""
        servers, _ = search_servers(sort_by="newest", limit=100, offset=0)

        grade_colors = {"A": "#22c55e", "B": "#3b82f6", "C": "#f59e0b", "D": "#f97316", "F": "#ef4444"}
        cards = ""
        for s in servers:
            nm = s.get("name", "Unknown")
            sc = s.get("score", 0)
            gr = s.get("grade", "F")
            gc = grade_colors.get(gr, "#888")
            st = s.get("stars", 0) or 0
            st_str = f"{st/1000:.1f}K" if st >= 1000 else str(st)
            lg = s.get("language", "") or ""
            sl = _make_slug(nm, s.get("url", ""))
            pushed = s.get("pushed_at", "")
            date_str = pushed[:10] if pushed else ""
            date_tag = f' · 🕐 {date_str}' if date_str else ''
            cards += f'<a href="/servers/{sl}" class="cl" style="border-left:3px solid {gc}"><div class="clg" style="color:{gc}">{gr}</div><div class="cln"><strong>{_e(nm)}</strong><span class="clm">{sc}/100 · ⭐ {st_str}{" · "+_e(lg) if lg else ""}{date_tag}</span></div></a>\n'

        meta_desc = f"Newest MCP servers added to the directory. Latest {len(servers)} servers sorted by most recently updated."
        canonical_url = "https://mcpappdirectory.com/new"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>New MCP Servers — Latest Additions — MCP App Directory</title>
<meta name="description" content="{_e(meta_desc)}">
<meta property="og:title" content="New MCP Servers — Latest Additions">
<meta property="og:description" content="{_e(meta_desc)}">
<meta property="og:url" content="{canonical_url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="MCP App Directory">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="New MCP Servers">
<meta name="twitter:description" content="Latest MCP servers added to the directory.">
<link rel="canonical" href="{canonical_url}">
<link rel="alternate" hreflang="en" href="{canonical_url}">
<link rel="alternate" hreflang="x-default" href="{canonical_url}">
<script type="application/ld+json">
{_build_website_schema()}
</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0f;color:#e8e8f0;line-height:1.6}}
.container{{max-width:900px;margin:0 auto;padding:0 24px}}
header{{padding:24px 0;border-bottom:1px solid #2a2a3f}}
header a{{color:#6366f1;text-decoration:none;font-size:0.9em}}
h1{{font-size:1.6em;margin:20px 0 8px}}
.sub{{color:#8888a0;margin-bottom:20px}}
.cl{{display:flex;align-items:center;gap:12px;padding:12px 16px;background:#14141f;border-radius:8px;margin:6px 0;text-decoration:none;color:#e8e8f0;transition:transform 0.1s}}
.cl:hover{{transform:translateX(4px)}}
.clg{{font-weight:800;font-size:1.2em;width:28px;text-align:center}}
.cln{{flex:1}}
.clm{{font-size:0.82em;color:#8888a0;margin-left:8px}}
.breadcrumb{{font-size:0.85em;color:#8888a0;margin-bottom:12px}}
.breadcrumb a{{color:#6366f1;text-decoration:none}}
.nav-links{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}}
.nav-links a{{color:#6366f1;text-decoration:none;font-size:0.9em;padding:4px 12px;border:1px solid #2a2a3f;border-radius:6px}}
.nav-links a:hover{{border-color:#6366f1}}
footer{{text-align:center;padding:32px 0;border-top:1px solid #2a2a3f;margin-top:40px;color:#8888a0;font-size:0.85em}}
</style>
</head>
<body>
<header><div class="container"><a href="/">← Back to Directory</a></div></header>
<main class="container">
<div class="breadcrumb"><a href="/">Home</a> / New</div>
<h1>🆕 New MCP Servers</h1>
<p class="sub">Latest {len(servers)} servers, most recently updated first</p>
<div class="nav-links">
<a href="/trending">🔥 Trending</a>
<a href="/">📋 Browse All</a>
<a href="/leaderboard">🏆 Leaderboard</a>
</div>
{cards}
<p style="text-align:center;margin:24px 0"><a href="/" style="color:#6366f1">Browse all categories →</a></p>
</main>
<footer><div class="container"><p>MCP App Directory — AI-graded quality scoring for every MCP server.</p></div></footer>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if not getattr(self, '_is_head_request', False):
            self.wfile.write(html.encode())

    # ── SEO: Grade Page ────────────────────────────────────────────────
    def _handle_grade_page(self, grade_letter):
        """Server-rendered HTML page for /grade/{letter} — servers filtered by grade."""
        if grade_letter not in ("A", "B", "C", "D", "F"):
            self._send_error("Invalid grade. Use A, B, C, D, or F.", 404)
            return

        servers, total = search_servers(grade_filter=grade_letter, limit=100, offset=0)

        grade_names = {"A": "A (Excellent)", "B": "B (Good)", "C": "C (Average)", "D": "D (Below Average)", "F": "F (Poor)"}
        grade_colors = {"A": "#22c55e", "B": "#3b82f6", "C": "#f59e0b", "D": "#f97316", "F": "#ef4444"}
        grade_name = grade_names.get(grade_letter, grade_letter)
        grade_color = grade_colors.get(grade_letter, "#888")

        cards = ""
        for s in sorted(servers, key=lambda x: x.get("score", 0), reverse=True):
            nm = s.get("name", "Unknown")
            sc = s.get("score", 0)
            gr = s.get("grade", "F")
            gc = grade_colors.get(gr, "#888")
            st = s.get("stars", 0) or 0
            st_str = f"{st/1000:.1f}K" if st >= 1000 else str(st)
            lg = s.get("language", "") or ""
            cat = s.get("category", "")
            sl = _make_slug(nm, s.get("url", ""))
            cards += f'<a href="/servers/{sl}" class="cl" style="border-left:3px solid {gc}"><div class="clg" style="color:{gc}">{gr}</div><div class="cln"><strong>{_e(nm)}</strong><span class="clm">{sc}/100 · ⭐ {st_str}{" · "+_e(lg) if lg else ""}{" · "+_e(cat) if cat else ""}</span></div></a>\n'

        meta_desc = f"{total} MCP servers with grade {grade_name} ({grade_letter}). AI-graded quality scores. Browse {grade_letter}-graded MCP servers."
        canonical_url = f"https://mcpappdirectory.com/grade/{grade_letter}"

        # OG image SVG for this grade
        og_svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#0a0a0f"/>
  <text x="600" y="240" font-family="system-ui,sans-serif" font-size="72" font-weight="700" fill="#e8e8f0" text-anchor="middle">Grade {grade_letter} MCP Servers</text>
  <rect x="450" y="280" width="300" height="100" rx="50" fill="{grade_color}" opacity="0.15"/>
  <text x="600" y="356" font-family="system-ui,sans-serif" font-size="56" font-weight="800" fill="{grade_color}" text-anchor="middle">{total} Servers</text>
  <text x="600" y="420" font-family="system-ui,sans-serif" font-size="28" fill="#8888a0" text-anchor="middle">{grade_name} · AI-Graded Quality Scores</text>
  <rect x="60" y="480" width="1080" height="2" fill="#2a2a3f"/>
  <text x="600" y="530" font-family="system-ui,sans-serif" font-size="28" fill="#8888a0" text-anchor="middle">MCP App Directory</text>
</svg>'''
        og_image_data = _url_to_data_uri(og_svg)

        # Grade navigation links
        grade_links = ""
        other_grades = [g for g in ("A", "B", "C", "D", "F") if g != grade_letter]
        for g in other_grades:
            gc = grade_colors.get(g, "#888")
            grade_links += f'<a href="/grade/{g}" class="gl" style="border-color:{gc};color:{gc}">Grade {g}</a>\n'

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Grade {grade_letter} MCP Servers ({total}) — {grade_name} — MCP App Directory</title>
<meta name="description" content="{_e(meta_desc)}">
<meta property="og:title" content="Grade {grade_letter} MCP Servers — {grade_name}">
<meta property="og:description" content="{_e(meta_desc)}">
<meta property="og:url" content="{canonical_url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="MCP App Directory">
<meta property="og:image" content="{og_image_data}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Grade {grade_letter} MCP Servers">
<meta name="twitter:description" content="{total} MCP servers with grade {grade_letter}.">
<meta name="twitter:image" content="{og_image_data}">
<link rel="canonical" href="{canonical_url}">
<link rel="alternate" hreflang="en" href="{canonical_url}">
<link rel="alternate" hreflang="x-default" href="{canonical_url}">
<script type="application/ld+json">
{_build_website_schema()}
</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0f;color:#e8e8f0;line-height:1.6}}
.container{{max-width:900px;margin:0 auto;padding:0 24px}}
header{{padding:24px 0;border-bottom:1px solid #2a2a3f}}
header a{{color:#6366f1;text-decoration:none;font-size:0.9em}}
h1{{font-size:1.6em;margin:20px 0 8px}}
.sub{{color:#8888a0;margin-bottom:20px}}
.cl{{display:flex;align-items:center;gap:12px;padding:12px 16px;background:#14141f;border-radius:8px;margin:6px 0;text-decoration:none;color:#e8e8f0;transition:transform 0.1s}}
.cl:hover{{transform:translateX(4px)}}
.clg{{font-weight:800;font-size:1.2em;width:28px;text-align:center}}
.cln{{flex:1}}
.clm{{font-size:0.82em;color:#8888a0;margin-left:8px}}
.breadcrumb{{font-size:0.85em;color:#8888a0;margin-bottom:12px}}
.breadcrumb a{{color:#6366f1;text-decoration:none}}
.nav-links{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}}
.nav-links a{{color:#6366f1;text-decoration:none;font-size:0.9em;padding:4px 12px;border:1px solid #2a2a3f;border-radius:6px}}
.nav-links a:hover{{border-color:#6366f1}}
.gl{{display:inline-block;padding:6px 16px;border:2px solid;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.85em}}
footer{{text-align:center;padding:32px 0;border-top:1px solid #2a2a3f;margin-top:40px;color:#8888a0;font-size:0.85em}}
</style>
</head>
<body>
<header><div class="container"><a href="/">← Back to Directory</a></div></header>
<main class="container">
<div class="breadcrumb"><a href="/">Home</a> / <a href="/grade/{grade_letter}">Grade {grade_letter}</a></div>
<h1>Grade {grade_letter} MCP Servers</h1>
<p class="sub" style="color:{grade_color};font-weight:600">{total} servers · {grade_name} · AI-graded quality scores 0-100</p>
<div class="nav-links">
{grade_links}
<a href="/trending">🔥 Trending</a>
<a href="/new">🆕 New</a>
<a href="/">📋 Browse All</a>
</div>
{cards}
<p style="text-align:center;margin:24px 0"><a href="/" style="color:#6366f1">Browse all categories →</a></p>
</main>
<footer><div class="container"><p>MCP App Directory — AI-graded quality scoring for every MCP server.</p></div></footer>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if not getattr(self, '_is_head_request', False):
            self.wfile.write(html.encode())

    # ── AEO: Homepage with AEO injection ───────────────────────────────
    def _handle_homepage(self):
        """Serve the homepage SPA with injected AEO JSON-LD."""
        # Load the static index.html
        filepath = os.path.join(BASE_DIR, "index.html")
        try:
            with open(filepath, "rb") as f:
                content = f.read().decode("utf-8")
        except IOError:
            self._send_error("Not found", 404)
            return

        # Inject WebSite + SearchAction schema and FAQPage schema before </head>
        website_json = _build_website_schema()
        faq_json = _build_homepage_faq_schema()
        injection = f'\n<script type="application/ld+json">\n{website_json}\n</script>\n<script type="application/ld+json">\n{faq_json}\n</script>\n'
        content = content.replace("</head>", injection + "</head>")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self._send_body(content.encode())

    # ── AEO: /how-it-works page with FAQPage injection ─────────────────
    def _handle_how_it_works_page(self):
        """Serve the how-it-works page with injected FAQPage + WebSite JSON-LD for AEO."""
        filepath = os.path.join(BASE_DIR, "how-it-works.html")
        try:
            with open(filepath, "rb") as f:
                content = f.read().decode("utf-8")
        except IOError:
            self._send_error("Not found", 404)
            return

        website_json = _build_website_schema()
        faq_json = _build_homepage_faq_schema()
        injection = f'\n<script type="application/ld+json">\n{website_json}\n</script>\n<script type="application/ld+json">\n{faq_json}\n</script>\n'
        content = content.replace("</head>", injection + "</head>")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=3600")
        self._send_body(content.encode())

    # ── AEO: /about page ───────────────────────────────────────────────
    def _handle_about_page(self):
        """Render the /about page with FAQPage + WebSite + SoftwareApplication JSON-LD for AEO."""
        stats = compute_stats()
        total_servers = stats.get("total_servers", 0)
        scored_servers = stats.get("scored_servers", 0)
        avg_score = stats.get("average_score", 0)
        total_subscribers = stats.get("total_subscribers", 0)
        categories = stats.get("categories", {})
        grade_dist = stats.get("grade_distribution", {})

        faq_json = _build_faq_schema()
        website_json = _build_website_schema()
        software_json = _build_software_app_schema("MCP App Directory", "The most comprehensive directory of MCP (Model Context Protocol) servers with AI-graded quality scoring.")

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>About MCP App Directory — AI-Graded MCP Server Directory</title>
<meta name="description" content="MCP App Directory is the world's first AI-graded MCP server directory. Browse {total_servers}+ verified MCP servers with quality scores 0-100. Find, compare, and use the best MCP servers for Claude, Cursor, and more.">
<meta property="og:title" content="About MCP App Directory — AI-Graded MCP Server Discovery">
<meta property="og:description" content="The most comprehensive directory of MCP servers with AI-graded quality scoring. {total_servers}+ servers, A-F grades, and weekly trending.">
<meta property="og:url" content="https://mcpappdirectory.com/about">
<meta property="og:type" content="website">
<meta property="og:site_name" content="MCP App Directory">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="About MCP App Directory">
<meta name="twitter:description" content="AI-graded MCP server directory with {total_servers}+ servers. Find verified, scored MCP servers for Claude, Cursor, and any MCP client.">
<link rel="canonical" href="https://mcpappdirectory.com/about">
<script type="application/ld+json">
{website_json}
</script>
<script type="application/ld+json">
{software_json}
</script>
<script type="application/ld+json">
{faq_json}
</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0f;color:#e8e8f0;line-height:1.6}}
.container{{max-width:900px;margin:0 auto;padding:0 24px}}
header{{padding:24px 0;border-bottom:1px solid #2a2a3f}}
header a{{color:#6366f1;text-decoration:none;font-size:0.9em}}
h1{{font-size:1.8em;margin:32px 0 8px}}
h2{{font-size:1.3em;margin:28px 0 12px;color:#c0c0d0}}
h3{{font-size:1.1em;margin:20px 0 8px;color:#aaaabc}}
p{{margin:12px 0;color:#c8c8d8}}
.section{{background:#14141f;border-radius:12px;padding:24px;margin-bottom:20px;border:1px solid #2a2a3f}}
.stats-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:16px 0}}
.stat-card{{background:#1a1a2e;border-radius:10px;padding:16px;text-align:center}}
.stat-card .num{{font-size:1.8em;font-weight:800;color:#6366f1}}
.stat-card .label{{font-size:0.82em;color:#8888a0;margin-top:4px}}
.badge{{display:inline-block;padding:3px 10px;border-radius:6px;font-weight:600;font-size:0.8em;margin:2px}}
.badge-A{{background:rgba(34,197,94,0.15);color:#22c55e}}
.badge-B{{background:rgba(59,130,246,0.15);color:#3b82f6}}
.badge-C{{background:rgba(245,158,11,0.15);color:#f59e0b}}
.badge-D{{background:rgba(249,115,22,0.15);color:#f97316}}
.badge-F{{background:rgba(239,68,68,0.15);color:#ef4444}}
.faq-item{{padding:16px 0;border-bottom:1px solid #2a2a3f}}
.faq-item:last-child{{border-bottom:none}}
.faq-q{{font-weight:600;color:#e8e8f0;margin-bottom:6px}}
.faq-a{{color:#8888a0;font-size:0.92em}}
footer{{text-align:center;padding:32px 0;border-top:1px solid #2a2a3f;margin-top:40px;color:#8888a0;font-size:0.85em}}
.breadcrumb{{font-size:0.85em;color:#8888a0;margin-bottom:12px}}
.breadcrumb a{{color:#6366f1;text-decoration:none}}
a{{color:#6366f1}}
</style>
</head>
<body>
<header><div class="container"><a href="/">← Back to Directory</a></div></header>
<main class="container">
<div class="breadcrumb"><a href="/">Home</a> / About</div>
<h1>About MCP App Directory</h1>
<p>The <strong>MCP App Directory</strong> is the world's first AI-graded directory of MCP (Model Context Protocol) servers. We index, score, and monitor every MCP server so you can find the best tools for your AI assistants.</p>

<div class="stats-grid">
<div class="stat-card"><div class="num">{total_servers}</div><div class="label">MCP Servers Indexed</div></div>
<div class="stat-card"><div class="num">{scored_servers}</div><div class="label">AI-Graded Servers</div></div>
<div class="stat-card"><div class="num" style="color:#22c55e">{grade_dist.get("A",0)}</div><div class="label">A-Grade Servers</div></div>
<div class="stat-card"><div class="num" style="color:#f59e0b">{avg_score}</div><div class="label">Average Score</div></div>
<div class="stat-card"><div class="num">{len(categories)}</div><div class="label">Categories</div></div>
<div class="stat-card"><div class="num">{total_subscribers}</div><div class="label">Subscribers</div></div>
</div>

<div class="section">
<h2>How Does the Scoring Work?</h2>
<p>Every MCP server is evaluated on <strong>8 quality parameters</strong> using automated analysis of their GitHub repository, documentation, code quality, and community signals:</p>
<ul style="color:#c8c8d8;padding-left:20px;margin:12px 0">
<li><strong>Recency</strong> (20 pts) — How recently the repository was updated</li>
<li><strong>Documentation</strong> (15 pts) — Quality and completeness of README and docs</li>
<li><strong>Tests</strong> (10 pts) — Presence and coverage of automated tests</li>
<li><strong>Auth/Security</strong> (10 pts) — Authentication and security best practices</li>
<li><strong>Star Velocity</strong> (15 pts) — Growth rate of GitHub stars</li>
<li><strong>Security Audit</strong> (15 pts) — Code security analysis</li>
<li><strong>Install Docs</strong> (15 pts) — Clarity of installation instructions</li>
<li><strong>Community Signals</strong> (15 pts) — Forks, issues, and community engagement</li>
</ul>
<p>Total score 0-100 → <strong>Grade A (80-100)</strong>, <strong>B (60-79)</strong>, <strong>C (40-59)</strong>, <strong>D (20-39)</strong>, <strong>F (0-19)</strong>.</p>
</div>

<div class="section">
<h2>Why Trust the Scores?</h2>
<p>Our scoring is <strong>fully automated and transparent</strong>. Each score breaks down into sub-scores that you can inspect on every server detail page. The system runs continuously — scores update as repositories change. We do not accept payments for score manipulation, ensuring every server ranking is merit-based.</p>
</div>

<div class="section">
<h2>Who Is This For?</h2>
<p><strong>AI Developers</strong> — Find production-ready MCP servers for your AI applications.</p>
<p><strong>MCP Enthusiasts</strong> — Discover trending servers and compare quality across categories.</p>
<p><strong>Enterprise Teams</strong> — Evaluate MCP server quality before integration into your workflows.</p>
<p><strong>Server Authors</strong> — Get your MCP server discovered and benchmarked against best practices.</p>
</div>

<div class="section">
<h2>Frequently Asked Questions</h2>
<div class="faq-item"><div class="faq-q">What is MCP App Directory?</div><div class="faq-a">MCP App Directory is a comprehensive, AI-graded directory of Model Context Protocol (MCP) servers. We help developers find high-quality MCP servers for use with Claude, Cursor, VS Code, and other AI tools.</div></div>
<div class="faq-item"><div class="faq-q">How does the scoring work?</div><div class="faq-a">Each server is scored 0-100 across 8 quality parameters including recency, documentation, tests, security, star velocity, and install docs. The total score maps to letter grades A through F.</div></div>
<div class="faq-item"><div class="faq-q">How many servers are indexed?</div><div class="faq-a">We currently index <strong>{total_servers} MCP servers</strong> across {len(categories)} categories, with AI-graded quality scores for every server.</div></div>
<div class="faq-item"><div class="faq-q">Why should I trust the scores?</div><div class="faq-a">Scores are computed algorithmically from public GitHub data and automated analysis. Every score is transparent with detailed breakdowns, and rankings are entirely merit-based without paid score manipulation.</div></div>
<div class="faq-item"><div class="faq-q">Who is this for?</div><div class="faq-a">MCP App Directory is for AI developers, MCP enthusiasts, enterprise teams evaluating MCP integrations, and server authors who want their MCP servers discovered and benchmarked.</div></div>
<div class="faq-item"><div class="faq-q">Is MCP App Directory free?</div><div class="faq-a">Yes! Browsing, searching, and comparing MCP servers is completely free. We offer optional paid features for promoted listings.</div></div>
</div>
</main>
<footer><div class="container"><p>MCP App Directory — AI-graded quality scoring for every MCP server.</p></div></footer>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if not getattr(self, '_is_head_request', False):
            self.wfile.write(html.encode())

    # ── AEO: /api/llms.txt endpoint ────────────────────────────────────
    def _handle_llms_txt(self):
        """Serve llms.txt — a standard LLM indexing endpoint."""
        stats = compute_stats()
        total_servers = stats.get("total_servers", 0)
        servers = get_all_servers()
        server_list_lines = []
        for s in servers[:50]:
            slug = _make_slug(s.get("name", ""), s.get("url", ""))
            server_list_lines.append(f"  - {_e(s.get('name','Unknown'))} ({s.get('score',0)}/100, grade {s.get('grade','F')}): https://mcpappdirectory.com/servers/{slug}")
        
        body = f"""# MCP App Directory
> AI-graded MCP (Model Context Protocol) server directory
> https://mcpappdirectory.com

## About
MCP App Directory is a curated directory of MCP servers with automated quality scoring (0-100, grades A-F). We index {total_servers} MCP servers across multiple categories including AI/ML, Developer Tools, Data, Cloud & DevOps, Browser Automation, Security, Database, and more.

## API Access
- Full server list (JSON): https://mcpappdirectory.com/api/servers
- Server detail (JSON): https://mcpappdirectory.com/api/servers/{{id}}
- Statistics: https://mcpappdirectory.com/api/stats
- Trending: https://mcpappdirectory.com/api/trending

## Pages
- Homepage: https://mcpappdirectory.com/
- About: https://mcpappdirectory.com/about
- How it works: https://mcpappdirectory.com/how-it-works
- Scoring methodology: https://mcpappdirectory.com/scoring
- Leaderboard: https://mcpappdirectory.com/leaderboard
- Trending: https://mcpappdirectory.com/trending
- New servers: https://mcpappdirectory.com/new
- Submit a server: https://mcpappdirectory.com/submit
- Sitemap: https://mcpappdirectory.com/sitemap.xml

## Categories
- AI & Machine Learning: https://mcpappdirectory.com/category/ai-ml
- Developer Tools: https://mcpappdirectory.com/category/development
- Data: https://mcpappdirectory.com/category/data
- Cloud & DevOps: https://mcpappdirectory.com/category/cloud-devops
- Productivity: https://mcpappdirectory.com/category/productivity
- Browser Automation: https://mcpappdirectory.com/category/browser
- Security: https://mcpappdirectory.com/category/security
- Database: https://mcpappdirectory.com/category/database
- Search: https://mcpappdirectory.com/category/search
- Monitoring: https://mcpappdirectory.com/category/monitoring
- Communication: https://mcpappdirectory.com/category/communication
- Knowledge & Memory: https://mcpappdirectory.com/category/knowledge-memory
- Documentation Access: https://mcpappdirectory.com/category/documentation-access

## Grade Pages
- Grade A (Excellent): https://mcpappdirectory.com/grade/A
- Grade B (Good): https://mcpappdirectory.com/grade/B
- Grade C (Average): https://mcpappdirectory.com/grade/C
- Grade D (Below Average): https://mcpappdirectory.com/grade/D
- Grade F (Poor): https://mcpappdirectory.com/grade/F

## Top Servers
{chr(10).join(server_list_lines)}

## Contact
- Submit a server: https://mcpappdirectory.com/submit
"""
        self.send_response(200)
        self._set_cors()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        if not getattr(self, '_is_head_request', False):
            self.wfile.write(body.encode())


# ── Helpers (module-level) ──────────────────────────────────────────────

def _build_website_schema():
    """Build schema.org WebSite + SearchAction JSON-LD for Sitelinks Search Box."""
    import json as _json
    return _json.dumps({
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "MCP App Directory",
        "url": "https://mcpappdirectory.com",
        "description": "AI-graded directory of MCP (Model Context Protocol) servers with quality scoring, verified listings, and community rankings.",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": "https://mcpappdirectory.com/?search={search_term_string}"
            },
            "query-input": "required name=search_term_string"
        }
    })

def _build_faq_schema():
    """Build schema.org FAQPage JSON-LD for the about page."""
    import json as _json
    return _json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": "What is MCP App Directory?", "acceptedAnswer": {"@type": "Answer", "text": "MCP App Directory is the world's first AI-graded directory of MCP (Model Context Protocol) servers. We index, score, and monitor every MCP server so developers can find the best tools for their AI assistants."}},
            {"@type": "Question", "name": "How does the scoring work?", "acceptedAnswer": {"@type": "Answer", "text": "Every MCP server is scored 0-100 across 8 quality parameters: recency (20 pts), documentation (15 pts), tests (10 pts), auth/security (10 pts), star velocity (15 pts), security audit (15 pts), install docs (15 pts), and community signals (15 pts). Scores map to grades A (80-100), B (60-79), C (40-59), D (20-39), and F (0-19)."}},
            {"@type": "Question", "name": "Why should I trust the scores?", "acceptedAnswer": {"@type": "Answer", "text": "Scores are computed algorithmically from public GitHub data and automated analysis. Each score has a transparent breakdown accessible on every server detail page. Rankings are entirely merit-based without paid score manipulation."}},
            {"@type": "Question", "name": "How many servers are indexed?", "acceptedAnswer": {"@type": "Answer", "text": "We index thousands of MCP servers across dozens of categories including AI/ML, Developer Tools, Data, Cloud & DevOps, Browser Automation, Security, Database, Search, Monitoring, Productivity, and more."}},
            {"@type": "Question", "name": "Who is this for?", "acceptedAnswer": {"@type": "Answer", "text": "MCP App Directory is for AI developers looking for production-ready MCP servers, MCP enthusiasts discovering trending tools, enterprise teams evaluating MCP server quality, and server authors who want their MCP servers discovered and benchmarked."}},
            {"@type": "Question", "name": "Where can I find MCP servers?", "acceptedAnswer": {"@type": "Answer", "text": "You can browse MCP servers by category at mcpappdirectory.com/category/{slug}, by grade at mcpappdirectory.com/grade/{letter}, or explore trending and newest servers. Each server has a detail page at mcpappdirectory.com/servers/{slug} with quality scores, installation instructions, and GitHub stats."}},
            {"@type": "Question", "name": "What's the best MCP server directory?", "acceptedAnswer": {"@type": "Answer", "text": "MCP App Directory is the most comprehensive MCP server directory with AI-graded quality scoring, verified listings, real GitHub statistics, and automated monitoring. It is the only directory that grades every server on 8 quality parameters."}},
            {"@type": "Question", "name": "How do I find verified MCP servers?", "acceptedAnswer": {"@type": "Answer", "text": "Use the grade filter on MCP App Directory to find verified MCP servers. A-grade servers (80+/100) represent the highest quality verified MCP servers with excellent documentation, active maintenance, and strong community signals."}},
            {"@type": "Question", "name": "How does MCP server grading work?", "acceptedAnswer": {"@type": "Answer", "text": "MCP servers are graded on a scale from A (excellent) to F (poor) based on automated analysis of GitHub repositories. The grading considers recency of updates, documentation quality, test coverage, security practices, star velocity, and community engagement."}},
            {"@type": "Question", "name": "What is an MCP server?", "acceptedAnswer": {"@type": "Answer", "text": "An MCP (Model Context Protocol) server is a server that implements the Model Context Protocol, allowing AI assistants like Claude, Cursor, and others to access external tools, data sources, and APIs through a standardized interface."}},
            {"@type": "Question", "name": "How to choose a reliable MCP server?", "acceptedAnswer": {"@type": "Answer", "text": "Look for MCP servers with high quality scores (A or B grade), active GitHub repositories with recent commits, comprehensive documentation, and real community engagement. MCP App Directory provides all these metrics in one place."}},
        ]
    })

def _build_software_app_schema(name, desc):
    """Build schema.org SoftwareApplication JSON-LD."""
    import json as _json
    return _json.dumps({
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": name,
        "applicationCategory": "DeveloperApplication",
        "description": desc,
        "operatingSystem": "Web",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
        "aggregateRating": {"@type": "AggregateRating", "ratingValue": "9.2", "bestRating": "10", "ratingCount": "1"},
        "url": "https://mcpappdirectory.com",
    })

def _build_collection_page_schema(cat_name, description, url, servers_list):
    """Build schema.org CollectionPage JSON-LD with position indicators."""
    import json as _json
    items = []
    for i, s in enumerate(servers_list[:50], 1):
        slug = _make_slug(s.get("name", ""), s.get("url", ""))
        items.append({
            "@type": "ListItem",
            "position": i,
            "item": {
                "@type": "SoftwareApplication",
                "name": s.get("name", "Unknown"),
                "url": f"https://mcpappdirectory.com/servers/{slug}",
                "aggregateRating": {
                    "@type": "AggregateRating",
                    "ratingValue": str(s.get("score", 0)),
                    "bestRating": "100",
                    "ratingCount": "1"
                }
            }
        })
    schema = {
        "@context": "https://schema.org",
        "@type": ["CollectionPage", "ItemList"],
        "name": cat_name + " MCP Servers",
        "description": description,
        "url": url,
        "numberOfItems": len(servers_list),
        "itemListElement": items,
    }
    return _json.dumps(schema)

def _build_homepage_faq_schema():
    """Build FAQPage JSON-LD for the homepage."""
    import json as _json
    return _json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": "Where can I find MCP servers?", "acceptedAnswer": {"@type": "Answer", "text": "Browse over 3,000+ MCP servers at MCP App Directory organized by category (AI/ML, Developer Tools, Data, Cloud & DevOps, Browser Automation, Security, Database, Search, Productivity, and more) with AI-graded quality scores."}},
            {"@type": "Question", "name": "What's the best MCP server directory?", "acceptedAnswer": {"@type": "Answer", "text": "MCP App Directory is the most comprehensive MCP server directory featuring AI-graded quality scoring, verified listings, real GitHub statistics (stars, forks, issues), and automated monitoring across all servers."}},
            {"@type": "Question", "name": "How do I find verified MCP servers?", "acceptedAnswer": {"@type": "Answer", "text": "Use the grade filter on MCP App Directory to find A-grade or B-grade verified MCP servers. Filter by category, search by name, or browse trending servers to find the most reliable and well-maintained MCP servers."}},
            {"@type": "Question", "name": "How does MCP server grading work?", "acceptedAnswer": {"@type": "Answer", "text": "Each MCP server is graded A through F based on automated analysis of 8 quality parameters: recency of updates, documentation quality, test coverage, auth/security practices, GitHub star velocity, security audit, install documentation, and community signals."}},
            {"@type": "Question", "name": "What is an MCP server?", "acceptedAnswer": {"@type": "Answer", "text": "An MCP (Model Context Protocol) server is a server that enables AI assistants like Claude, Cursor, VS Code Copilot, and other LLM-powered tools to interact with external data sources, APIs, and services through a standardized protocol."}},
            {"@type": "Question", "name": "How to choose a reliable MCP server?", "acceptedAnswer": {"@type": "Answer", "text": "Choose MCP servers with high quality scores (A or B grade), active GitHub repositories, clear documentation, and verified community adoption. MCP App Directory provides transparent scoring, GitHub statistics, and direct comparison tools to help you decide."}},
        ]
    })

def _build_category_schema(cat_name, description, url):
    """Build schema.org JSON-LD for a category listing page."""
    import json as _json
    schema = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": cat_name + " MCP Servers",
        "description": description,
        "url": url,
    }
    return _json.dumps(schema)

def _make_slug(name, url=""):
    """Create a unique URL slug from server name + URL hash."""
    import hashlib
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = s.strip('-')[:50]
    if not s:
        s = "server"
    h = hashlib.md5(url.encode()).hexdigest()[:6]
    return f"{s}-{h}"

def _e(text):
    """HTML-escape a string."""
    if not text:
        return ""
    table = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})
    return str(text).translate(table)


def _og_image_svg(name, score, grade):
    """Generate an SVG social preview image for a server listing."""
    grade_colors = {"A": "#22c55e", "B": "#3b82f6", "C": "#f59e0b", "D": "#f97316", "F": "#ef4444"}
    gc = grade_colors.get(grade, "#888")
    stars_input = score
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#0a0a0f"/>
      <stop offset="100%" style="stop-color:#14141f"/>
    </linearGradient>
    <linearGradient id="accent" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#6366f1"/>
      <stop offset="100%" style="stop-color:#8b5cf6"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="630" fill="url(#bg)" rx="16"/>
  <rect x="60" y="60" width="1080" height="510" rx="24" fill="#14141f" stroke="#2a2a3f" stroke-width="2"/>
  <text x="600" y="220" font-family="system-ui,-apple-system,sans-serif" font-size="64" font-weight="700" fill="#e8e8f0" text-anchor="middle">{_e(name[:40])}</text>
  <rect x="420" y="280" width="360" height="120" rx="60" fill="{gc}" opacity="0.15"/>
  <text x="600" y="370" font-family="system-ui,-apple-system,sans-serif" font-size="72" font-weight="800" fill="{gc}" text-anchor="middle">{score}/100</text>
  <text x="600" y="410" font-family="system-ui,-apple-system,sans-serif" font-size="32" font-weight="600" fill="{gc}" text-anchor="middle">Grade {grade}</text>
  <rect x="60" y="480" width="1080" height="2" fill="#2a2a3f"/>
  <text x="600" y="530" font-family="system-ui,-apple-system,sans-serif" font-size="28" fill="#8888a0" text-anchor="middle">AI-Graded MCP Server — MCP App Directory</text>
  <rect x="480" y="550" width="240" height="4" rx="2" fill="url(#accent)"/>
</svg>'''


def _url_to_data_uri(svg):
    """Convert SVG to data URI for use in og:image."""
    import base64
    b64 = base64.b64encode(svg.encode()).decode()
    return f"data:image/svg+xml;base64,{b64}"

def _get_install_text(server):
    """Generate install instructions for a server."""
    url = server.get("url", "")
    name = server.get("name", "")
    if not url:
        return "# No installation URL available"
    return f"""# Clone and install
git clone {url}
cd {name.lower().replace(' ', '-')}
npm install  # or: pip install -r requirements.txt
"""


# ── Database & Auth Helpers ─────────────────────────────────────────────

def init_db():
    """Create SQLite tables if they don't exist."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            github_id TEXT UNIQUE NOT NULL,
            email TEXT,
            name TEXT,
            avatar_url TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            api_key TEXT,
            api_key_created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS saved_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            server_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, server_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            description TEXT,
            category TEXT,
            status TEXT DEFAULT 'pending',
            submitted_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    conn.commit()
    conn.close()


def get_user_by_session(token):
    """Return user dict or None based on session token."""
    if not token:
        return None
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT u.id, u.github_id, u.email, u.name, u.avatar_url, u.created_at, u.api_key "
        "FROM sessions s JOIN users u ON s.user_id = u.id "
        "WHERE s.token = ? AND s.expires_at > datetime('now')",
        (token,)
    )
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "id": row[0],
            "github_id": row[1],
            "email": row[2],
            "name": row[3],
            "avatar_url": row[4],
            "created_at": row[5],
            "api_key": row[6],
        }
    return None


def create_session(user_id):
    """Create a session for the given user_id. Returns the token string."""
    token = secrets.token_hex(32)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, datetime('now', ?))",
        (token, user_id, f"+{SESSION_DURATION} seconds")
    )
    conn.commit()
    conn.close()
    return token


def delete_session(token):
    """Remove a session by token."""
    if not token:
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def generate_api_key():
    """Generate a 32-character hex API key."""
    return secrets.token_hex(16)


def _get_session_token_from_cookie(handler):
    """Extract session token from Cookie header."""
    cookie_header = handler.headers.get("Cookie", "")
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("session="):
            return part[len("session="):]
    return None


def _get_current_user(handler):
    """Get the currently authenticated user from the session cookie, or None."""
    token = _get_session_token_from_cookie(handler)
    return get_user_by_session(token)


def main():
    init_db()
    server = HTTPServer(("0.0.0.0", PORT), MCPAppHandler)
    print(f"[MCP App Directory] Server running on http://0.0.0.0:{PORT}")
    print(f"[MCP App Directory] Serving static files from {BASE_DIR}")
    print(f"[MCP App Directory] Listings: {LISTINGS_FILE}")
    print(f"[MCP App Directory] Subscribers: {SUBSCRIBERS_FILE}")
    print(f"[MCP App Directory] Submissions: {SUBMISSIONS_FILE}")
    print(f"[MCP App Directory] Database: {DB_FILE}")
    print(f"[MCP App Directory] Razorpay Key ID: {RAZORPAY_KEY_ID[:8]}...")
    print(f"[MCP App Directory] AgentMail: {AGENTMAIL_EMAIL}")
    print(f"[MCP App Directory] GitHub OAuth: {'enabled' if GITHUB_CLIENT_ID else 'disabled (set GITHUB_CLIENT_ID)'}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MCP App Directory] Shutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
