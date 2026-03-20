"""Models for the game simulator."""

from dataclasses import dataclass, field


@dataclass
class Check:
    """A single field-level verification check."""
    name: str
    passed: bool
    expected: str = ""
    actual: str = ""
    points: float = 1.0

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        detail = f" (expected={self.expected!r}, actual={self.actual!r})" if not self.passed else ""
        return f"  [{status}] {self.name}{detail}"


@dataclass
class TaskResult:
    """Result of running a single task through the simulator."""
    task_id: str
    task_name: str
    tier: int
    prompt: str
    checks: list[Check] = field(default_factory=list)
    api_calls: int = 0
    api_errors: int = 0
    duration_s: float = 0.0
    optimal_calls: int = 1
    error: str = ""

    @property
    def checks_passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total_checks(self) -> int:
        return len(self.checks)

    @property
    def correctness(self) -> float:
        if not self.checks:
            return 0.0
        total_points = sum(c.points for c in self.checks)
        earned_points = sum(c.points for c in self.checks if c.passed)
        return earned_points / total_points if total_points > 0 else 0.0

    @property
    def score(self) -> float:
        c = self.correctness
        if c == 1.0 and self.optimal_calls > 0:
            call_ratio = min(1.0, self.optimal_calls / max(1, self.api_calls))
            error_penalty = max(0.0, 1.0 - (self.api_errors * 0.15))
            efficiency = call_ratio * error_penalty
            return self.tier + (self.tier * efficiency)
        return c * self.tier

    def print_details(self):
        print(f"\n{'='*60}")
        print(f"Task: {self.task_id} — {self.task_name} (Tier {self.tier})")
        print(f"Prompt: {self.prompt[:100]}...")
        print(f"Checks: {self.checks_passed}/{self.total_checks}")
        for check in self.checks:
            print(str(check))
        print(f"API calls: {self.api_calls} (optimal: {self.optimal_calls})")
        print(f"API errors: {self.api_errors}")
        print(f"Duration: {self.duration_s:.1f}s")
        print(f"Correctness: {self.correctness:.2f}")
        print(f"Score: {self.score:.2f} / {self.tier * 2:.1f} max")
        if self.error:
            print(f"Error: {self.error}")
        print(f"{'='*60}")


@dataclass
class SimulatorReport:
    """Aggregated report across all tasks."""
    results: list[TaskResult] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        return sum(r.score for r in self.results)

    @property
    def max_possible(self) -> float:
        return sum(r.tier * 2 for r in self.results)

    def print_summary(self):
        print(f"\n{'='*60}")
        print("SIMULATOR REPORT")
        print(f"{'='*60}")
        print(f"{'Task':<12} {'Checks':<10} {'Calls':<10} {'Errors':<8} {'Score':<8} {'Max':<6}")
        print(f"{'-'*60}")
        for r in self.results:
            print(
                f"{r.task_id:<12} "
                f"{r.checks_passed}/{r.total_checks:<8} "
                f"{r.api_calls:<10} "
                f"{r.api_errors:<8} "
                f"{r.score:<8.2f} "
                f"{r.tier * 2:<6.1f}"
            )
        print(f"{'-'*60}")
        print(f"{'TOTAL':<42} {self.total_score:<8.2f} {self.max_possible:<6.1f}")
        print(f"{'='*60}")
