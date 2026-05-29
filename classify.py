#!/usr/bin/env python3
"""Auto-categorize all MCP servers in listings.json based on keyword matching (v6 - refined)."""

import json
import re
from collections import defaultdict

# Strategy:
# - AI/ML: broad catch with many keywords (70%+ of servers are AI-related)
# - Data, Communication, Browser, Cloud & DevOps: specific category keywords
# - Security: specific security-only keywords (not generic auth)
# - Development: specific dev tooling keywords, NOT broad language/api matches
# - Productivity: catch-all for everything else

CATEGORY_RULES = {
    "🤖 AI/ML": [
        (["topics"], r'\b(ai(?!-api|-related|-sdk|-server|mcp-ai|\.ai)|ml\b|machine-learning|deep-learning|llm|gpt|chatgpt|claude|neural|nlp|vision|tensorflow|pytorch|openai|gemini|rag|embedding|ai-agents|ai-agent|ai-research|ai-tools|autonomous-agent|llm-framework|llm-agents|generative-ai|computer-vision|speech-recognition|natural-language|transformer|huggingface|langchain|gpt-researcher|openai-agents|multi-agent|agent(?!s\b|ic\b|-|_)|agentic|grok|llmops|embedding-model|text-generation|image-generation|stable-diffusion|docling|semantic-search|prompt-enhancer|deep-research|deepresearch|ai-researcher|ai-trader)\b'),
        (["name"], r'\b(ai[-_]?(agent|cli|tools?|powered|driven|research|researcher|trader)|llm|gpt|claude|gemini|openai|chatgpt|neural|deepseek|rag|embedding|langchain|pytorch|tensorflow|huggingface|machine.?learning|deep.?learning|vision|nlp|grok|semantic|prompt|autonomous)\b', re.IGNORECASE),
        (["description"], r'\b(ai[- ]?agent|llm|gpt|claude|machine learning|deep learning|neural network|natural language|computer vision|generative ai|chatgpt|openai|gemini|rag|embedding|langchain|hugging face|artificial intelligence|large language model|semantic search|prompt enhancement|deep research|ai[- ]powered|intelligent agent|autonomous)\b', re.IGNORECASE),
    ],
    "📊 Data": [
        (["topics"], r'\b(database|sql\b|postgresql|mongodb|analytics|etl\b|pipeline|big-data|data-analysis|data-engineering|data-science|data-pipeline|data-warehouse|data-lake|data-integration|data-catalog|metadata|olap|oltp|clickhouse|duckdb|redis|elasticsearch|kafka|spark|hadoop|hive|presto|trino|dbt|delta-lake|iceberg|parquet|arrow|pandas|numpy|jupyter|notebook|bi\b|business-intelligence|data-viz|data-visualization|data-api|sqlite|timescaledb|cockroachdb|neo4j|graphdb|influxdb|data-federation|data-fabric|data-mesh)\b'),
        (["name"], r'\b(database|sql\b|postgres|mysql|mongo|redis|elastic|kafka|spark|hadoop|analytics|etl\b|pipeline|warehouse|olap|clickhouse|duckdb|trino|presto|dbt\b|jupyter|pandas|numpy|metadata|openmetadata|chatsum|sqlite|neo4j|cockroach|timescale|influx|datagouv|data-api)\b', re.IGNORECASE),
        (["description"], r'\b(database|sql\b|data analysis|data pipeline|data engineering|data science|analytics|etl\b|business intelligence|data visualization|data warehouse|big data|data integration|data lake|olap|oltp|metadata|data discovery|data observability|data catalog|data api)\b', re.IGNORECASE),
    ],
    "💬 Communication": [
        (["topics"], r'\b(communication|slack|discord|telegram|email|messaging|notification|chat\b(?!gpt|bot)|mattermost|teams\b|whatsapp|signal|wechat|irc\b|matrix|rocket\.chat|zulip|webhook|push-notification|realtime|real-time|msteams)\b'),
        (["name"], r'\b(slack|discord|telegram|email|mail|message|chat\b|notification|whatsapp|signal|teams\b|mattermost|irc\b|matrix|webhook|sms\b|communicate)\b', re.IGNORECASE),
        (["description"], r'\b(slack|discord|telegram|email|messaging|chat\b|notification|communication|whatsapp|signal|teams\b|mattermost|sms\b|push notification|webhook|realtime message|messages|conversation)\b', re.IGNORECASE),
    ],
    "🌐 Browser": [
        (["topics"], r'\b(browser|chrome|firefox|website|frontend|html|css|webapp|web-extension|browser-extension|browser-automation|puppeteer|playwright|selenium|headless(?!-ida)|dom|progressive-web-app|pwa|responsive|chrome-devtools)\b'),
        (["name"], r'\b(browser|chrome|firefox|frontend|html|css|webapp|puppeteer|playwright|selenium|chrome-devtools|browser-use)\b', re.IGNORECASE),
        (["description"], r'\b(browser|web browser|chrome|firefox|frontend|website|web app|browser extension|web scraping|browser automation|headless browser|dom|html|css)\b', re.IGNORECASE),
    ],
    "☁️ Cloud & DevOps": [
        (["topics"], r'\b(cloud(?!-flared)|devops|aws|azure|gcp|deployment|infrastructure|monitoring|observability|kubernetes\b|terraform|ansible|pulumi|helm|prometheus|grafana|datadog|newrelic|cloud-native|serverless|lambda|ecs|eks|s3|cloudfront|cloudflare|netlify|vercel|heroku|digitalocean|linode|hosting|cdn|load-balancer|dns|certificate|ssl|tls|networking|container|orchestration|provisioning|iac|infrastructure-as-code|ci\b|cd\b|github-actions|gitlab-ci|jenkins|circleci|google-cloud|google-cloud-run|argocd|gitops|kubefwd|k8s|kubernetes-dashboard|kubernetes-monitoring|kubernetes-tools|kubernetes-ui|kubectl-plugin|nat\b|router|vpn\b|wireguard|dhcp-server|flux-operator)\b'),
        (["name"], r'\b(cloud(?!-flare)|aws|azure|gcp|kubernetes|k8s|terraform|devops|serverless|deploy|infra|monitor|observability|prometheus|grafana|helm|ansible|pulumi|netlify|vercel|heroku|cloudflare|container|kubefwd|cloud-run|radar|kubescape|nat[-_]?router|wireguard|flux-operator|mcphub)\b', re.IGNORECASE),
        (["description"], r'\b(cloud(?!-flare)|devops|infrastructure|deployment|monitoring|observability|container|kubernetes|docker|aws|azure|gcp|serverless|ci/cd|continuous integration|continuous deployment|terraform|helm|provisioning|orchestration|kubernetes ui|kubernetes service|port forwarding|cloud run|google cloud|gitops|argocd|nat router|firewall|vpn|wireguard)\b', re.IGNORECASE),
    ],
    "🔒 Security": [
        # Only strong security signals - not generic auth
        (["topics"], r'\b(vulnerability|malware|privacy|audit|penetration-testing|penetration|exploit|cyber-security|cybersecurity|cryptography|crypto(?!currencies|trading|exchange|assets|market)|hashicorp|vault|secret|secrets-management|supply-chain|dependency-check|sast|dast|sca|harden|ransomware|zero-trust|firewall|ids|ips|waf|sbom|security(?!-related)|mcp-security|security-audit|security-tools|red-team|redteam|redteam-tools|encryption)\b'),
        (["name"], r'\b(vuln|privacy|audit|penetration|exploit|crypt|vault|harden|malware|ransomware|cyber|sbom|secret|zero-trust|firewall|waf|kubescape|hexstrike)\b', re.IGNORECASE),
        (["description"], r'\b(vulnerability|penetration testing|cybersecurity|cryptography|secret management|zero trust|firewall|malware|supply chain security|sbom|security audit|encrypt|decrypt|security tool|red team|cyber security|privacy[-\s]?(first|focused|tool|protection))\b', re.IGNORECASE),
    ],
    "🛠 Development": [
        # Specific development tooling keywords - no broad language/api matches
        (["topics"], r'\b(development|dev-tools|sdk|framework|ide|debugging|testing|compiler|interpreter|linter|formatter|code-quality|code-review|package-manager|dependency|build-tool|editor|vscode|devkit|developer|mcp-client|xcode|xcodebuild|applescript|javafx|winui|tauri|electron|react-native|flutter|unity|unreal|godot|game-engine|mcp-proxy|mcp-gateway|proxy-server)\b'),
        (["name"], r'\b(sdk[-_]|devkit|debug|compiler|lint\b|formatter|git\b|github|terminal|shell|plugin|extension|swagger|openapi|graphql|grpc|middleware|toolkit|toolbox|utilities|mcp-for-beginners|fastapi|builder|semble|cli[-_]?mcp|mcp[-_]?cli|csharp-sdk|xcode|mcp-proxy|curriculum|httprunner|mcpo|proxy-server)\b', re.IGNORECASE),
        (["description"], r'\b(developer tool|sdk|cli tool|command.line tool|framework|code quality|linting|testing tool|debugging tool|compiler|package manager|build tool|git integration|developer experience|development kit|plugin system|extension|toolkit|curriculum|open-source curriculum|proxy|mcp server implementation|debugger)\b', re.IGNORECASE),
    ],
}

