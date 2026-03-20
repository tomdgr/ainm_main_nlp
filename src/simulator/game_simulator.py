"""Game simulator for local testing of the Tripletex AI agent."""

import logging
import os
import random
import time

import httpx
from dotenv import load_dotenv

from src.simulator.models import SimulatorReport, TaskResult

load_dotenv()

logger = logging.getLogger(__name__)

# Import all task definitions
from src.simulator.tasks.task_departments import DepartmentsTask
from src.simulator.tasks.task_customer import CustomerTask
from src.simulator.tasks.task_supplier import SupplierTask
from src.simulator.tasks.task_invoice import InvoiceTask
from src.simulator.tasks.task_payment import PaymentTask
from src.simulator.tasks.task_project import ProjectTask

ALL_TASKS = {
    "task_1": DepartmentsTask("task_1"),
    "task_2": CustomerTask("task_2"),
    "task_4": SupplierTask("task_4"),
    "task_6": InvoiceTask("task_6"),
    "task_7": PaymentTask("task_7"),
    "task_8": ProjectTask("task_8"),
}


class TripletexVerifier:
    """Client for querying the Tripletex API to verify task results."""

    def __init__(self, base_url: str, session_token: str):
        self.base_url = base_url.rstrip("/")
        self.auth = ("0", session_token)
        self.client = httpx.Client(timeout=30.0)

    def get(self, path: str, params: dict | None = None) -> dict:
        resp = self.client.get(
            f"{self.base_url}{path}",
            auth=self.auth,
            params=params or {},
        )
        if resp.status_code >= 400:
            logger.warning(f"Verifier GET {path} -> {resp.status_code}: {resp.text[:200]}")
            return {}
        return resp.json()

    def close(self):
        self.client.close()


class GameSimulator:
    """Runs task prompts against the local agent and scores results."""

    def __init__(
        self,
        agent_url: str = "https://localhost:8000",
        base_url: str | None = None,
        session_token: str | None = None,
        agent_api_key: str | None = None,
        log_dir: str | None = None,
    ):
        self.agent_url = agent_url.rstrip("/")
        self.base_url = base_url or os.getenv("API_URL", "https://kkpqfuj-amager.tripletex.dev/v2")
        self.session_token = session_token or os.getenv("SESSION_TOKEN", "")
        self.agent_api_key = agent_api_key or os.getenv("AGENT_API_KEY", "")
        self.log_dir = log_dir or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "logs", "simulator"
        )

    def _send_solve(self, prompt: str, task_id: str | None = None) -> dict:
        """Send a task to the local /solve endpoint."""
        payload = {
            "prompt": prompt,
            "files": [],
            "tripletex_credentials": {
                "base_url": self.base_url,
                "session_token": self.session_token,
            },
            "task_id": task_id,
        }
        headers = {}
        if self.agent_api_key:
            headers["Authorization"] = f"Bearer {self.agent_api_key}"
        with httpx.Client(timeout=300, verify=False) as client:
            resp = client.post(f"{self.agent_url}/solve", json=payload, headers=headers)
            return resp.json()

    def _read_run_log(self, task_id: str) -> tuple[int, int]:
        """Read the latest run log to extract api_calls and api_errors.

        Logs are stored under src/logs/runs/{host}/{task_id}/ by the RunLogger.
        """
        logs_base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "runs")

        # Find the latest _run.txt file across all subdirs
        latest_file = None
        latest_mtime = 0

        if os.path.exists(logs_base):
            for root, _, files in os.walk(logs_base):
                for f in files:
                    if f.endswith("_run.txt"):
                        fpath = os.path.join(root, f)
                        mtime = os.path.getmtime(fpath)
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                            latest_file = fpath

        if not latest_file:
            return 0, 0

        api_calls = 0
        api_errors = 0
        with open(latest_file) as f:
            for line in f:
                if "[DONE]" in line:
                    for part in line.split():
                        if part.startswith("api_calls="):
                            api_calls = int(part.split("=")[1])
                        elif part.startswith("api_errors="):
                            api_errors = int(part.split("=")[1])

        return api_calls, api_errors

    async def run_task(self, task_id: str, prompt: str | None = None) -> TaskResult:
        """Run a single task and score the result."""
        if task_id not in ALL_TASKS:
            return TaskResult(
                task_id=task_id,
                task_name="Unknown",
                tier=1,
                prompt=prompt or "",
                error=f"Unknown task: {task_id}",
            )

        task = ALL_TASKS[task_id]

        # Pick a prompt
        if prompt is None:
            prompt = random.choice(task.prompts)

        logger.info(f"Running {task_id}: {prompt[:80]}...")

        verifier = TripletexVerifier(self.base_url, self.session_token)

        try:
            # Extract expected values from the prompt
            expected = task.extract_expected(prompt)

            # Setup prerequisites (e.g., pre-create customer + invoice for payment tasks)
            task.setup(self.base_url, self.session_token, expected)

            # Send to agent
            start = time.monotonic()
            self._send_solve(prompt, task_id=task_id)
            duration = time.monotonic() - start

            # Read run log for call/error counts
            api_calls, api_errors = self._read_run_log(task_id)

            # Verify results
            checks = task.check(verifier, expected)

            return TaskResult(
                task_id=task_id,
                task_name=task.name,
                tier=task.tier,
                prompt=prompt,
                checks=checks,
                api_calls=api_calls,
                api_errors=api_errors,
                duration_s=duration,
                optimal_calls=task.optimal_calls,
            )

        except Exception as e:
            logger.exception(f"Task {task_id} failed: {e}")
            return TaskResult(
                task_id=task_id,
                task_name=task.name,
                tier=task.tier,
                prompt=prompt,
                error=str(e),
            )
        finally:
            verifier.close()

    async def run_all(self, task_ids: list[str] | None = None) -> SimulatorReport:
        """Run all tasks (or a subset) and return aggregated report."""
        ids = task_ids or list(ALL_TASKS.keys())
        report = SimulatorReport()

        for task_id in ids:
            result = await self.run_task(task_id)
            result.print_details()
            report.results.append(result)

        report.print_summary()
        return report
