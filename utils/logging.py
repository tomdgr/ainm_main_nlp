"""Game logging utilities."""

import json
import os
from datetime import datetime

LOG_BASE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")


def open_logs(level: str):
    log_dir = os.path.join(LOG_BASE, level)
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = os.path.join(log_dir, f"{timestamp}.jsonl")
    txt_path = os.path.join(log_dir, f"{timestamp}.txt")
    return open(jsonl_path, "w"), open(txt_path, "w")


def log_turn(f, state_raw: str, actions: list):
    entry = {"state": json.loads(state_raw), "actions": actions}
    f.write(json.dumps(entry) + "\n")
    f.flush()


def log_console(f, line: str):
    f.write(line + "\n")
    f.flush()


"""Example usage:

jsonl_file, txt_file = open_logs("info")
log_turn(jsonl_file, '{"player": "Alice", "score": 10}', ["move1", "move2"])
log_console(txt_file, "Player Alice scored 10 points")

"""
