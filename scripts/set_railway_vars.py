"""One-shot: push the dispatcher's missing env vars from local .env to the
linked Railway service (fiboprana-research-weekly). Prints variable NAMES
only — values never hit the console. --skip-deploys: the vars bake in on the
next deploy (the railway.json cutover push)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env = {}
for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()

to_set = {
    "TYPEFULLY_API_KEY": env.get("TYPEFULLY_API_KEY"),
    "TYPEFULLY_SOCIAL_SET_ID": "316379",
    "XAI_API_KEY": env.get("XAI_API_KEY"),
    "REPLY_CHANNELS": "x",
    "HEALTHCHECKS_URL": env.get("HEALTHCHECKS_URL"),
    "DISPATCH_RESEARCH": "1",
    # GSC read-only token (minted 2026-08-08) so the cloud metrics run
    # snapshots Search Console too, not just local runs.
    "GSC_OAUTH_REFRESH_TOKEN": env.get("GSC_OAUTH_REFRESH_TOKEN"),
}
missing = [k for k, v in to_set.items() if not v]
if missing:
    sys.exit(f"missing in .env: {missing}")

args = ["railway", "variables", "--skip-deploys"]
for k, v in to_set.items():
    args += ["--set", f"{k}={v}"]

r = subprocess.run(args, capture_output=True, text=True, shell=True)
if r.returncode != 0:
    sys.exit(f"railway CLI failed: {r.stderr[:500]}")
print("Set on Railway (values suppressed):")
for k in to_set:
    print(" ", k)
