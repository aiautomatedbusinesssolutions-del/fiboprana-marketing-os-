"""Load sourcing/config.yaml. Fresh read per call — cheap enough that we
don't bother caching, and editing the file between scans takes effect
without restarting the app.
"""

from pathlib import Path
import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