CATEGORY_ORDER = [
    "🤖 AI/ML",
    "📊 Data",
    "💬 Communication",
    "🌐 Browser",
    "☁️ Cloud & DevOps",
    "🔒 Security",
    "🛠 Development",
    "⚡ Productivity",
]

def check_field(value, pattern, flags=0):
    if not value:
        return False
    if isinstance(value, list):
        value = ' '.join(v.lower() for v in value)
    else:
        value = str(value).lower()
    return bool(re.search(pattern, value, flags))

def classify_entry(entry):
    name = entry.get("name", "")
    description = entry.get("description", "")
    language = entry.get("language", "")
    topics = entry.get("topics", [])
    
    # Handle specific overrides
    if name == "mcp-for-beginners":
        return "🛠 Development"
    
    fields = {
        "name": str(name).lower(),
        "description": str(description).lower(),
        "language": str(language).lower(),
        "topics": topics,
    }
    
    for category in CATEGORY_ORDER:
        if category == "⚡ Productivity":
            continue
        rules = CATEGORY_RULES.get(category, [])
        for field_list, pattern, *flags in rules:
            flag = flags[0] if flags else 0
            for field_name in field_list:
                if check_field(fields[field_name], pattern, flag):
                    return category
    
    return "⚡ Productivity"


def main():
    with open("/root/mcpappdirectory/listings.json", "r") as f:
        data = json.load(f)
    
    print(f"Loaded {len(data)} entries\n")
    
    counts = defaultdict(int)
    
    for entry in data:
        new_cat = classify_entry(entry)
        entry["category"] = new_cat
        counts[new_cat] += 1
    
    with open("/root/mcpappdirectory/listings.json", "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"{'='*60}")
    print(f"CATEGORIZATION RESULTS")
    print(f"{'='*60}")
    total = sum(counts.values())
    for cat in CATEGORY_ORDER:
        c = counts.get(cat, 0)
        pct = (c / total * 100) if total else 0
        print(f"  {cat:20s} → {c:4d} ({pct:5.1f}%)")
    print(f"{'='*60}")
    print(f"  {'TOTAL':20s} → {total}")
    print(f"{'='*60}")
    
    empty_count = sum(1 for e in data if not e.get("category"))
    print(f"\n✓ Entries with empty category: {empty_count}")
    
    print(f"\n{'='*60}")
    print(f"SPOT CHECKS")
    print(f"{'='*60}")
    checks = ['mcp-for-beginners', 'fastapi_mcp', 'radar', 'chrome-devtools-mcp', 'n8n', 'kubefwd',
              'OpenMetadata', 'slackdump', 'mcp-teams-server', 'cloud-run-mcp', 'XcodeBuildMCP',
              'XHS-Downloader', 'QuantDinger', 'ENScan_GO', 'esp32_nat_router', 'mirobody',
              'github-mcp-server', 'csharp-sdk', 'browser-use-mcp-server', 'cloudsword', 'redd-archiver',
              'wassette', 'ida-mcp-rs', 'one-search-mcp', 'sp500-mcp-server', 'flux-operator', 'mcphub']
    for name in checks:
        for e in data:
            if e['name'] == name:
                print(f"  {name:30s} → {e['category']}")
                break
    
    print(f"\n{'='*60}")
    print(f"SAMPLES PER CATEGORY")
    print(f"{'='*60}")
    for cat in CATEGORY_ORDER:
        samples = [e["name"] for e in data if e.get("category") == cat][:6]
        if samples:
            print(f"\n  {cat} (total: {counts.get(cat, 0)}):")
            for s in samples:
                e = next(x for x in data if x['name'] == s)
                print(f"    - {s}")


if __name__ == "__main__":
    main()
