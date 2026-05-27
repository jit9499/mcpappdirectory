#!/usr/bin/env python3
"""
Categorize all unclassified MCP servers and save the final dataset.
"""
import json, re

servers = json.load(open('/root/mcpappdirectory/listings.json'))

categories = {
    'database': ['sql', 'postgres', 'mysql', 'sqlite', 'mongodb', 'redis', 'database', 'couch', 'dynamo', 'mariadb', 'neo4j', 'cassandra', 'clickhouse', 'supabase', 'firebase', 'prisma', 'drizzle', 'knex'],
    'cloud-platforms': ['aws', 'gcp', 'azure', 'cloud', 'kubernetes', 'k8s', 'docker', 'terraform', 'digitalocean', 'heroku', 'vercel', 'netlify', 'cloudflare', 'fly.io', 'render'],
    'browser-automation': ['browser', 'playwright', 'puppeteer', 'selenium', 'chrome', 'webdriver', 'headless'],
    'search': ['search', 'elastic', 'algolia', 'meilisearch', 'whoosh', 'typesense', 'solr'],
    'communication': ['slack', 'discord', 'telegram', 'email', 'gmail', 'outlook', 'teams', 'whatsapp', 'signal', 'sms', 'mail'],
    'finance': ['finance', 'stock', 'stripe', 'payment', 'coin', 'crypto', 'bank', 'invoice', 'paypal', 'revenue', 'accounting', 'quickbooks', 'xero'],
    'security': ['security', 'auth', 'oauth', 'vault', 'secret', 'encrypt', 'sso', 'jwt', 'okta', 'auth0', 'keycloak'],
    'monitoring': ['monitor', 'grafana', 'prometheus', 'datadog', 'sentry', 'log', 'observability', 'newrelic', 'datadog', 'uptime', 'statuspage'],
    'web-scraping': ['scrape', 'crawl', 'firecrawl', 'jina', 'extract', 'html', 'crawler', 'spider'],
    'documentation': ['documentation', 'docs', 'wiki', 'notion', 'confluence', 'readme', 'docusaurus', 'gitbook'],
    'project-management': ['jira', 'linear', 'asana', 'trello', 'clickup', 'todo', 'task', 'pivotaltracker', 'basecamp', 'monday'],
    'code-analysis': ['linter', 'sonar', 'codeql', 'static-analysis', 'eslint', 'prettier', 'coverage', 'codeclimate'],
    'knowledge-memory': ['memory', 'knowledge', 'vector', 'embedding', 'rag', 'pinecone', 'chroma', 'weaviate', 'qdrant', 'milvus'],
    'research': ['research', 'arxiv', 'paper', 'scholar', 'pubmed', 'science', 'academic'],
    'automation': ['automation', 'zapier', 'make.com', 'n8n', 'workflow', 'pipeline', 'ci/cd', 'jenkins', 'github-actions'],
    'ai-ml': ['ai', 'ml', 'llm', 'openai', 'anthropic', 'claude', 'gpt', 'huggingface', 'replicate', 'model', 'inference', 'train', 'langchain', 'llamaindex'],
    'developer-tools': ['git', 'github', 'api', 'rest', 'graphql', 'cli', 'terminal', 'ssh', 'dev', 'sdk', 'npm', 'pip', 'debug', 'test'],
    'content': ['content', 'blog', 'cms', 'wordpress', 'strapi', 'ghost', 'medium', 'substack', 'newsletter'],
    'maps-location': ['map', 'geo', 'location', 'google-maps', 'openstreetmap', 'places'],
    'analytics': ['analytics', 'google-analytics', 'mixpanel', 'amplitude', 'segment', 'heap', 'plausible'],
}

def classify(item):
    text = f"{item.get('name', '')} {item.get('description', '')} {item.get('url', '')}".lower()
    for cat, keywords in categories.items():
        if any(kw in text for kw in keywords):
            return cat
    return 'developer-tools'

cat_map = {
    'database': 'Database', 'cloud-platforms': 'Cloud Platforms',
    'browser-automation': 'Browser Automation', 'search': 'Search',
    'communication': 'Communication', 'finance': 'Finance',
    'security': 'Security', 'monitoring': 'Monitoring',
    'web-scraping': 'Web Scraping', 'documentation': 'Documentation',
    'project-management': 'Project Management',
    'code-analysis': 'Code Analysis', 'knowledge-memory': 'Knowledge & Memory',
    'research': 'Research & Data', 'automation': 'App Automation',
    'ai-ml': 'AI & Machine Learning', 'developer-tools': 'Developer Tools',
    'content': 'Content Creation', 'maps-location': 'Maps & Location',
    'analytics': 'Analytics'
}

lang_map = {
    '.py': 'Python', '.ts': 'TypeScript', '.js': 'JavaScript',
    '.go': 'Go', '.rs': 'Rust', '.java': 'Java', '.kt': 'Kotlin',
    '.rb': 'Ruby', '.php': 'PHP', '.swift': 'Swift',
}

for s in servers:
    s['category'] = cat_map.get(classify(s), 'Developer Tools')
    # Detect language from URL
    url = s.get('url', '').lower()
    for ext, lang in lang_map.items():
        if ext in url:
            s['language'] = lang
            break
    if 'language' not in s:
        s['language'] = 'Unknown'

# Count
cats = {}
for s in servers:
    c = s['category']
    cats[c] = cats.get(c, 0) + 1

print('Category breakdown:')
for c, n in sorted(cats.items(), key=lambda x: -x[1]):
    print(f'  {c}: {n}')
print(f'\nTotal: {len(servers)} servers')

json.dump(servers, open('/root/mcpappdirectory/listings.json', 'w'), indent=2)
