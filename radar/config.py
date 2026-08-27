"""Load radar/config.yaml. Fresh read per call so edits to feeds/queries
take effect on the next scan without restarting the app.
"""

from pathlib import Path
import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
