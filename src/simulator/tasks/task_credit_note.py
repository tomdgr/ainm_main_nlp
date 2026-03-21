"""Task 14: Create a credit note to cancel an existing invoice."""

import datetime
import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class CreditNoteTask(BaseTask):
    name = "Create Credit Note"
    tier = 2
    optimal_calls = 4  # find customer + find invoice + create credit note

    prompts = [
        'Kunden Nordlys AS (org.nr 934567890) har reklamert på fakturaen for "Konsulenttimer" (18500 NOK eksklusiv MVA). Opprett en fullstendig kreditnota som kansellerer hele fakturaen.',
        'The customer Clearwater Ltd (org no. 891234567) has complained about the invoice for "IT Support" (9200 NOK excluding VAT). Create a full credit note that cancels the entire invoice.',
        'Der Kunde Bergwerk GmbH (Org.-Nr. 920065007) hat die Rechnung für "Beratung" (22000 NOK ohne MwSt.) reklamiert. Erstellen Sie eine vollständige Gutschrift.',
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}
        org_match = re.search(r'\b(\d{9})\b', prompt)
        if org_match:
            result["organizationNumber"] = org_match.group(1)

        amount_match = re.search(r'(\d[\d\s]*\d)\s*(?:NOK|kr)', prompt)
        if amount_match:
            result["amount_excl_vat"] = float(amount_match.group(1).replace(" ", ""))

        name_patterns = [r'(?:Kunden|customer|Kunde|client)\s+(.+?)(?:\s*\()']
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
        """Create customer + invoice that the agent will credit."""
        today = datetime.date.today().isoformat()
        org_nr = expected.get("organizationNumber", "")
        name = expected.get("customer_name", "Test Customer")
        amount = expected.get("amount_excl_vat", 10000)
        description = expected.get("description", "Service")

        print(f"  Setting up credit note task: customer={name}, amount={amount}")

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
            return

        # Create order + invoice
        resp = self._api(base_url, session_token, "POST", "/order", json_body={
            "customer": {"id": customer_id},
            "orderDate": today, "deliveryDate": today,
        })
        order_id = resp.get("value", {}).get("id")
        if order_id:
            self._api(base_url, session_token, "POST", "/order/orderline", json_body={
                "order": {"id": order_id},
                "description": description, "count": 1,
                "unitPriceExcludingVatCurrency": amount, "vatType": {"id": 3},
            })
            resp = self._api(base_url, session_token, "PUT", f"/order/{order_id}/:invoice", params={
                "invoiceDate": today, "sendToCustomer": False,
            })
            invoice_id = resp.get("value", {}).get("id")
            expected["_invoice_id"] = invoice_id
            print(f"  Created invoice: id={invoice_id}")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        org_nr = expected.get("organizationNumber", "")

        # Find customer
        resp = verifier.get("/customer", {"organizationNumber": org_nr, "fields": "id", "count": 1})
        customer_id = resp.get("values", [{}])[0].get("id") if resp.get("values") else None
        if not customer_id:
            checks.append(Check(name="Customer found", passed=False, points=1))
            return checks

        # Find credit notes
        resp = verifier.get("/invoice", {
            "customerId": customer_id,
            "invoiceDateFrom": "2020-01-01", "invoiceDateTo": "2099-12-31",
            "fields": "id,isCreditNote,amount,amountExcludingVat",
            "count": 20,
        })
        invoices = resp.get("values", [])
        credit_notes = [inv for inv in invoices if inv.get("isCreditNote")]

        checks.append(Check(
            name="Credit note created",
            passed=len(credit_notes) > 0,
            expected="at least 1 credit note",
            actual=f"{len(credit_notes)} credit notes",
            points=3,
        ))

        if credit_notes:
            cn = credit_notes[0]
            amount_excl = expected.get("amount_excl_vat", 0)
            actual_excl = abs(cn.get("amountExcludingVat", 0))
            checks.append(Check(
                name="Credit note amount correct",
                passed=abs(actual_excl - amount_excl) < 10,
                expected=str(amount_excl),
                actual=str(actual_excl),
                points=2,
            ))

        # Original invoice should now be balanced
        invoice_id = expected.get("_invoice_id")
        if invoice_id:
            resp = verifier.get(f"/invoice/{invoice_id}", {"fields": "id,amountOutstanding"})
            outstanding = resp.get("value", {}).get("amountOutstanding", -1)
            checks.append(Check(
                name="Original invoice balanced (outstanding ≈ 0)",
                passed=abs(outstanding) < 10,
                expected="~0",
                actual=str(outstanding),
                points=2,
            ))

        return checks
