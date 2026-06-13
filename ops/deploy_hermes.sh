#!/bin/bash
# Hermes Resilience Suite Deploy Script v2
# No external Python dependencies - pure bash + stdlib
set -e
echo "=== HERMES RESILIENCE SUITE v2 ==="

# Step 1: Create directories
mkdir -p /root/.hermes/scripts /root/.hermes/logs
echo "[1/6] Directories ready"

# Step 2: Write context_guard.py
cat > /root/.hermes/scripts/context_guard.py << 'GUARD_EOF'
#!/usr/bin/env python3
"""Pre-flight context guard - prevents 413/400 context overflow"""
import json, os, sys
from pathlib import Path
from datetime import datetime

HERMES_DIR = Path(os.environ.get("HERMES_DIR", "/root/.hermes"))
GUARD_LOG = HERMES_DIR / "logs" / "context_guard.log"

MODEL_LIMITS = {"deepseek": 32000, "claude": 180000, "default": 28000}
SAFETY = 0.80

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] GUARD: {msg}"
    print(line, file=sys.stderr)
    try:
        with open(str(GUARD_LOG), "a") as f:
            f.write(line + "\n")
    except: pass

def tokens(text):
    return len(text) // 4

def limit(model):
    name = (model or "").lower()
    for k, v in MODEL_LIMITS.items():
        if k in name:
            return int(v * SAFETY)
    return int(MODEL_LIMITS["default"] * SAFETY)

def trim(messages, max_tok):
    sys_m = [m for m in messages if m.get("role") == "system"]
    other = [m for m in messages if m.get("role") != "system"]
    budget = max_tok - tokens(json.dumps(sys_m))
    if budget <= 0:
        for m in sys_m:
            if isinstance(m.get("content"), str):
                m["content"] = m["content"][:2000] + "\n...[truncated]"
        return sys_m + other[-5:]
    kept, used = [], 0
    for msg in reversed(other):
        t = tokens(json.dumps(msg))
        if used + t <= budget:
            kept.insert(0, msg)
            used += t
        else:
            break
    log(f"Trimmed {len(other)-len(kept)} messages")
    return sys_m + kept

def guard(payload, model=""):
    msgs = payload.get("messages", [])
    if not msgs:
        return payload
    lim = limit(model)
    total = tokens(json.dumps(msgs))
    log(f"Context: {total}/{lim} tokens (model: {model or 'unknown'})")
    if total <= lim:
        log("OK")
        return payload
    log(f"OVER LIMIT - trimming")
    payload["messages"] = trim(msgs, lim)
    new_total = tokens(json.dumps(payload["messages"]))
    log(f"Trimmed to {new_total} tokens ({len(msgs)} -> {len(payload['messages'])} messages)")
    return payload

if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.load(sys.stdin)
        json.dump(guard(data, model), sys.stdout)
    except Exception as e:
        log(f"ERROR: {e}")
        sys.exit(1)
GUARD_EOF
chmod +x /root/.hermes/scripts/context_guard.py
echo "[2/6] context_guard.py installed"

# Step 3: Write watchdog.py
cat > /root/.hermes/scripts/watchdog.py << 'WATCH_EOF'
#!/usr/bin/env python3
"""Self-healing watchdog - monitors Hermes health, auto-restarts, alerts"""
import os, sys, time, subprocess
from pathlib import Path
from datetime import datetime

HERMES_DIR = Path(os.environ.get("HERMES_DIR", "/root/.hermes"))
LOGS_DIR = HERMES_DIR / "logs"
LOG_FILE = LOGS_DIR / "watchdog.log"
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "1285517048")
INTERVAL = int(os.environ.get("WATCHDOG_INTERVAL", "60"))
SERVICES = ["hermes-realtime.service"]
PROCESSES = ["hermes_realtime.py", "neural_backbone_listener.py"]
_cooldown = {}

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] WATCHDOG: {msg}"
    print(line, flush=True)
    try:
        with open(str(LOG_FILE), "a") as f:
            f.write(line + "\n")
    except: pass

