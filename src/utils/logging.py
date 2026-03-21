"""Logging utilities for the Tripletex AI agent."""

import io
import json
import logging
import os
import sys
from datetime import datetime, timezone


LOG_BASE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")


def setup_logging():
    """Configure structured logging for the application.

    Uses JSON format when running in Cloud Run (LOG_FORMAT=json),
    otherwise uses a readable text format for local development.
    """
    log_format = os.getenv("LOG_FORMAT", "text")
    level = os.getenv("LOG_LEVEL", "INFO").upper()

    if log_format == "json":
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        handlers=[handler],
        force=True,
    )

    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


class JsonFormatter(logging.Formatter):
    """JSON log formatter compatible with Google Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


# ---------------------------------------------------------------------------
# RunLogger — per-request logging of full agent runs
# ---------------------------------------------------------------------------

def _get_host_prefix() -> str:
    """Get a folder prefix from LOG_HOST, K_SERVICE and K_REVISION (Cloud Run) env vars."""
    host = os.getenv("LOG_HOST") or os.getenv("K_SERVICE") or "local"
    revision = os.getenv("K_REVISION", "")
    # Sanitize for use as folder name
    host = host.replace("https://", "").replace("http://", "").replace("/", "_").strip(".")
    if revision:
        return f"{host}/{revision}"
    return host


class RunLogger:
    """Captures a full agent run into two separate log buffers:
    - run log: detailed step-by-step trace of the agent (prompt, tool calls, responses, result)
    - console log: standard application log lines emitted during the run

    Controlled by LOG_STORAGE env var:
      "local" (default) -> writes to src/logs/runs/{host}/
      "gcs"             -> uploads to GCS bucket (LOG_BUCKET env var) under runs/{host}/

    Set LOG_HOST env var to override the folder prefix (defaults to K_SERVICE on Cloud Run, or "local").
    """

    def __init__(self, task_id: str | None = None):
        self.host_prefix = _get_host_prefix()
        self.task_id = task_id  # Optional subfolder for task-specific logs
        self.attempt_number: int = 0  # Set by leaderboard detection
        self.timestamp: str | None = None  # Set at finalize() with end-time
        self._run_buf = io.StringIO()
        self._console_buf = io.StringIO()

        # Attach a logging handler that captures console output for this run
        self._log_handler = logging.StreamHandler(self._console_buf)
        self._log_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logging.getLogger().addHandler(self._log_handler)

    # -- Run log helpers --

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def log(self, step_type: str, message: str):
        """Write a line to the run log."""
        self._run_buf.write(f"[{self._ts()}] [{step_type}] {message}\n")

    def log_prompt(self, prompt: str, file_count: int):
        self.log("PROMPT", f"({file_count} files attached)")
        self._run_buf.write(f"{prompt}\n")
        self._run_buf.write("-" * 80 + "\n")

    def log_tool_call(self, tool_name: str, args: dict):
        args_str = json.dumps(args, ensure_ascii=False, default=str)
        self.log("TOOL_CALL", f"{tool_name}({args_str})")

    def log_tool_result(self, tool_name: str, result: str):
        # Truncate very long results for readability
        truncated = result[:2000] + "..." if len(result) > 2000 else result
        self.log("TOOL_RESULT", f"{tool_name} -> {truncated}")

    def log_api_call(self, method: str, path: str, status: int, duration_ms: float, body_preview: str = ""):
        self.log("API", f"{method} {path} -> {status} ({duration_ms:.0f}ms)")
        if body_preview:
            truncated = body_preview[:1000] + "..." if len(body_preview) > 1000 else body_preview
            self._run_buf.write(f"  Response: {truncated}\n")

    def log_thinking(self, text: str):
        """Log a thinking block from extended thinking mode."""
        truncated = text[:5000] + "..." if len(text) > 5000 else text
        self.log("THINKING", truncated)

    def log_model_response(self, text: str):
        self.log("MODEL", text[:3000])

    def log_result(self, duration_s: float, api_calls: int, api_errors: int, usage: str):
        self._run_buf.write("=" * 80 + "\n")
        self.log("DONE", f"duration={duration_s:.1f}s api_calls={api_calls} api_errors={api_errors}")
        self.log("USAGE", usage)

    def log_error(self, error: str):
        self.log("ERROR", error)

    def log_validation_warning(self, method: str, path: str, warnings: list[str]):
        warning_text = "; ".join(warnings)
        self.log("VALIDATION", f"{method} {path} blocked: {warning_text}")

    # -- Finalize & Save --

    def finalize(self):
        """Set the final timestamp to NOW (run end time). Call before save()."""
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]

    async def save(self):
        """Persist both log files to local disk or GCS."""
        # Ensure timestamp is set (fallback if finalize() wasn't called)
        if not self.timestamp:
            self.finalize()
        # Detach the console handler
        logging.getLogger().removeHandler(self._log_handler)

        storage = os.getenv("LOG_STORAGE", "local")
        if storage == "gcs":
            await self._save_gcs()
        else:
            self._save_local()

    def _log_subdir(self) -> str:
        """Build the log subdirectory path, including task_id if set."""
        parts = ["runs", self.host_prefix]
        task = self.task_id or "unclassified"
        parts.append(task)
        return os.path.join(*parts)

    def _file_prefix(self) -> str:
        """Build filename prefix: no_{attempt}_{timestamp}."""
        return f"no_{self.attempt_number}_{self.timestamp}"

    def _save_local(self):
        log_dir = os.path.join(LOG_BASE, self._log_subdir())
        os.makedirs(log_dir, exist_ok=True)

        prefix = self._file_prefix()
        run_path = os.path.join(log_dir, f"{prefix}_run.txt")
        console_path = os.path.join(log_dir, f"{prefix}_console.txt")

        with open(run_path, "w") as f:
            f.write(self._run_buf.getvalue())
        with open(console_path, "w") as f:
            f.write(self._console_buf.getvalue())

        logging.getLogger(__name__).info(f"Run logs saved: {run_path}")

    async def _save_gcs(self):
        bucket_name = os.getenv("LOG_BUCKET", "")
        if not bucket_name:
            logging.getLogger(__name__).warning("LOG_STORAGE=gcs but LOG_BUCKET not set, falling back to local")
            self._save_local()
            return

        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(bucket_name)

            subdir = self._log_subdir()
            file_prefix = self._file_prefix()

            run_blob = bucket.blob(f"{subdir}/{file_prefix}_run.txt")
            run_blob.upload_from_string(self._run_buf.getvalue(), content_type="text/plain")

            console_blob = bucket.blob(f"{subdir}/{file_prefix}_console.txt")
            console_blob.upload_from_string(self._console_buf.getvalue(), content_type="text/plain")

            logging.getLogger(__name__).info(f"Run logs uploaded to gs://{bucket_name}/{subdir}/{file_prefix}_*.txt")
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to upload logs to GCS: {e}, falling back to local")
            self._save_local()
