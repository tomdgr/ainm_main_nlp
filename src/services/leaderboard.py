"""Leaderboard service for auto-detecting task IDs and tracking scores per revision."""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

LOG_BASE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")


def _revision_log_dir() -> str:
    """Get the revision-level log directory for score files."""
    host = os.getenv("LOG_HOST") or os.getenv("K_SERVICE") or "local"
    revision = os.getenv("K_REVISION", "")
    host = host.replace("https://", "").replace("http://", "").replace("/", "_").strip(".")
    parts = ["runs", host]
    if revision:
        parts.append(revision)
    return os.path.join(*parts)


class LeaderboardService:
    """Polls the competition leaderboard API to detect task IDs and track scores.

    Uses a claimed-set + asyncio.Lock to prevent concurrent background detections
    from matching the same leaderboard entry.
    """

    TEAM_ID = "3b61f6c8-acfb-4833-a39f-959ab17fe224"

    def __init__(self, api_base: str = "https://api.ainm.no"):
        self._url = f"{api_base}/tripletex/leaderboard/{self.TEAM_ID}"
        self._initial_saved = False
        self._claimed: set[tuple[str, str]] = set()  # (task_id, last_attempt_at)
        self._lock = asyncio.Lock()

    async def _fetch(self) -> list[dict] | None:
        """Fetch leaderboard data from the API."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self._url)
                if resp.status_code != 200:
                    logger.warning(f"Leaderboard API returned {resp.status_code}")
                    return None
                return resp.json()
        except Exception as e:
            logger.error(f"Leaderboard fetch failed: {e}")
            return None

    def _save_score_file(self, filename: str, tasks: list[dict]):
        """Save scores to a JSON file in the revision log directory."""
        storage = os.getenv("LOG_STORAGE", "local")
        subdir = _revision_log_dir()

        scores = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "total_score": sum(t.get("best_score", 0) for t in tasks),
            "tasks": {
                t["tx_task_id"]: {
                    "best_score": t.get("best_score", 0),
                    "total_attempts": t.get("total_attempts", 0),
                    "last_attempt_at": t.get("last_attempt_at"),
                }
                for t in tasks
            },
        }

        if storage == "gcs":
            self._save_score_gcs(subdir, filename, scores)
        else:
            self._save_score_local(subdir, filename, scores)

    def _save_score_local(self, subdir: str, filename: str, scores: dict):
        log_dir = os.path.join(LOG_BASE, subdir)
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, filename)
        with open(path, "w") as f:
            json.dump(scores, f, indent=2, ensure_ascii=False)
        logger.info(f"Scores saved: {path}")

    def _save_score_gcs(self, subdir: str, filename: str, scores: dict):
        bucket_name = os.getenv("LOG_BUCKET", "")
        if not bucket_name:
            self._save_score_local(subdir, filename, scores)
            return
        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(f"{subdir}/{filename}")
            blob.upload_from_string(
                json.dumps(scores, indent=2, ensure_ascii=False),
                content_type="application/json",
            )
            logger.info(f"Scores uploaded to gs://{bucket_name}/{subdir}/{filename}")
        except Exception as e:
            logger.error(f"Failed to upload scores to GCS: {e}")
            self._save_score_local(subdir, filename, scores)

    def _match_task_unclaimed(
        self, tasks: list[dict], run_end_time: datetime, max_age_s: float
    ) -> tuple[str | None, int, str | None]:
        """Find the closest unclaimed task match.

        Returns:
            (task_id, attempt_number, last_attempt_at_str) or (None, 0, None)
        """
        best_task_id = None
        best_attempts = 0
        best_attempt_at = None
        best_diff = timedelta(seconds=max_age_s)

        for task in tasks:
            attempt_at_str = task.get("last_attempt_at")
            if not attempt_at_str:
                continue

            task_id = f"task_{task['tx_task_id']}"

            # Skip already claimed entries
            if (task_id, attempt_at_str) in self._claimed:
                continue

            attempt_at = datetime.fromisoformat(attempt_at_str)
            if attempt_at.tzinfo is None:
                attempt_at = attempt_at.replace(tzinfo=timezone.utc)

            diff = abs(run_end_time - attempt_at)
            if diff < best_diff:
                best_diff = diff
                best_task_id = task_id
                best_attempts = task.get("total_attempts", 0)
                best_attempt_at = attempt_at_str

        return best_task_id, best_attempts, best_attempt_at

    async def detect_task(
        self,
        run_end_time: datetime,
        max_age_s: float = 60.0,
        retries: int = 5,
        retry_delay_s: float = 3.0,
    ) -> tuple[str | None, int]:
        """Detect which task was just attempted, with retry and claim-based dedup.

        The lock serializes concurrent detections so that when multiple runs finish
        at the same time, each one claims a different leaderboard entry.

        Returns:
            Tuple of (task_id like "task_07", attempt_number) or (None, 0) if no match.
        """
        async with self._lock:
            for attempt in range(retries):
                tasks = await self._fetch()
                if not tasks:
                    logger.warning(f"Leaderboard fetch failed (attempt {attempt + 1}/{retries})")
                    await asyncio.sleep(retry_delay_s)
                    continue

                # Save initial scores on first fetch (once per revision lifetime)
                if not self._initial_saved:
                    self._save_score_file("initial_scores.json", tasks)
                    self._initial_saved = True

                # Always update latest scores
                self._save_score_file("latest_scores.json", tasks)

                # Try to match (excluding already-claimed entries)
                task_id, attempts, attempt_at = self._match_task_unclaimed(
                    tasks, run_end_time, max_age_s
                )

                if task_id and attempt_at:
                    self._claimed.add((task_id, attempt_at))
                    logger.info(
                        f"Detected task: {task_id} (attempt #{attempts}, "
                        f"try {attempt + 1}/{retries}, claimed {attempt_at})"
                    )
                    return task_id, attempts

                if attempt < retries - 1:
                    logger.info(f"No unclaimed match (try {attempt + 1}/{retries}), retrying in {retry_delay_s}s...")
                    await asyncio.sleep(retry_delay_s)

            logger.warning(f"No task matched after {retries} retries — saving to unclassified/")
            return None, 0
