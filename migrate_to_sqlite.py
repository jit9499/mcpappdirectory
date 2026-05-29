#!/usr/bin/env python3
"""
Migration script: Move from JSON file storage to SQLite database.

Creates new tables (servers, subscribers, purchases, categories, tags_server)
without affecting existing auth tables (users, sessions, saved_servers).
Also patches the existing submissions table to allow nullable user_id.

Safe to rerun — uses IF NOT EXISTS and INSERT OR IGNORE for idempotency.
"""
import json
import os
import sqlite3
import hashlib
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "mcpapp.db")
LISTINGS_FILE = os.path.join(BASE_DIR, "listings.json")
SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "subscribers.json")
SUBMISSIONS_FILE = os.path.join(BASE_DIR, "submissions.json")
PURCHASES_FILE = os.path.join(BASE_DIR, "purchases.json")


def migrate():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print("=== MCP App Directory: JSON → SQLite Migration ===")

    # ── Step 1: Create new tables (idempotent) ──────────────────────────
    print("\n[1/5] Creating SQLite tables...")

    # First, patch the existing submissions table to allow nullable user_id
    print("   Patching submissions table to allow nullable user_id...")
    try:
        c.execute("PRAGMA foreign_keys=OFF")
        # Check if submissions table already patched (user_id nullable)
        cols = [row[1] for row in c.execute("PRAGMA table_info(submissions)").fetchall()]
        if "user_id" in cols:
            # Recreate with nullable user_id
            c.execute("""
                CREATE TABLE IF NOT EXISTS submissions_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    description TEXT,
                    category TEXT,
                    status TEXT DEFAULT 'pending',
                    submitted_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            c.execute("""
                INSERT OR IGNORE INTO submissions_v2 (id, user_id, name, url, description, category, status, submitted_at)
                SELECT id, user_id, name, url, description, category, status, submitted_at FROM submissions
            """)
            c.execute("DROP TABLE IF EXISTS submissions")
            c.execute("ALTER TABLE submissions_v2 RENAME TO submissions")
            print("   ✓ Submissions table patched")
        else:
            print("   ✓ Submissions table already patched")
        c.execute("PRAGMA foreign_keys=ON")
    except Exception as e:
        print(f"   ! Submissions table already patched: {e}")

    c.executescript("""
        -- Servers table (replaces listings.json)
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'Uncategorized',
            sub_category TEXT DEFAULT '',
            language TEXT DEFAULT '',
            stars INTEGER DEFAULT 0,
            forks INTEGER DEFAULT 0,
            open_issues INTEGER DEFAULT 0,
            license TEXT DEFAULT '',
            topics TEXT DEFAULT '[]',
            created_at TEXT DEFAULT '',
            pushed_at TEXT DEFAULT '',
            score INTEGER DEFAULT 0,
            grade TEXT DEFAULT 'F',
            verified INTEGER DEFAULT 0,
            has_github_stats INTEGER DEFAULT 1,
            install TEXT DEFAULT '',
            score_details TEXT DEFAULT '{}',
            source TEXT DEFAULT 'github',
            last_updated TEXT DEFAULT (datetime('now')),
            added_at TEXT DEFAULT (datetime('now')),
            featured INTEGER DEFAULT 0,
            featured_expires TEXT DEFAULT NULL
        );

        -- Subscribers table (replaces subscribers.json)
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT DEFAULT '',
            source TEXT DEFAULT 'website',
            subscribed_at TEXT DEFAULT (datetime('now')),
            confirmed INTEGER DEFAULT 0,
            confirmed_at TEXT DEFAULT NULL,
            confirm_token TEXT DEFAULT NULL
        );

        -- Purchases table (replaces purchases.json)
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE NOT NULL,
            product TEXT NOT NULL,
            product_name TEXT DEFAULT '',
            amount INTEGER DEFAULT 0,
            currency TEXT DEFAULT 'INR',
            email TEXT DEFAULT '',
            status TEXT DEFAULT 'created',
            payment_id TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            verified_at TEXT DEFAULT NULL
        );

        -- Categories lookup table
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            server_count INTEGER DEFAULT 0
        );

        -- Tags / topics table
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );

        -- Junction: servers <-> tags
        CREATE TABLE IF NOT EXISTS server_tags (
            server_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (server_id, tag_id),
            FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );
    """)

    conn.commit()
    print("   ✓ Core tables created (or already exist)")

    # Create indexes (IF NOT EXISTS)
    print("   Creating indexes...")
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_servers_category ON servers(category)",
        "CREATE INDEX IF NOT EXISTS idx_servers_grade ON servers(grade)",
        "CREATE INDEX IF NOT EXISTS idx_servers_score ON servers(score)",
        "CREATE INDEX IF NOT EXISTS idx_servers_stars ON servers(stars)",
        "CREATE INDEX IF NOT EXISTS idx_servers_name ON servers(name)",
        "CREATE INDEX IF NOT EXISTS idx_servers_url ON servers(url)",
        "CREATE INDEX IF NOT EXISTS idx_servers_verified ON servers(verified)",
        "CREATE INDEX IF NOT EXISTS idx_servers_featured ON servers(featured)",
        "CREATE INDEX IF NOT EXISTS idx_servers_pushed_at ON servers(pushed_at)",
        "CREATE INDEX IF NOT EXISTS idx_subscribers_email ON subscribers(email)",
        "CREATE INDEX IF NOT EXISTS idx_subscribers_confirmed ON subscribers(confirmed)",
        "CREATE INDEX IF NOT EXISTS idx_subscribers_confirm_token ON subscribers(confirm_token)",
        "CREATE INDEX IF NOT EXISTS idx_purchases_order_id ON purchases(order_id)",
        "CREATE INDEX IF NOT EXISTS idx_purchases_status ON purchases(status)",
        "CREATE INDEX IF NOT EXISTS idx_server_tags_server ON server_tags(server_id)",
        "CREATE INDEX IF NOT EXISTS idx_server_tags_tag ON server_tags(tag_id)",
    ]
    for idx in indexes:
        c.execute(idx)
    conn.commit()
    print("   ✓ Indexes created")

    # ── Step 2: Import servers from listings.json ───────────────────────
    print("\n[2/5] Importing servers from listings.json...")

    if not os.path.exists(LISTINGS_FILE):
        print("   ! listings.json not found, skipping")
    else:
        with open(LISTINGS_FILE, "r") as f:
            servers_data = json.load(f)

        if not isinstance(servers_data, list):
            print("   ! listings.json is not a list, skipping")
        else:
            imported = 0
            skipped = 0
            for s in servers_data:
                name = s.get("name", "")
                url = s.get("url", "")
                if not name or not url:
                    skipped += 1
                    continue

                existing = c.execute(
                    "SELECT id FROM servers WHERE url = ?", (url,)
                ).fetchone()

                if existing:
                    skipped += 1
                    continue

                topics = s.get("topics", []) or []
                score_details = s.get("score_details", {}) or {}

                c.execute("""
                    INSERT INTO servers (
                        name, url, description, category, sub_category,
                        language, stars, forks, open_issues, license,
                        topics, created_at, pushed_at, score, grade,
                        verified, has_github_stats, install,
                        score_details, source, last_updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    name,
                    url,
                    s.get("description", ""),
                    s.get("category", "Uncategorized"),
                    s.get("sub_category", ""),
                    s.get("language", ""),
                    s.get("stars", 0) or 0,
                    s.get("forks", 0) or 0,
                    s.get("open_issues", 0) or 0,
                    s.get("license", ""),
                    json.dumps(topics),
                    s.get("created_at", ""),
                    s.get("pushed_at", ""),
                    s.get("score", 0) or 0,
                    s.get("grade", "F"),
                    1 if s.get("verified") else 0,
                    0 if s.get("has_github_stats") == False else 1,
                    s.get("install", ""),
                    json.dumps(score_details),
                    s.get("source", "github"),
                    s.get("pushed_at", "") or s.get("created_at", ""),
                ))

                server_id = c.lastrowid

                # Import topics/tags
                for topic in topics:
                    topic = topic.strip().lower()
                    if not topic:
                        continue
                    c.execute(
                        "INSERT OR IGNORE INTO tags (name) VALUES (?)",
                        (topic,)
                    )
                    tag_row = c.execute(
                        "SELECT id FROM tags WHERE name = ?", (topic,)
                    ).fetchone()
                    if tag_row:
                        c.execute(
                            "INSERT OR IGNORE INTO server_tags (server_id, tag_id) VALUES (?, ?)",
                            (server_id, tag_row["id"])
                        )

                imported += 1

            conn.commit()
            print(f"   ✓ Imported {imported} servers, skipped {skipped} existing")

            # Update category counts
            c.execute("""
                INSERT OR IGNORE INTO categories (name, slug, server_count)
                SELECT category, LOWER(REPLACE(REPLACE(REPLACE(category, ' & ', '-'), ' ', '-'), '--', '-')), COUNT(*)
                FROM servers
                GROUP BY category
            """)
            c.execute("""
                UPDATE categories SET server_count = (
                    SELECT COUNT(*) FROM servers WHERE servers.category = categories.name
                )
            """)
            conn.commit()
            print("   ✓ Category counts updated")

    # ── Step 3: Import subscribers from subscribers.json ────────────────
    print("\n[3/5] Importing subscribers from subscribers.json...")

    if not os.path.exists(SUBSCRIBERS_FILE):
        print("   ! subscribers.json not found, skipping")
    else:
        with open(SUBSCRIBERS_FILE, "r") as f:
            subs_data = json.load(f)

        if not isinstance(subs_data, list):
            print("   ! subscribers.json is not a list, skipping")
        else:
            imported = 0
            skipped = 0
            for s in subs_data:
                email = s.get("email", "").strip().lower()
                if not email:
                    skipped += 1
                    continue

                existing = c.execute(
                    "SELECT id FROM subscribers WHERE email = ?", (email,)
                ).fetchone()

                if existing:
                    c.execute("""
                        UPDATE subscribers SET
                            name = ?, source = ?, confirmed = ?,
                            confirmed_at = ?, confirm_token = ?
                        WHERE email = ?
                    """, (
                        s.get("name", ""),
                        s.get("source", "website"),
                        1 if s.get("confirmed") else 0,
                        s.get("confirmed_at", None),
                        s.get("confirm_token", None),
                        email,
                    ))
                    skipped += 1
                else:
                    c.execute("""
                        INSERT INTO subscribers (email, name, source, subscribed_at, confirmed, confirmed_at, confirm_token)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        email,
                        s.get("name", ""),
                        s.get("source", "website"),
                        s.get("subscribed_at", None),
                        1 if s.get("confirmed") else 0,
                        s.get("confirmed_at", None),
                        s.get("confirm_token", None),
                    ))
                    imported += 1

            conn.commit()
            print(f"   ✓ Imported {imported} subscribers, skipped {skipped} existing")

    # ── Step 4: Import submissions from submissions.json ────────────────
    print("\n[4/5] Importing submissions from submissions.json...")

    if not os.path.exists(SUBMISSIONS_FILE):
        print("   ! submissions.json not found, skipping")
    else:
        with open(SUBMISSIONS_FILE, "r") as f:
            submissions_data = json.load(f)

        if not isinstance(submissions_data, list):
            print("   ! submissions.json is not a list, skipping")
        else:
            imported = 0
            skipped = 0
            for s in submissions_data:
                url = s.get("url", "").strip()
                if not url:
                    skipped += 1
                    continue

                existing = c.execute(
                    "SELECT id FROM submissions WHERE url = ?", (url,)
                ).fetchone()

                if existing:
                    skipped += 1
                    continue

                c.execute("""
                    INSERT INTO submissions (name, url, description, category, status, submitted_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    s.get("name", ""),
                    url,
                    s.get("description", ""),
                    s.get("category", ""),
                    s.get("status", "pending"),
                    s.get("submitted_at", None),
                ))
                imported += 1

            conn.commit()
            print(f"   ✓ Imported {imported} submissions, skipped {skipped} existing")

    # ── Step 5: Import purchases from purchases.json if it exists ───────
    print("\n[5/5] Importing purchases from purchases.json...")

    if os.path.exists(PURCHASES_FILE):
        with open(PURCHASES_FILE, "r") as f:
            purchases_data = json.load(f)

        if isinstance(purchases_data, list):
            imported = 0
            skipped = 0
            for p in purchases_data:
                order_id = p.get("order_id", "")
                if not order_id:
                    skipped += 1
                    continue

                existing = c.execute(
                    "SELECT id FROM purchases WHERE order_id = ?", (order_id,)
                ).fetchone()

                if existing:
                    skipped += 1
                    continue

                c.execute("""
                    INSERT INTO purchases (order_id, product, product_name, amount, currency, email, status, payment_id, created_at, verified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order_id,
                    p.get("product", ""),
                    p.get("product_name", ""),
                    p.get("amount", 0),
                    p.get("currency", "INR"),
                    p.get("email", ""),
                    p.get("status", "created"),
                    p.get("payment_id", None),
                    p.get("created_at", None),
                    p.get("verified_at", None),
                ))
                imported += 1

            conn.commit()
            print(f"   ✓ Imported {imported} purchases, skipped {skipped} existing")
        else:
            print("   ! purchases.json is not a list, skipping")
    else:
        print("   ! purchases.json not found — will be created on first purchase via SQLite")

    conn.close()
    print("\n=== Migration complete! ===")


if __name__ == "__main__":
    migrate()
