import json
from pathlib import Path

with Path(__file__).with_name("config.json").open(encoding="utf-8") as f:
    config_file = json.load(f)
