FROM python:3.12-slim

# Hosted / container mode: headless Chromium, research and page tools.
# LinkedIn tools are disabled here because signing in needs a visible window on your own machine.
ENV PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOST=0.0.0.0 \
    MCP_TRANSPORT=streamable-http \
    HOME=/tmp \
    BROWSER_HEADLESS=true \
    BROWSER_CHANNEL=chromium \
    WEB_BROWSER_MCP_DISABLE_LINKEDIN=true \
    WEB_BROWSER_MCP_CACHE_DIR=/tmp/web-browser-mcp \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY pyproject.toml README.md CHANGELOG.md LICENSE* ./
COPY server.py browser_manager.py config.py linkedin.py web_research.py ./

RUN pip install --no-cache-dir . \
    && playwright install --with-deps chromium \
    && groupadd -r appuser \
    && useradd -r -g appuser -d /tmp -s /sbin/nologin appuser \
    && chown -R appuser:appuser /app /ms-playwright

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health')" || exit 1

CMD ["python", "server.py"]
