# 🔌 MCP App Directory

[![Website](https://img.shields.io/badge/Website-mcpappdirectory.com-blue)](https://mcpappdirectory.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/jit9499/mcpappdirectory)](https://github.com/jit9499/mcpappdirectory/stargazers)

> **The curated directory of MCP (Model Context Protocol) servers** — discover, compare, and choose the best MCP servers for your AI agents.

🌐 **Live at [mcpappdirectory.com](https://mcpappdirectory.com)**

---

## ✨ Features

- 🔍 **Search & Browse** — Find MCP servers by category, capability, and use case
- 🏆 **Leaderboard** — Top-ranked servers scored by quality, reliability, and community trust
- ⚖️ **Compare** — Side-by-side comparison of MCP servers
- 📊 **Dashboard** — Analytics and insights about the MCP ecosystem
- 📝 **Submit** — Community-driven submissions of new MCP servers
- 📧 **Newsletter** — Stay updated on new MCP servers and ecosystem news

---

## 🏗️ Architecture

```
mcpappdirectory/
├── server.py              # Main backend server (API + routing)
├── index.html             # Directory listing page
├── leaderboard.html       # Top-ranked MCP servers
├── compare.html           # Side-by-side comparison tool
├── dashboard.html         # Analytics dashboard
├── submit.html            # Submit new MCP server
├── scoring.html           # Scoring methodology
├── about.html / how-it-works.html
├── listings.json          # MCP server data (1,100+ entries)
├── submissions.json       # Community submissions
├── subscribers.json       # Newsletter subscribers
├── score_v3.py            # Scoring algorithm (v3)
├── rescore.py             # Batch rescoring
├── rescore_github.py      # GitHub-based rescoring
├── classify.py            # Server classification
├── enrich_listings.py     # Data enrichment pipeline
├── validate_*.py          # URL & data validation
├── audit.py               # Site auditing
└── health_check.py        # Health monitoring
```

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- pip

### Setup

```bash
# Clone the repo
git clone https://github.com/jit9499/mcpappdirectory.git
cd mcpappdirectory

# Install dependencies
pip install -r requirements.txt  # if available

# Run the server
python server.py
```

---

## 📈 Data Pipeline

The directory uses a multi-stage data pipeline:

1. **Classify** (`classify.py`) — Categorize MCP servers by type and capability
2. **Enrich** (`enrich_listings.py`) — Pull additional metadata (GitHub stars, descriptions, etc.)
3. **Score** (`score_v3.py`) — Rate servers on quality, reliability, and community metrics
4. **Validate** (`validate_*.py`) — Verify URLs and data integrity
5. **Display** — Serve via the web frontend

---

## 🤝 Contributing

We welcome contributions! Here's how:

1. **Submit an MCP Server** — Use the [submission form](https://mcpappdirectory.com/submit.html) on the website
2. **Report Issues** — Open a [GitHub Issue](https://github.com/jit9499/mcpappdirectory/issues)
3. **Code Contributions** — Fork, create a branch, submit a PR

---

## 📊 Stats

- **1,100+** MCP servers listed
- **Multi-version** scoring algorithm
- **Community-driven** submissions
- **SEO-optimized** with sitemap & IndexNow

---

## 📜 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## 🔗 Related Projects

- [awesome-mcp-servers](https://github.com/jit9499/awesome-mcp-servers) — A curated list of MCP servers
- [Model Context Protocol](https://modelcontextprotocol.io/) — The official MCP specification

---

Built with ❤️ by [@jit9499](https://github.com/jit9499)
