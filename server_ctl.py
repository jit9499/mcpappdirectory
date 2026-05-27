#!/usr/bin/env python3
"""Auto-heal: start or restart the MCP App Directory web server."""
import subprocess
import os
import sys
import time

SITE_DIR = "/root/mcpappdirectory"
PORT = 8081
PID_FILE = "/tmp/mcpappdir.pid"
LOG_FILE = "/tmp/mcpappdir.log"

def is_running():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            pass
    return False

def start():
    if is_running():
        print("Already running")
        return True
    
    with open(LOG_FILE, "w") as logf:
        proc = subprocess.Popen(
            ["python3", "-m", "http.server", str(PORT), "--bind", "0.0.0.0"],
            cwd=SITE_DIR,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True
        )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    
    time.sleep(2)
    if is_running():
        print(f"Started on port {PORT} (pid {proc.pid})")
        return True
    else:
        print("Failed to start")
        return False

def stop():
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            pid = f.read().strip()
        subprocess.run(["kill", pid], capture_output=True)
        os.remove(PID_FILE)
        print(f"Stopped pid {pid}")
    else:
        subprocess.run(["pkill", "-f", f"http.server {PORT}"], capture_output=True)
        print("Stopped all matching processes")

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "start":
        start()
    elif action == "stop":
        stop()
    elif action == "restart":
        stop()
        time.sleep(1)
        start()
    else:
        if is_running():
            with open(PID_FILE) as f:
                pid = f.read().strip()
            print(f"Running (pid {pid}) — http://187.127.153.234:{PORT}")
        else:
            print("Not running")
