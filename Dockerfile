# research agent — one image, two entrypoints.
#   * MCP stdio server (default CMD) — an MCP client launches it via `docker run -i`
#   * Autonomous weekly run — override the command (see below) for the Render cron
#
# Secrets are NEVER baked in; they arrive via the environment at runtime:
#   SUPABASE_URL, SUPABASE_ANON_KEY, ANTHROPIC_API_KEY  (EXA_API_KEY optional)
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Interactive door: the MCP stdio server. MCP client config example:
#   command: docker
#   args: [run, -i, --rm, --env-file, .env, fiboprana-research]
CMD ["python", "-m", "fleet.mcp_server"]

# Autonomous door (Render/Railway weekly cron) — override CMD with:
#   python -m fleet.research_run
