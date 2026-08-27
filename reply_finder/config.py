"""Load reply_finder/config.yaml. Fresh read per call so editing the file
between runs takes effect without restarting anything (mirrors sourcing/config.py).
"""

from pathlib import Path
import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
