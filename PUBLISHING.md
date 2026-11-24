# Publishing Guide — Web Browser MCP

This guide walks you through publishing **web-browser-mcp** so developers and AI agents can discover and install it.

## Checklist

- [x] `README.md` with install instructions
- [x] `pyproject.toml` with metadata and console script
- [x] `smithery.yaml` for Smithery registry
- [x] `LICENSE` (MIT)
- [ ] Publish to PyPI
- [ ] Submit to Smithery
- [ ] Submit to Glama & Pulse MCP directories
- [ ] Set GitHub topics

---

## 1. Publish to PyPI

### One-time setup

```bash
pip install build twine
# Create account at https://pypi.org/account/register/
```

### Build and publish

```bash
cd web-browser-mcp
python -m build
twine upload dist/*
```

### Verify install

```bash
uvx web-browser-mcp --help
# Or add to MCP config:
```

```json
{
  "mcpServers": {
    "web-browser": {
      "command": "uvx",
      "args": ["web-browser-mcp"],
      "env": {
        "BROWSER_CHANNEL": "chrome"
      }
    }
  }
}
```

---

## 2. Submit to Smithery

Smithery is the primary MCP package registry.

1. Go to [https://smithery.ai](https://smithery.ai) and sign in with GitHub
2. Click **Publish Server** → connect your `Pawangunjkar/web-browser-mcp` repository
3. Smithery reads `smithery.yaml` automatically
4. Complete the review flow

**Prerequisite:** Publish to PyPI first (Smithery uses `uvx web-browser-mcp`).

**CLI alternative:**

```bash
npm install -g @smithery/cli
smithery login
smithery publish
```

---

## 3. List on MCP Directories

### Glama.ai

- Visit [https://glama.ai/mcp/servers](https://glama.ai/mcp/servers)
- Submit your repository URL: `https://github.com/Pawangunjkar/web-browser-mcp`

### Pulse MCP

- Visit [https://www.pulsemcp.com](https://www.pulsemcp.com)
- Submit your repository for indexing

These directories feed into community hubs like [apappascs/mcp-servers-hub](https://github.com/apappascs/mcp-servers-hub).

---

## 4. GitHub Discoverability

### Topics

Add these topics on your repo **About** settings:

```
mcp, model-context-protocol, mcp-server, playwright, browser, chrome, web-research, linkedin, automation, python, fastmcp
```

### GitHub CLI

```bash
gh repo edit Pawangunjkar/web-browser-mcp \
  --add-topic mcp,model-context-protocol,mcp-server,playwright,browser,web-research,linkedin,python
```

---

## 5. Recommended README Badges

Add to the top of `README.md`:

```markdown
[![PyPI version](https://badge.fury.io/py/web-browser-mcp.svg)](https://pypi.org/project/web-browser-mcp/)
[![Smithery](https://smithery.ai/badge/web-browser-mcp)](https://smithery.ai/server/web-browser-mcp)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
```

*(Update Smithery badge URL after your server is listed.)*

---

## Support

- **Issues:** https://github.com/Pawangunjkar/web-browser-mcp/issues
- **Author:** Pawan Gunjkar — pawangunjkar@gmail.com
