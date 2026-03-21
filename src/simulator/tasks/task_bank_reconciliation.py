"""Task 23: Reconcile bank statement (CSV) against open invoices and supplier payments."""

import base64
import random

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask

# Fixed test data for reproducible simulations
_CUSTOMERS = [
    {"name": "Nordfjord AS", "orgNumber": "987654001"},
    {"name": "Sørvik AS", "orgNumber": "987654002"},
    {"name": "Vestland AS", "orgNumber": "987654003"},
]

_SUPPLIERS = [
    {"name": "Leverandor Fjell AS"},
    {"name": "Leverandor Berg AS"},
]

_INVOICE_AMOUNTS = [12500.0, 8750.0, 18000.0]  # Amounts for 3 customer invoices
_PARTIAL_PAYMENT_AMOUNT = 6000.0  # Partial payment on invoice #3
_SUPPLIER_AMOUNTS = [9500.0, 14200.0]
_BANK_FEE = 250.0
_INTEREST = 127.50


class BankReconciliationTask(BaseTask):
    """Tier 3 task: Reconcile a CSV bank statement against Tripletex invoices.

    Pre-creates customers with invoices, then generates a CSV bank statement
    that the agent must reconcile by registering payments and posting vouchers.
    """

    name = "Bank Reconciliation (CSV)"
    tier = 3
    optimal_calls = 10  # paymentType + invoices + 3 payments + suppliers + 2 supplier vouchers + 2 misc vouchers

    prompts = [
        "Avstem bankutskriften (vedlagt CSV) mot åpne fakturaer i Tripletex. Match innbetalinger til kundefakturaer og utbetalinger til leverandørfakturaer. Håndter delbetalinger korrekt.",
        "Reconcile the bank statement (attached CSV) with open invoices in Tripletex. Match incoming payments to customer invoices and outgoing payments to supplier invoices. Handle partial payments correctly.",
    ]

    def __init__(self, task_id: str):
        super().__init__(task_id)
        self._invoice_ids: list[int] = []
        self._invoice_numbers: list[int] = []
        self._customer_ids: list[int] = []
        self._supplier_ids: list[int] = []

    def extract_expected(self, prompt: str) -> dict:
        return {
            "invoice_amounts": _INVOICE_AMOUNTS,
            "partial_amount": _PARTIAL_PAYMENT_AMOUNT,
            "supplier_amounts": _SUPPLIER_AMOUNTS,
            "bank_fee": _BANK_FEE,
            "interest": _INTEREST,
        }

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Pre-create customers, invoices (charged), and suppliers."""
        self._invoice_ids = []
        self._invoice_numbers = []
        self._customer_ids = []
        self._supplier_ids = []

        # Ensure bank account 1920 has a bank account number
        resp = self._api(base_url, session_token, "GET", "/ledger/account", {
            "number": "1920", "fields": "id,version,bankAccountNumber",
        })
        accts = resp.get("values", [])
        if accts and not accts[0].get("bankAccountNumber"):
            self._api(base_url, session_token, "PUT", f"/ledger/account/{accts[0]['id']}", json_body={
                "id": accts[0]["id"], "version": accts[0]["version"],
                "bankAccountNumber": "12345678903",
            })

        # Create customers and invoices
        for i, cust in enumerate(_CUSTOMERS):
            # Create or find customer
            resp = self._api(base_url, session_token, "GET", "/customer", {
                "organizationNumber": cust["orgNumber"], "fields": "id", "count": 1,
            })
            if resp.get("values"):
                cust_id = resp["values"][0]["id"]
            else:
                resp = self._api(base_url, session_token, "POST", "/customer", json_body={
                    "name": cust["name"], "organizationNumber": cust["orgNumber"], "isCustomer": True,
                })
                cust_id = resp.get("value", {}).get("id")
            self._customer_ids.append(cust_id)
            print(f"  Customer {cust['name']}: id={cust_id}")

            # Create invoice for this customer
            amount = _INVOICE_AMOUNTS[i]
            resp = self._api(base_url, session_token, "POST", "/invoice", json_body={
                "customer": {"id": cust_id},
                "invoiceDate": "2026-01-10",
                "invoiceDueDate": "2026-02-10",
                "orders": [{
                    "customer": {"id": cust_id},
                    "orderDate": "2026-01-10",
                    "deliveryDate": "2026-01-10",
                    "orderLines": [{
                        "description": f"Tjenester {cust['name']}",
                        "count": 1,
                        "unitPriceExcludingVatCurrency": amount,
                        "vatType": {"id": 0},
                    }],
                }],
            })
            inv = resp.get("value", {})
            inv_id = inv.get("id")
            inv_num = inv.get("invoiceNumber")
            self._invoice_ids.append(inv_id)
            self._invoice_numbers.append(inv_num)
            print(f"  Invoice #{inv_num}: id={inv_id}, amount={amount}")

        # Create suppliers
        for sup in _SUPPLIERS:
            resp = self._api(base_url, session_token, "GET", "/supplier", {
                "name": sup["name"], "fields": "id", "count": 1,
            })
            if resp.get("values"):
                sup_id = resp["values"][0]["id"]
            else:
                resp = self._api(base_url, session_token, "POST", "/supplier", json_body={
                    "name": sup["name"],
                })
                sup_id = resp.get("value", {}).get("id")
            self._supplier_ids.append(sup_id)
            print(f"  Supplier {sup['name']}: id={sup_id}")

    def get_files(self, expected: dict) -> list[dict]:
        """Generate a CSV bank statement matching the pre-created invoices."""
        lines = ["Dato;Forklaring;Inn;Ut;Saldo"]
        balance = 100000.0

        # Customer payments (3 invoices — last one is partial)
        for i, (cust, amount) in enumerate(zip(_CUSTOMERS, _INVOICE_AMOUNTS)):
            if i == 2:
                # Partial payment on third invoice
                pay_amount = _PARTIAL_PAYMENT_AMOUNT
            else:
                pay_amount = amount
            balance += pay_amount
            inv_num = self._invoice_numbers[i] if i < len(self._invoice_numbers) else i + 1
            date = f"2026-01-{16 + i * 3}"
            lines.append(f"{date};Innbetaling fra {cust['name']} / Faktura {inv_num};{pay_amount:.2f};;{balance:.2f}")

        # Supplier payments
        for i, (sup, amount) in enumerate(zip(_SUPPLIERS, _SUPPLIER_AMOUNTS)):
            balance -= amount
            date = f"2026-01-{27 + i * 2}"
            lines.append(f"{date};Betaling Leverandor {sup['name']};;-{amount:.2f};{balance:.2f}")

        # Bank fee
        balance -= _BANK_FEE
        lines.append(f"2026-02-03;Bankgebyr;;-{_BANK_FEE:.2f};{balance:.2f}")

        # Interest income
        balance += _INTEREST
        lines.append(f"2026-02-05;Renteinntekter;{_INTEREST:.2f};;{balance:.2f}")

        csv_content = "\n".join(lines)
        return [{
            "filename": "bankutskrift.csv",
            "content_base64": base64.b64encode(csv_content.encode()).decode(),
            "mime_type": "text/csv",
        }]

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []

        # Check 1: Customer invoice payments registered
        payments_registered = 0
        for i, inv_id in enumerate(self._invoice_ids):
            if not inv_id:
                continue
            resp = verifier.get(f"/invoice/{inv_id}", {
                "fields": "id,invoiceNumber,amount,amountOutstanding",
            })
            inv = resp.get("value", {})
            outstanding = inv.get("amountOutstanding", inv.get("amount", 0))
            original = inv.get("amount", 0)

            if i == 2:
                # Partial payment — outstanding should be reduced but not zero
                if outstanding < original and outstanding > 0:
                    payments_registered += 1
            else:
                # Full payment — outstanding should be 0
                if outstanding == 0 or outstanding < original * 0.1:
                    payments_registered += 1

        checks.append(Check(
            name="Customer invoice payments registered",
            passed=payments_registered >= 2,
            expected="at least 2 of 3 invoices paid",
            actual=f"{payments_registered}/3 payments registered",
            points=3,
        ))

        # Check 2: Partial payment handled correctly
        if len(self._invoice_ids) >= 3 and self._invoice_ids[2]:
            resp = verifier.get(f"/invoice/{self._invoice_ids[2]}", {
                "fields": "id,amount,amountOutstanding",
            })
            inv3 = resp.get("value", {})
            outstanding = inv3.get("amountOutstanding", 0)
            original = inv3.get("amount", 0)
            partial_ok = 0 < outstanding < original

            checks.append(Check(
                name="Partial payment correctly applied",
                passed=partial_ok,
                expected=f"outstanding between 0 and {original}",
                actual=f"outstanding={outstanding}",
                points=2,
            ))

        # Check 3: Supplier payment vouchers exist (posting on account 2400)
        acct_resp = verifier.get("/ledger/account", {
            "number": "2400", "fields": "id", "count": 1,
        })
        acct_2400_id = acct_resp.get("values", [{}])[0].get("id") if acct_resp.get("values") else None

        supplier_vouchers = 0
        if acct_2400_id:
            resp = verifier.get("/ledger/voucher", {
                "dateFrom": "2026-01-01", "dateTo": "2099-12-31",
                "count": 20, "sorting": "-id",
            })
            for v in resp.get("values", [])[:15]:
                v_detail = verifier.get(f"/ledger/voucher/{v['id']}", {
                    "fields": "id,postings(*)",
                })
                for p in v_detail.get("value", {}).get("postings", []):
                    if p.get("account", {}).get("id") == acct_2400_id and p.get("amountGross", 0) > 0:
                        supplier_vouchers += 1
                        break

        checks.append(Check(
            name="Supplier payment vouchers",
            passed=supplier_vouchers >= 1,
            expected="at least 1 supplier payment voucher",
            actual=f"{supplier_vouchers} found",
            points=2,
        ))

        # Check 4: Bank fee/interest voucher exists
        acct_resp = verifier.get("/ledger/account", {
            "number": "8050", "fields": "id", "count": 1,
        })
        acct_8050_id = acct_resp.get("values", [{}])[0].get("id") if acct_resp.get("values") else None

        misc_vouchers = 0
        if acct_8050_id:
            resp = verifier.get("/ledger/voucher", {
                "dateFrom": "2026-02-01", "dateTo": "2099-12-31",
                "count": 20, "sorting": "-id",
            })
            for v in resp.get("values", [])[:10]:
                v_detail = verifier.get(f"/ledger/voucher/{v['id']}", {
                    "fields": "id,postings(*)",
                })
                for p in v_detail.get("value", {}).get("postings", []):
                    if p.get("account", {}).get("id") == acct_8050_id:
                        misc_vouchers += 1
                        break

        checks.append(Check(
            name="Interest/fee vouchers",
            passed=misc_vouchers >= 1,
            expected="at least 1 interest or fee voucher",
            actual=f"{misc_vouchers} found",
            points=1,
        ))

        return checks
