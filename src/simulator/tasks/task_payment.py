"""Task: Register payment on existing invoice."""

import datetime
import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class PaymentTask(BaseTask):
    name = "Register Payment on Invoice"
    tier = 2
    optimal_calls = 4  # find customer + find invoice + get payment type + register payment

    prompts = [
        'The customer Ironbridge Ltd (org no. 948020890) has an outstanding invoice for 6300 NOK excluding VAT for "Consulting Hours". Register full payment on this invoice.',
        'Kunden Strandvik AS (org.nr 840390055) har ein uteståande faktura på 27050 kr eksklusiv MVA for "Datarådgjeving". Registrer full betaling på denne fakturaen.',
        'Der Kunde Sonnental GmbH (Org.-Nr. 958906471) hat eine offene Rechnung über 21750 NOK ohne MwSt. für "Webdesign". Registrieren Sie die vollständige Zahlung dieser Rechnung.',
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
            r'(?:customer|Kunde|Kunden|client|cliente)\s+(.+?)(?:\s*\()',
        ]
        for pattern in name_patterns:
            m = re.search(pattern, prompt, re.IGNORECASE)
            if m:
                result["customer_name"] = m.group(1).strip().strip("'\"")
                break

        # Extract product description (quoted)
        desc_match = re.search(r'"([^"]+)"', prompt)
        if desc_match:
            result["description"] = desc_match.group(1)

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Pre-create customer, product, order, and invoice so the agent only needs to register payment."""
        today = datetime.date.today().isoformat()
        org_nr = expected.get("organizationNumber", "")
        name = expected.get("customer_name", "Test Customer")
        amount = expected.get("amount_excl_vat", 1000)
        description = expected.get("description", "Service")

        print(f"  Setting up payment task: customer={name}, amount={amount}")

        # Check if customer already exists
        resp = self._api(base_url, session_token, "GET", "/customer", {
            "organizationNumber": org_nr, "fields": "id,name", "count": 1,
        })
        customers = resp.get("values", [])

        if customers:
            customer_id = customers[0]["id"]
            print(f"  Customer already exists: id={customer_id}")
        else:
            resp = self._api(base_url, session_token, "POST", "/customer", json_body={
                "name": name, "organizationNumber": org_nr, "isCustomer": True,
            })
            customer_id = resp.get("value", {}).get("id")
            print(f"  Created customer: id={customer_id}")

        if not customer_id:
            print("  ERROR: Failed to create customer")
            return

        # Ensure bank account is set (needed for invoicing)
        resp = self._api(base_url, session_token, "GET", "/ledger/account", {
            "number": "1920", "fields": "id,version,bankAccountNumber",
        })
        accounts = resp.get("values", [])
        if accounts and not accounts[0].get("bankAccountNumber"):
            self._api(base_url, session_token, "PUT", f"/ledger/account/{accounts[0]['id']}", json_body={
                "id": accounts[0]["id"], "version": accounts[0]["version"],
                "bankAccountNumber": "12345678903",
            })
            print("  Set bank account number on ledger account 1920")

        # Create order with order line (use vatType 0 for sandbox compatibility)
        resp = self._api(base_url, session_token, "POST", "/order", json_body={
            "customer": {"id": customer_id},
            "orderDate": today,
            "deliveryDate": today,
        })
        order_id = resp.get("value", {}).get("id")
        print(f"  Created order: id={order_id}")

        if not order_id:
            print("  ERROR: Failed to create order")
            return

        # Add order line (try vatType 3, fall back to 0)
        resp = self._api(base_url, session_token, "POST", "/order/orderline", json_body={
            "order": {"id": order_id},
            "description": description,
            "count": 1,
            "unitPriceExcludingVatCurrency": amount,
            "vatType": {"id": 3},
        })
        if not resp.get("value"):
            # Fallback: vatType 3 not available on this sandbox
            resp = self._api(base_url, session_token, "POST", "/order/orderline", json_body={
                "order": {"id": order_id},
                "description": description,
                "count": 1,
                "unitPriceExcludingVatCurrency": amount,
                "vatType": {"id": 0},
            })
        print(f"  Created order line: {description}")

        # Create invoice from order
        resp = self._api(base_url, session_token, "PUT", f"/order/{order_id}/:invoice", params={
            "invoiceDate": today, "sendToCustomer": False,
        })
        invoice_id = resp.get("value", {}).get("id")
        print(f"  Created invoice: id={invoice_id}")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        org_nr = expected.get("organizationNumber", "")

        resp = verifier.get("/customer", {
            "organizationNumber": org_nr, "fields": "id,name", "count": 1,
        })
        customers = resp.get("values", [])
        customer = customers[0] if customers else None

        checks.append(Check(
            name="Customer found",
            passed=customer is not None,
            expected=expected.get("customer_name", org_nr),
            actual=customer.get("name", "NOT FOUND") if customer else "NOT FOUND",
        ))

        if not customer:
            return checks

        customer_id = customer["id"]

        resp = verifier.get("/invoice", {
            "customerId": customer_id,
            "invoiceDateFrom": "2020-01-01",
            "invoiceDateTo": "2099-12-31",
            "fields": "id,invoiceNumber,amount,amountExcludingVat,amountOutstanding,isCreditNote",
            "count": 10,
        })
        invoices = [inv for inv in resp.get("values", []) if not inv.get("isCreditNote")]
        invoice = invoices[0] if invoices else None

        checks.append(Check(
            name="Invoice found",
            passed=invoice is not None,
            expected="existing invoice",
            actual="FOUND" if invoice else "NOT FOUND",
            points=2,
        ))

        if not invoice:
            return checks

        outstanding = invoice.get("amountOutstanding", -1)
        checks.append(Check(
            name="Payment registered (outstanding = 0)",
            passed=abs(outstanding) < 1,
            expected="0",
            actual=str(outstanding),
            points=3,
        ))

        amount_excl = expected.get("amount_excl_vat", 0)
        actual_excl = invoice.get("amountExcludingVat", 0)
        checks.append(Check(
            name="Invoice amount excl. VAT correct",
            passed=abs(actual_excl - amount_excl) < 1,
            expected=str(amount_excl),
            actual=str(actual_excl),
        ))

        return checks
