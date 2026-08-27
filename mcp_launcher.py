"""Standalone launcher for the fleet's MCP server (point an MCP client here).

An MCP client launches this MCP server as a subprocess. Give its config this file's
absolute path with the venv's python. The launcher self-locates the repo root,
puts it on sys.path, and sets it as the working directory, so it runs correctly no
matter what directory the MCP client starts it from (the #1 cause of "module not found"
when wiring MCP servers).

All the actual tools live in fleet/mcp_server.py — this file just imports that
FastMCP instance and runs it over stdio. Nothing is duplicated.

    <venv python>  mcp_launcher.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing the server module loads .env, builds the FastMCP instance, and
# registers the run_research / log_outcome tools.
from fleet.mcp_server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run()  # stdio transport
