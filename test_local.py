#!/usr/bin/env python
"""Local smoke test for the Tripletex AI agent.

Runs the game simulator against a locally running agent (started via ./run_local.sh)
and prints results. Use this to verify changes before deploying.

Usage:
    # Terminal 1: start the agent
    ./run_local.sh

    # Terminal 2: run tests
    python test_local.py              # run all tasks
    python test_local.py task_1       # run one task
    python test_local.py task_1 task_4 task_6  # run specific tasks

The simulator is NOT a ground-truth scorer — it's a pre-validation step to catch
regressions and verify the agent starts up and responds correctly.
"""

import asyncio
import glob
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def find_latest_run_log(task_id: str | None = None) -> str | None:
    """Find the most recently written run log file."""
    logs_base = os.path.join(os.path.dirname(__file__), "src", "logs", "runs")
    if not os.path.exists(logs_base):
        return None

    latest_file = None
    latest_mtime = 0.0

    for root, _, files in os.walk(logs_base):
        for f in files:
            if not f.endswith("_run.txt"):
                continue
            if task_id and task_id not in root:
                continue
            fpath = os.path.join(root, f)
            mtime = os.path.getmtime(fpath)
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_file = fpath

    return latest_file


def print_log_summary(log_path: str):
    """Print key lines from a run log: validation warnings, errors, and summary."""
    with open(log_path) as f:
        lines = f.readlines()

    print(f"\n--- Log: {os.path.relpath(log_path)} ---")

    validation_warnings = []
    api_errors = []
    done_line = None

    for line in lines:
        if "[VALIDATION]" in line:
            validation_warnings.append(line.strip())
        elif "[API]" in line and ("-> 4" in line or "-> 5" in line):
            api_errors.append(line.strip())
        elif "[DONE]" in line:
            done_line = line.strip()

    if validation_warnings:
        print(f"  Validation catches ({len(validation_warnings)}):")
        for w in validation_warnings:
            print(f"    {w}")

    if api_errors:
        print(f"  API errors ({len(api_errors)}):")
        for e in api_errors[:5]:
            print(f"    {e}")
        if len(api_errors) > 5:
            print(f"    ... and {len(api_errors) - 5} more")

    if done_line:
        print(f"  {done_line}")

    if not validation_warnings and not api_errors:
        print("  Clean run (no validation warnings, no API errors)")

    print()


async def main():
    from src.simulator.game_simulator import GameSimulator, ALL_TASKS

    # Parse task IDs from command line
    task_ids = sys.argv[1:] if len(sys.argv) > 1 else None

    if task_ids:
        unknown = [t for t in task_ids if t not in ALL_TASKS]
        if unknown:
            print(f"Unknown tasks: {unknown}")
            print(f"Available: {list(ALL_TASKS.keys())}")
            sys.exit(1)

    agent_url = os.getenv("AGENT_URL", "https://localhost:8000")

    print(f"Agent: {agent_url}")
    print(f"Tasks: {task_ids or list(ALL_TASKS.keys())}")
    print()

    # Check agent is reachable
    import httpx
    try:
        async with httpx.AsyncClient(verify=False, timeout=5) as client:
            resp = await client.get(f"{agent_url}/health")
            if resp.status_code != 200:
                print(f"Agent health check failed: {resp.status_code}")
                sys.exit(1)
            print(f"Agent healthy: {resp.json()}")
    except httpx.ConnectError:
        print(f"Cannot connect to {agent_url}")
        print("Start the agent first: ./run_local.sh")
        sys.exit(1)

    print()

    # Track log files created before the run
    logs_base = os.path.join(os.path.dirname(__file__), "src", "logs", "runs")
    pre_existing = set()
    if os.path.exists(logs_base):
        for root, _, files in os.walk(logs_base):
            for f in files:
                if f.endswith("_run.txt"):
                    pre_existing.add(os.path.join(root, f))

    # Run simulator
    sim = GameSimulator(agent_url=agent_url)
    report = await sim.run_all(task_ids=task_ids)

    # Find and display new log files
    new_logs = []
    if os.path.exists(logs_base):
        for root, _, files in os.walk(logs_base):
            for f in files:
                if f.endswith("_run.txt"):
                    fpath = os.path.join(root, f)
                    if fpath not in pre_existing:
                        new_logs.append(fpath)

    if new_logs:
        new_logs.sort(key=os.path.getmtime)
        print(f"\n{'='*60}")
        print(f"RUN LOG ANALYSIS ({len(new_logs)} new logs)")
        print(f"{'='*60}")

        total_validations = 0
        total_api_errors = 0

        for log_path in new_logs:
            with open(log_path) as f:
                content = f.read()
            validations = content.count("[VALIDATION]")
            api_errs = sum(1 for line in content.split("\n") if "[API]" in line and ("-> 4" in line or "-> 5" in line))
            total_validations += validations
            total_api_errors += api_errs
            print_log_summary(log_path)

        print(f"{'='*60}")
        print(f"Total validation catches: {total_validations} (saved {total_validations} API calls)")
        print(f"Total API errors: {total_api_errors}")
        print(f"{'='*60}")

    # Exit code based on whether any task fully failed
    failed = [r for r in report.results if r.error]
    if failed:
        print(f"\n{len(failed)} task(s) had errors — check logs above")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
