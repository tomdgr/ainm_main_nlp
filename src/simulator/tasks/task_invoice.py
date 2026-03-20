"""Task: Create and send invoice."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class InvoiceTask(BaseTask):
    name = "Create & Send Invoice"
    tier = 2
    optimal_calls = 5  # customer + product + order + invoice + send

    prompts = [
        "Crie e envie uma fatura ao cliente Rio Azul Lda (org. nº 959230277) por 27900 NOK sem IVA. A fatura refere-se a Relatório de análise.",
        'Der Kunde Sonnental GmbH (Org.-Nr. 958906471) soll eine Rechnung über 21750 NOK ohne MwSt. für "Webdesign" erhalten. Erstellen und senden Sie die Rechnung.',
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        # Extract org number
        org_match = re.search(r'\b(\d{9})\b', prompt)
        if org_match:
            result["organizationNumber"] = org_match.group(1)

        # Extract amount (number before NOK/kr)
        amount_match = re.search(r'(\d[\d\s]*\d)\s*(?:NOK|kr)', prompt)
        if amount_match:
            result["amount_excl_vat"] = float(amount_match.group(1).replace(" ", ""))

        # Extract customer name — between "cliente/Kunde/customer" and "(org"
        name_patterns = [
            r'(?:cliente|customer|Kunde|Kunden)\s+(.+?)(?:\s*\()',
        ]
        for pattern in name_patterns:
            m = re.search(pattern, prompt, re.IGNORECASE)
            if m:
                result["customer_name"] = m.group(1).strip().strip("'\"")
                break

        # Extract product/service description — quoted or after "for/für/para/refere-se a"
        desc_patterns = [
            r'["\u201c]([^"\u201d]+)["\u201d]',
            r'(?:refere-se a|refers to|für|para)\s+(.+?)(?:\.|$)',
        ]
        for pattern in desc_patterns:
            m = re.search(pattern, prompt, re.IGNORECASE)
            if m:
                result["description"] = m.group(1).strip()
                break

        return result

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        org_nr = expected.get("organizationNumber", "")
        amount_excl = expected.get("amount_excl_vat", 0)
        amount_incl = amount_excl * 1.25  # Standard 25% VAT

        # Find customer
        resp = verifier.get("/customer", {
            "organizationNumber": org_nr,
            "fields": "id,name,organizationNumber",
            "count": 1,
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

        # Find invoice
        resp = verifier.get("/invoice", {
            "customerId": customer_id,
            "invoiceDateFrom": "2020-01-01",
            "invoiceDateTo": "2099-12-31",
            "fields": "id,invoiceNumber,amount,amountExcludingVat,amountOutstanding,customer(name),isCharged,isCreditNote",
            "count": 10,
        })
        invoices = [inv for inv in resp.get("values", []) if not inv.get("isCreditNote")]
        invoice = invoices[0] if invoices else None

        checks.append(Check(
            name="Invoice found",
            passed=invoice is not None,
            expected="invoice for customer",
            actual="FOUND" if invoice else "NOT FOUND",
            points=2,
        ))

        if not invoice:
            return checks

        # Check amount excluding VAT
        actual_excl = invoice.get("amountExcludingVat", 0)
        checks.append(Check(
            name="Amount excl. VAT correct",
            passed=abs(actual_excl - amount_excl) < 1,
            expected=str(amount_excl),
            actual=str(actual_excl),
        ))

        # Check amount including VAT (25%)
        actual_incl = invoice.get("amount", 0)
        checks.append(Check(
            name="Amount incl. VAT correct (25%)",
            passed=abs(actual_incl - amount_incl) < 1,
            expected=str(amount_incl),
            actual=str(actual_incl),
        ))

        # Check invoice is charged/sent
        checks.append(Check(
            name="Invoice is charged/sent",
            passed=invoice.get("isCharged", False) is True,
            expected="true",
            actual=str(invoice.get("isCharged")),
        ))

        return checks
