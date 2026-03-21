"""Task 18: Reverse a bank payment that was returned (make invoice outstanding again)."""

import datetime
import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class ReversePaymentTask(BaseTask):
    """Tier 2 task: A customer's payment was returned by the bank.
    The agent must find the paid invoice and reverse the payment voucher
    so the invoice shows as outstanding again.

    Setup creates a customer, an invoice, and registers a payment on it.
    The agent must reverse the payment.
    """

    name = "Reverse Bank Payment"
    tier = 2
    optimal_calls = 5  # find customer + find invoice + find voucher + reverse voucher

    prompts = [
        'Betalingen fra Nordlys AS (org.nr 943251876) for fakturaen "Konsulenttimer" (12500 NOK eksklusiv MVA) ble returnert av banken. Reverser betalingen slik at fakturaen igjen viser utestående beløp.',
        'The payment from Clearwater Ltd (org no. 891234567) for the invoice "IT Support" (8500 NOK excluding VAT) was returned by the bank. Reverse the payment so the invoice shows the outstanding amount again.',
        'Die Zahlung von Bergkraft GmbH (Org.-Nr. 912345678) für die Rechnung "Beratung" (15000 NOK ohne MwSt.) wurde von der Bank zurückgegeben. Stornieren Sie die Zahlung, damit die Rechnung wieder den offenen Betrag anzeigt.',
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        org_match = re.search(r'\b(\d{9})\b', prompt)
        if org_match:
            result["organizationNumber"] = org_match.group(1)

        amount_match = re.search(r'(\d[\d\s]*\d)\s*(?:NOK|kr)', prompt)
        if amount_match:
            result["amount_excl_vat"] = float(amount_match.group(1).replace(" ", ""))

        name_patterns = [
            r'(?:fra|from|von)\s+(.+?)(?:\s*\()',
        ]
        for pat in name_patterns:
            m = re.search(pat, prompt, re.IGNORECASE)
            if m:
                result["customer_name"] = m.group(1).strip()
                break

        desc_match = re.search(r'"([^"]+)"', prompt)
        if desc_match:
            result["description"] = desc_match.group(1)

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Create customer + invoice + payment (so agent can reverse the payment)."""
        today = datetime.date.today().isoformat()
        org_nr = expected.get("organizationNumber", "")
        name = expected.get("customer_name", "Test Customer")
        amount = expected.get("amount_excl_vat", 10000)
        description = expected.get("description", "Service")

        print(f"  Setting up reverse payment task: customer={name}, amount={amount}")

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
                "email": "test@test.no", "invoiceEmail": "test@test.no",
            })
            customer_id = resp.get("value", {}).get("id")
        print(f"  Customer: id={customer_id}")

        if not customer_id:
            print("  ERROR: Failed to create customer")
            return

        # Create order + invoice
        resp = self._api(base_url, session_token, "POST", "/order", json_body={
            "customer": {"id": customer_id},
            "orderDate": today,
            "deliveryDate": today,
        })
        order_id = resp.get("value", {}).get("id")

        if not order_id:
            print("  ERROR: Failed to create order")
            return

        self._api(base_url, session_token, "POST", "/order/orderline", json_body={
            "order": {"id": order_id},
            "description": description,
            "count": 1,
            "unitPriceExcludingVatCurrency": amount,
            "vatType": {"id": 3},
        })

        resp = self._api(base_url, session_token, "PUT", f"/order/{order_id}/:invoice", params={
            "invoiceDate": today, "sendToCustomer": False,
        })
        invoice = resp.get("value", {})
        invoice_id = invoice.get("id")
        invoice_amount = invoice.get("amount", amount * 1.25)
        print(f"  Created invoice: id={invoice_id}, amount={invoice_amount}")

        if not invoice_id:
            print("  ERROR: Failed to create invoice")
            return

        expected["_invoice_id"] = invoice_id

        # Register full payment on the invoice
        # First get payment type
        resp = self._api(base_url, session_token, "GET", "/invoice/paymentType", {
            "fields": "id,description", "count": 3,
        })
        payment_types = resp.get("values", [])
        payment_type_id = payment_types[0]["id"] if payment_types else None

        if payment_type_id:
            resp = self._api(base_url, session_token, "PUT",
                f"/invoice/{invoice_id}/:payment",
                params={
                    "paymentDate": today,
                    "paymentTypeId": payment_type_id,
                    "paidAmount": invoice_amount,
                })
            if resp.get("value"):
                print(f"  Registered payment: {invoice_amount} NOK (invoice now fully paid)")
                # Verify it's paid
                resp2 = self._api(base_url, session_token, "GET", f"/invoice/{invoice_id}", {
                    "fields": "id,amountOutstanding",
                })
                outstanding = resp2.get("value", {}).get("amountOutstanding", "?")
                print(f"  Invoice outstanding after payment: {outstanding}")
            else:
                print("  WARNING: Payment registration may have failed")
        else:
            print("  ERROR: No payment types found")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        invoice_id = expected.get("_invoice_id")

        if not invoice_id:
            checks.append(Check(
                name="Invoice exists",
                passed=False,
                expected="invoice from setup",
                actual="setup failed — no invoice ID",
                points=2,
            ))
            return checks

        # Check the invoice — after reversal it should be outstanding again
        resp = verifier.get(f"/invoice/{invoice_id}", {
            "fields": "id,amount,amountOutstanding,invoiceNumber",
        })
        invoice = resp.get("value", {})
        amount = invoice.get("amount", 0)
        outstanding = invoice.get("amountOutstanding", 0)

        checks.append(Check(
            name="Invoice found",
            passed=amount > 0,
            expected="invoice with amount",
            actual=f"amount={amount}, outstanding={outstanding}",
            points=1,
        ))

        # The key check: after reversal, outstanding should equal the full amount
        checks.append(Check(
            name="Payment reversed (invoice outstanding again)",
            passed=abs(outstanding - amount) < 10,
            expected=f"outstanding ≈ {amount} (full amount)",
            actual=f"outstanding = {outstanding}",
            points=4,
        ))

        # Check that a reversal voucher was created
        voucher_resp = verifier.get("/ledger/voucher", {
            "dateFrom": "2026-01-01",
            "dateTo": "2099-12-31",
            "count": 20,
            "sorting": "-number",
        })
        vouchers = voucher_resp.get("values", [])
        # Look for a reversal voucher (has reverseVoucher set)
        has_reversal = False
        for v in vouchers[:10]:
            if v.get("reverseVoucher"):
                has_reversal = True
                break
            # Also check via detail
            v_detail = verifier.get(f"/ledger/voucher/{v['id']}", {"fields": "id,description,reverseVoucher"})
            if v_detail.get("value", {}).get("reverseVoucher"):
                has_reversal = True
                break

        checks.append(Check(
            name="Reversal voucher created",
            passed=has_reversal,
            expected="voucher with reverseVoucher set",
            actual="FOUND" if has_reversal else "NOT FOUND",
            points=2,
        ))

        return checks
