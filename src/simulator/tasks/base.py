"""Base class for task definitions."""

from abc import ABC, abstractmethod

import httpx

from src.simulator.models import Check


class BaseTask(ABC):
    """Base class for all task definitions."""

    def __init__(self, task_id: str):
        self.task_id = task_id

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def tier(self) -> int: ...

    @property
    @abstractmethod
    def optimal_calls(self) -> int: ...

    @property
    @abstractmethod
    def prompts(self) -> list[str]: ...

    @abstractmethod
    def extract_expected(self, prompt: str) -> dict:
        """Parse the prompt to extract expected entity values."""
        ...

    @abstractmethod
    def check(self, verifier, expected: dict) -> list[Check]:
        """Query the API and verify the task result."""
        ...

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Create any prerequisite data needed before the agent runs.

        Override in subclasses that need pre-populated data (e.g., existing invoices).
        Default does nothing.
        """
        pass

    def _api(self, base_url: str, session_token: str, method: str, path: str,
             params: dict | None = None, json_body: dict | None = None) -> dict:
        """Helper for making API calls during setup."""
        with httpx.Client(timeout=30.0) as client:
            resp = client.request(
                method=method,
                url=f"{base_url}{path}",
                auth=("0", session_token),
                params=params,
                json=json_body,
            )
            if resp.status_code >= 400:
                print(f"  Setup {method} {path} -> {resp.status_code}: {resp.text[:200]}")
                return {}
            try:
                return resp.json()
            except Exception:
                return {}
