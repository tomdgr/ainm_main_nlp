"""Task 25: Find overdue invoice, book reminder fee, create fee invoice, register partial payment."""

import datetime
import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class OverdueInvoiceTask(BaseTask):
    """Tier 3 task: Find an overdue invoice, book a reminder fee as a voucher,
    create a fee invoice to the customer, and register a partial payment.

    Setup creates a customer with an overdue (past-due) invoice.
    The agent must:
    1. Find the overdue invoice
    2. Book reminder fee (debit 1500 receivables, credit 3400 fee income)
    3. Create a new invoice for the fee amount
    4. Register a partial payment on the overdue invoice
    """

    name = "Overdue Invoice + Reminder + Payment"
    tier = 3
    optimal_calls = 8  # find invoice + accounts + paymentType + voucher + order + invoice + payment

    _CUSTOMER_ORG = "876543210"
    _CUSTOMER_NAME = "Testfirma Forfall AS"
    _INVOICE_AMOUNT_EXCL = 25000
    _FEE_AMOUNT = 70
    _PARTIAL_PAYMENT = 5000

    prompts = [
        (
            f"Kunden {_CUSTOMER_NAME} (org.nr {_CUSTOMER_ORG}) har en forfalt faktura. "
            f"Finn den forfalte fakturaen og bokfør en purregebyr på {_FEE_AMOUNT} NOK. "
            f"Soll Kundefordringer (1500), Haben Purregebyr (3400). "
            f"Opprett også en faktura på purregebyret til kunden og send den. "
            f"Registrer i tillegg en delbetaling på {_PARTIAL_PAYMENT} NOK på den forfalte fakturaen."
        ),
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {
            "customer_org": self._CUSTOMER_ORG,
            "customer_name": self._CUSTOMER_NAME,
            "fee_amount": self._FEE_AMOUNT,
            "partial_payment": self._PARTIAL_PAYMENT,
        }
        # Try to parse from prompt in case values differ
        org_match = re.search(r'\b(\d{9})\b', prompt)
        if org_match:
            result["customer_org"] = org_match.group(1)

        fee_match = re.search(r'purregebyr.*?(\d+)\s*NOK', prompt, re.IGNORECASE)
        if fee_match:
            result["fee_amount"] = int(fee_match.group(1))

        payment_match = re.search(r'delbetaling.*?(\d+)\s*NOK', prompt, re.IGNORECASE)
        if payment_match:
            result["partial_payment"] = int(payment_match.group(1))

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Create customer with an overdue invoice."""
        org_nr = expected["customer_org"]
        name = expected["customer_name"]
        amount = self._INVOICE_AMOUNT_EXCL

        print(f"  Setting up overdue invoice task: customer={name}")

        # Ensure bank account
        resp = self._api(base_url, session_token, "GET", "/ledger/account", {
            "number": "1920", "fields": "id,version,bankAccountNumber",
        })
        accounts = resp.get("values", [])
        if accounts and not accounts[0].get("bankAccountNumber"):
            self._api(base_url, session_token, "PUT", f"/ledger/account/{accounts[0]['id']}", json_body={
                "id": accounts[0]["id"], "version": accounts[0]["version"],
                "bankAccountNumber": "12345678903",
            })
            print("  Set bank account number")

        # Create customer
        resp = self._api(base_url, session_token, "GET", "/customer", {
            "organizationNumber": org_nr, "fields": "id,name", "count": 1,
        })
        customers = resp.get("values", [])
        if customers:
            customer_id = customers[0]["id"]
        else:
            resp = self._api(base_url, session_token, "POST", "/customer", json_body={
                "name": name, "organizationNumber": org_nr, "isCustomer": True,
                "email": "test@testforfall.no", "invoiceEmail": "test@testforfall.no",
            })
            customer_id = resp.get("value", {}).get("id")
        print(f"  Customer: id={customer_id}")

        if not customer_id:
            print("  ERROR: Failed to create customer")
            return

        # Create overdue invoice (due date in the past)
        past_date = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
        today = datetime.date.today().isoformat()

        resp = self._api(base_url, session_token, "POST", "/order", json_body={
            "customer": {"id": customer_id},
            "orderDate": past_date,
            "deliveryDate": past_date,
        })
        order_id = resp.get("value", {}).get("id")

        if order_id:
            self._api(base_url, session_token, "POST", "/order/orderline", json_body={
                "order": {"id": order_id},
                "description": "Konsulentarbeid",
                "count": 1,
                "unitPriceExcludingVatCurrency": amount,
                "vatType": {"id": 3},
            })

            resp = self._api(base_url, session_token, "PUT", f"/order/{order_id}/:invoice", params={
                "invoiceDate": past_date,
                "sendToCustomer": False,
            })
            invoice_id = resp.get("value", {}).get("id")
            expected["_overdue_invoice_id"] = invoice_id
            print(f"  Created overdue invoice: id={invoice_id} (due {past_date})")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        org_nr = expected["customer_org"]
        fee_amount = expected["fee_amount"]
        partial_payment = expected["partial_payment"]

        # Find customer
        resp = verifier.get("/customer", {
            "organizationNumber": org_nr, "fields": "id,name", "count": 1,
        })
        customers = resp.get("values", [])
        customer_id = customers[0]["id"] if customers else None

        if not customer_id:
            checks.append(Check(name="Customer found", passed=False, expected=org_nr, actual="NOT FOUND", points=1))
            return checks

        # Get all invoices for this customer
        resp = verifier.get("/invoice", {
            "customerId": customer_id,
            "invoiceDateFrom": "2020-01-01",
            "invoiceDateTo": "2099-12-31",
            "fields": "id,invoiceNumber,amount,amountExcludingVat,amountOutstanding,isCreditNote",
            "count": 20,
        })
        invoices = [inv for inv in resp.get("values", []) if not inv.get("isCreditNote")]

        # Check 1: reminder fee voucher exists (look for posting on account 3400)
        acct_resp = verifier.get("/ledger/account", {"number": "3400", "fields": "id", "count": 1})
        fee_acct_id = acct_resp.get("values", [{}])[0].get("id") if acct_resp.get("values") else None

        has_fee_voucher = False
        if fee_acct_id:
            voucher_resp = verifier.get("/ledger/voucher", {
                "dateFrom": "2026-01-01", "dateTo": "2099-12-31", "count": 50,
            })
            for v in voucher_resp.get("values", [])[-10:]:
                v_detail = verifier.get(f"/ledger/voucher/{v['id']}", {"fields": "id,postings(*)"})
                for p in v_detail.get("value", {}).get("postings", []):
                    if p.get("account", {}).get("id") == fee_acct_id:
                        has_fee_voucher = True
                        break
                if has_fee_voucher:
                    break

        checks.append(Check(
            name="Reminder fee voucher (account 3400)",
            passed=has_fee_voucher,
            expected=f"voucher with posting on 3400 for {fee_amount} NOK",
            actual="FOUND" if has_fee_voucher else "NOT FOUND",
            points=2,
        ))

        # Check 2: fee invoice created (small invoice for the fee amount)
        fee_invoices = [inv for inv in invoices if inv.get("amountExcludingVat", 0) > 0
                        and abs(inv.get("amountExcludingVat", 0) - fee_amount) < 10]
        checks.append(Check(
            name="Fee invoice created",
            passed=len(fee_invoices) > 0,
            expected=f"invoice for ~{fee_amount} NOK",
            actual=f"{len(fee_invoices)} matching invoices",
            points=2,
        ))

        # Check 3: partial payment on overdue invoice
        overdue_id = expected.get("_overdue_invoice_id")
        if overdue_id:
            inv_resp = verifier.get(f"/invoice/{overdue_id}", {
                "fields": "id,amount,amountOutstanding",
            })
            inv = inv_resp.get("value", {})
            total = inv.get("amount", 0)
            outstanding = inv.get("amountOutstanding", total)
            paid = total - outstanding

            checks.append(Check(
                name=f"Partial payment registered ({partial_payment} NOK)",
                passed=paid >= partial_payment - 10,
                expected=f">= {partial_payment} paid",
                actual=f"{paid:.0f} paid (outstanding: {outstanding:.0f})",
                points=3,
            ))
        else:
            checks.append(Check(
                name="Partial payment registered",
                passed=False,
                expected="overdue invoice found",
                actual="setup failed — no overdue invoice ID",
                points=3,
            ))

        return checks
