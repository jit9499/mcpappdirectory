#!/usr/bin/env python3
"""
MCP App Directory — Curation Pipeline
Checks AgentMail inbox for new submissions, validates, and updates the database.
Cron: every 6 hours
"""
import json
import os
import subprocess
import sys
from datetime import datetime

AGENTMAIL_INBOX = "jituhermes@agentmail.to"
SITE_DIR = "/root/mcpappdirectory"
DB_FILE = os.path.join(SITE_DIR, "servers.json")
HTML_FILE = os.path.join(SITE_DIR, "index.html")

def get_api_key():
    key = os.environ.get('AGENTMAIL_API_KEY', '')
    if key:
        return key
    env_path = os.path.expanduser('~/.hermes/.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('AGENTMAIL_API_KEY='):
                    return line.split('=', 1)[1].strip()
    return ''

def read_inbox():
    """Use AgentMail MCP via Node to read inbox threads."""
    api_key = get_api_key()
    if not api_key:
        print("ERROR: No AgentMail API key")
        return []
    
    node_script = f"""
    const {{ spawn }} = require('child_process');
    const server = spawn('npx', ['-y', 'agentmail-mcp'], {{ 
        stdio: ['pipe', 'pipe', 'pipe'], 
        env: {{ ...process.env, AGENTMAIL_API_KEY: '{api_key}' }} 
    }});
    let acc = '';
    let results = [];
    server.stdout.on('data', d => {{ 
        acc += d.toString(); 
        const lines = acc.split('\\n'); 
        acc = lines.pop(); 
        for (const l of lines) {{ 
            if(l.trim()) {{ 
                try {{ 
                    const j = JSON.parse(l); 
                    if(j.result) results.push(j);
                }} catch(e) {{}} 
            }} 
        }} 
    }});
    function send(m) {{ server.stdin.write(JSON.stringify(m) + '\\n'); }}
    send({{jsonrpc:'2.0',id:1,method:'initialize',params:{{protocolVersion:'2024-11-05',capabilities:{{}},clientInfo:{{name:'hermes',version:'1.0'}}}}}});
    setTimeout(() => send({{jsonrpc:'2.0',id:2,method:'notifications/initialized',params:{{}}}}), 100);
    setTimeout(() => send({{jsonrpc:'2.0',id:3,method:'tools/call',params:{{name:'list_threads',arguments:{{inboxId:'{AGENTMAIL_INBOX}',limit:20}}}}}}), 300);
    setTimeout(() => {{ server.kill(); console.log(JSON.stringify(results)); process.exit(0); }}, 5000);
    """
    
    try:
        result = subprocess.run(['node', '-e', node_script], 
                               capture_output=True, text=True, timeout=15)
        output = result.stdout.strip()
        if output:
            return json.loads(output)
    except Exception as e:
        print(f"Error reading inbox: {e}")
    return []

def check_health():
    """Health check endpoint."""
    try:
        import http.client
        conn = http.client.HTTPConnection("localhost", 8081, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        status = resp.status
        conn.close()
        return status == 200
    except:
        return False

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "health"
    
    if action == "health":
        ok = check_health()
        print(f"OK" if ok else "DOWN")
        sys.exit(0 if ok else 1)
    
    elif action == "check-inbox":
        threads = read_inbox()
        print(f"Found {len(threads)} threads")
        for t in threads[:5]:
            print(json.dumps(t, indent=2)[:200])
    
    elif action == "status":
        print(f"MCP App Directory — {SITE_DIR}")
        print(f"Server: http://0.0.0.0:8081")
        print(f"Domains: mcpappdirectory.com ($8K BIN)")
        print(f"DNS: Currently on Afternic (parked). Needs GoDaddy → VPS IP change.")
        print(f"Health: {'OK' if check_health() else 'DOWN'}")
    
    else:
        print(f"Unknown action: {action}")
        print("Usage: curation_pipeline.py [health|check-inbox|status]")
