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
from src.simulator.tasks.task_voucher import VoucherExpenseTask
from src.simulator.tasks.task_employee_contract import EmployeeContractTask
from src.simulator.tasks.task_ledger_correction import LedgerCorrectionTask
from src.simulator.tasks.task_overdue_invoice import OverdueInvoiceTask
from src.simulator.tasks.task_currency_exchange import CurrencyExchangeTask
from src.simulator.tasks.task_reverse_payment import ReversePaymentTask
from src.simulator.tasks.task_credit_note import CreditNoteTask
from src.simulator.tasks.task_fixed_price_project import FixedPriceProjectTask
from src.simulator.tasks.task_timesheet_invoice import TimesheetInvoiceTask
from src.simulator.tasks.task_dimension_voucher import DimensionVoucherTask
from src.simulator.tasks.task_receipt_expense import ReceiptExpenseTask
from src.simulator.tasks.task_product import ProductTask
from src.simulator.tasks.task_bank_reconciliation import BankReconciliationTask
from src.simulator.tasks.task_expense_analysis import ExpenseAnalysisTask
from src.simulator.tasks.task_supplier_invoice import SupplierInvoiceTask
from src.simulator.tasks.task_travel_expense import TravelExpenseTask
from src.simulator.tasks.task_payroll import PayrollTask
from src.simulator.tasks.task_project_lifecycle import ProjectLifecycleTask
from src.simulator.tasks.task_year_end import YearEndTask
from src.simulator.tasks.task_employee_pdf import EmployeePDFTask
from src.simulator.tasks.task_supplier_invoice_pdf import SupplierInvoicePDFTask
from src.simulator.tasks.task_offer_letter import OfferLetterTask

ALL_TASKS = {
    # Tier 1
    "task_1": DepartmentsTask("task_1"),
    "task_5": DepartmentsTask("task_5"),
    "task_2": CustomerTask("task_2"),
    "task_3": ProductTask("task_3"),                      # Create product with number, price, VAT
    "task_4": SupplierTask("task_4"),
    "task_6": InvoiceTask("task_6"),
    "task_7": PaymentTask("task_7"),
    "task_8": ProjectTask("task_8"),
    # Tier 2
    "task_9": VoucherExpenseTask("task_9"),             # Expense voucher posting
    "task_10": EmployeeContractTask("task_10"),         # Employee with full employment details
    "task_14": CreditNoteTask("task_14"),               # Credit note on existing invoice
    "task_15": FixedPriceProjectTask("task_15"),        # Fixed price project + partial invoice
    "task_16": TimesheetInvoiceTask("task_16"),         # Log hours + project invoice
    "task_17": DimensionVoucherTask("task_17"),         # Accounting dimension + voucher
    "task_11": SupplierInvoiceTask("task_11"),           # Supplier invoice (no PDF, text details)
    "task_13": TravelExpenseTask("task_13"),             # Travel expense with per diem + costs
    "task_12": PayrollTask("task_12"),                   # Payroll: base salary + bonus
    "task_18": ReversePaymentTask("task_18"),           # Reverse bank payment (returned)
    # Tier 3
    "task_22": ReceiptExpenseTask("task_22"),             # Expense from PDF receipt
    "task_27": ReceiptExpenseTask("task_27"),             # Same as task_22 (receipt expense PDF)
    "task_23": BankReconciliationTask("task_23"),          # Bank reconciliation from CSV
    "task_24": LedgerCorrectionTask("task_24"),         # Find & correct 4 ledger errors
    "task_28": ExpenseAnalysisTask("task_28"),           # Expense analysis + projects + activities         # Find & correct 4 ledger errors
    "task_25": OverdueInvoiceTask("task_25"),           # Overdue invoice + reminder + partial payment
    "task_26": CurrencyExchangeTask("task_26"),         # Currency exchange agio/disagio
    "task_29": ProjectLifecycleTask("task_29"),           # Full project lifecycle (budget+hours+supplier cost+invoice)
    "task_30": YearEndTask("task_30"),                     # Year-end closing: depreciation + tax provision
    "task_19": EmployeePDFTask("task_19"),                   # Employee from PDF employment contract
    "task_20": SupplierInvoicePDFTask("task_20"),             # Supplier invoice from PDF (leverandørfaktura)
    "task_21": OfferLetterTask("task_21"),                      # Employee from offer letter PDF (tilbudsbrev)
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

    async def _send_solve(self, prompt: str, task_id: str | None = None,
                          files: list[dict] | None = None) -> dict:
        """Send a task to the local /solve endpoint (async for parallel support)."""
        payload = {
            "prompt": prompt,
            "files": files or [],
            "tripletex_credentials": {
                "base_url": self.base_url,
                "session_token": self.session_token,
            },
            "task_id": task_id,
        }
        headers = {}
        if self.agent_api_key:
            headers["Authorization"] = f"Bearer {self.agent_api_key}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0), verify=False) as client:
            resp = await client.post(f"{self.agent_url}/solve", json=payload, headers=headers)
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

            # Get file attachments (PDFs, receipts) if the task provides them
            files = task.get_files(expected)

            # Send to agent
            start = time.monotonic()
            await self._send_solve(prompt, task_id=task_id, files=files)
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

    async def run_all(self, task_ids: list[str] | None = None,
                      parallel: int = 1) -> SimulatorReport:
        """Run all tasks (or a subset) and return aggregated report.

        Args:
            task_ids: List of task IDs to run (default: all).
            parallel: Number of tasks to run concurrently (default: 1 = sequential).
        """
        ids = task_ids or list(ALL_TASKS.keys())
        report = SimulatorReport()

        if parallel <= 1:
            # Sequential (original behavior)
            for task_id in ids:
                result = await self.run_task(task_id)
                result.print_details()
                report.results.append(result)
        else:
            # Concurrent — run up to `parallel` tasks at a time
            import asyncio
            semaphore = asyncio.Semaphore(parallel)

            async def run_with_semaphore(task_id: str) -> TaskResult:
                async with semaphore:
                    return await self.run_task(task_id)

            results = await asyncio.gather(
                *(run_with_semaphore(tid) for tid in ids),
                return_exceptions=True,
            )
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    result = TaskResult(
                        task_id=ids[i], task_name="Error", tier=1,
                        prompt="", error=str(result),
                    )
                result.print_details()
                report.results.append(result)

        report.print_summary()
        return report
