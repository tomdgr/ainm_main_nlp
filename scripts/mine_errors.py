#!/usr/bin/env python3
"""Mine error patterns from run logs to suggest validator rules.

Walks example_runs/**/*_run.txt, extracts 4xx/5xx API errors with their
request context, groups by (endpoint_template, error_pattern), and outputs
a ranked list of the most common errors with suggested validator rules.

Usage:
    python scripts/mine_errors.py [--min-count 2] [--log-dir example_runs/]
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

# Regex to capture API result lines: [timestamp] [API] METHOD /path -> STATUS
RE_API = re.compile(
    r"\[[\d:.]+\]\s+\[API\]\s+(GET|POST|PUT|DELETE)\s+(\S+)\s+->\s+(\d+)"
)

# Regex to capture the preceding TOOL_CALL with json_body
RE_TOOL_CALL = re.compile(
    r'\[[\d:.]+\]\s+\[TOOL_CALL\]\s+tripletex_api\((\{.*\})\)'
)

# Regex to capture error response body on the next line
RE_RESPONSE = re.compile(r"^\s+Response:\s+(.+)$")


def normalize_path(path: str) -> str:
    """Convert /employee/123 to /employee/{id}, preserving action segments."""
    return re.sub(r"/\d+", "/{id}", path)


def extract_validation_message(response_text: str) -> str | None:
    """Extract the key validation message from an error response."""
    try:
        body = json.loads(response_text)
        messages = body.get("validationMessages") or []
        if messages:
            parts = []
            for m in messages[:3]:
                field = m.get("field", "")
                msg = m.get("message", "")
                parts.append(f"{field}: {msg}" if field else msg)
            return " | ".join(parts)
        return body.get("message", "")
    except (json.JSONDecodeError, AttributeError):
        return None


def parse_run_file(filepath: Path) -> list[dict]:
    """Extract error API calls with their context from a run log."""
    errors = []
    lines = filepath.read_text(errors="replace").splitlines()

    for i, line in enumerate(lines):
        api_match = RE_API.search(line)
        if not api_match:
            continue

        method, path, status_str = api_match.groups()
        status = int(status_str)
        if status < 400:
            continue

        # Look for the response body on the next line
        response_text = None
        if i + 1 < len(lines):
            resp_match = RE_RESPONSE.match(lines[i + 1])
            if resp_match:
                response_text = resp_match.group(1)

        # Look backwards for the tool call that triggered this
        request_body = None
        for j in range(i - 1, max(i - 5, -1), -1):
            tc_match = RE_TOOL_CALL.search(lines[j])
            if tc_match:
                try:
                    call_data = json.loads(tc_match.group(1))
                    request_body = call_data.get("json_body")
                except json.JSONDecodeError:
                    pass
                break

        validation_msg = extract_validation_message(response_text) if response_text else None

        errors.append({
            "method": method,
            "path": path,
            "path_template": normalize_path(path),
            "status": status,
            "validation_message": validation_msg or f"HTTP {status}",
            "request_body_keys": sorted(request_body.keys()) if isinstance(request_body, dict) else None,
            "file": str(filepath),
        })

    return errors


def main():
    parser = argparse.ArgumentParser(description="Mine error patterns from run logs")
    parser.add_argument("--min-count", type=int, default=2, help="Min occurrences to report")
    parser.add_argument("--log-dir", default="example_runs/", help="Root log directory")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        print(f"Log directory not found: {log_dir}")
        return

    run_files = sorted(log_dir.rglob("*_run.txt"))
    print(f"Scanning {len(run_files)} run files...\n")

    all_errors = []
    for f in run_files:
        all_errors.extend(parse_run_file(f))

    if not all_errors:
        print("No API errors found in logs.")
        return

    # Group by (method, path_template, validation_message)
    pattern_counter: Counter = Counter()
    pattern_examples: dict[tuple, list] = defaultdict(list)

    for err in all_errors:
        key = (err["method"], err["path_template"], err["status"], err["validation_message"])
        pattern_counter[key] += 1
        if len(pattern_examples[key]) < 2:
            pattern_examples[key].append(err)

    # Output ranked
    print(f"Found {len(all_errors)} total API errors across {len(pattern_counter)} unique patterns.\n")
    print("=" * 80)

    for (method, path_tmpl, status, msg), count in pattern_counter.most_common():
        if count < args.min_count:
            continue

        print(f"\n[{count}x] {method} {path_tmpl} -> {status}")
        print(f"  Message: {msg}")

        examples = pattern_examples[(method, path_tmpl, status, msg)]
        for ex in examples:
            print(f"  Example: {ex['method']} {ex['path']}")
            if ex["request_body_keys"]:
                print(f"    Body keys: {ex['request_body_keys']}")

        # Suggest validator rule
        if status == 422 and msg:
            print(f"  SUGGESTION: Add validator rule for {method} {path_tmpl}")
            if "null" in msg.lower() or "må angis" in msg.lower() or "required" in msg.lower():
                field = msg.split(":")[0].strip() if ":" in msg else "unknown"
                print(f"    -> Check required field '{field}' is present before calling")
            elif "ikke lik 0" in msg or "sum" in msg.lower():
                print(f"    -> Validate that postings sum to zero before calling")

    print("\n" + "=" * 80)
    print(f"\nTotal: {len(all_errors)} errors, {len(pattern_counter)} patterns, "
          f"{sum(1 for c in pattern_counter.values() if c >= args.min_count)} with count >= {args.min_count}")


if __name__ == "__main__":
    main()