def alert(key, msg):
    now = time.time()
    if now - _cooldown.get(key, 0) < 300:
        return
    _cooldown[key] = now
    log(f"ALERT: {msg}")
    if TG_TOKEN:
        try:
            import urllib.request, json
            data = json.dumps({"chat_id": TG_CHAT, "text": f"🚨 HERMES WATCHDOG\n{msg}"}).encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log(f"Alert failed: {e}")

def svc_active(name):
    r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
    return r.stdout.strip() == "active"

def restart_svc(name):
    log(f"Restarting {name}...")
    r = subprocess.run(["systemctl", "restart", name], capture_output=True, text=True)
    if r.returncode == 0:
        log(f"Restarted {name} OK")
        alert(f"restart_{name}", f"Auto-restarted {name}")
    else:
        log(f"FAIL: {r.stderr[:200]}")
        alert(f"fail_{name}", f"FAILED to restart {name}!\n{r.stderr[:200]}")

def proc_running(name):
    return subprocess.run(["pgrep", "-f", name], capture_output=True).returncode == 0

def disk_free():
    try:
        r = subprocess.run(["df", "-BG", "/"], capture_output=True, text=True)
        return int(r.stdout.strip().split("\n")[-1].split()[3].rstrip("G"))
    except:
        return 999

def check():
    issues = []
    for svc in SERVICES:
        if not svc_active(svc):
            restart_svc(svc)
            issues.append(f"restarted {svc}")
    for proc in PROCESSES:
        if not proc_running(proc):
            alert(f"proc_{proc}", f"Process {proc} not found!")
            issues.append(f"missing {proc}")
    free = disk_free()
    if free < 2:
        alert("disk", f"Low disk: {free}GB free!")
        issues.append(f"disk {free}GB")
    if not issues:
        log("Health OK")

if __name__ == "__main__":
    log("Watchdog started")
    while True:
        try:
            check()
        except Exception as e:
            log(f"Loop error: {e}")
        time.sleep(INTERVAL)
WATCH_EOF
chmod +x /root/.hermes/scripts/watchdog.py
echo "[3/6] watchdog.py installed"

# Step 4: Watchdog systemd service
cat > /etc/systemd/system/hermes-watchdog.service << 'SVC_EOF'
[Unit]
Description=Hermes Self-Healing Watchdog
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /root/.hermes/scripts/watchdog.py
Restart=always
RestartSec=30
Environment=HERMES_DIR=/root/.hermes
Environment=WATCHDOG_INTERVAL=60

[Install]
WantedBy=multi-user.target
SVC_EOF
systemctl daemon-reload
systemctl enable hermes-watchdog.service 2>/dev/null
systemctl restart hermes-watchdog.service
echo "[4/6] watchdog service active"

# Step 5: Update config.yaml (no PyYAML needed)
cp /root/.hermes/config.yaml /root/.hermes/config.yaml.bak 2>/dev/null || true
python3 << 'PY_EOF'
import json, os

config_path = "/root/.hermes/config.yaml"
lines = []
if os.path.exists(config_path):
    with open(config_path) as f:
        lines = f.readlines()

# Remove old model/token/hook lines
clean = [l for l in lines if not any(k in l for k in [
    "primary_model:", "fallback_model:", "emergency_model:",
    "max_tokens:", "pre_task_hook:", "context_guard_enabled:",
    "model:", "deepseek-v4-flash"
])]

# Add new config at the end
additions = """
# Model hierarchy (updated by deploy script)
primary_model: "anthropic/claude-opus-4"
fallback_model: "deepseek/deepseek-v4-pro"
emergency_model: "deepseek-chat"
max_tokens: 8192
context_guard_enabled: true
pre_task_hook: "python3 /root/.hermes/scripts/context_guard.py"
"""
clean.append(additions)

with open(config_path, "w") as f:
    f.writelines(clean)

print("[5/6] config.yaml updated - claude-opus-4 / deepseek-v4-pro / deepseek-chat")
PY_EOF

# Step 6: Verify
echo "[6/6] Verification:"
ls -la /root/.hermes/scripts/ 2>/dev/null
systemctl is-active hermes-watchdog.service
grep -E "model|max_tokens" /root/.hermes/config.yaml 2>/dev/null | head -5
echo ""
echo "=== DEPLOY COMPLETE ==="
