"""Task: Create supplier."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class SupplierTask(BaseTask):
    name = "Create Supplier"
    tier = 1
    optimal_calls = 1

    prompts = [
        "Enregistrez le fournisseur Cascade SARL avec le numéro d'organisation 997712560. E-mail : faktura@cascadesarl.no.",
        "Register the supplier Silveroak Ltd with organization number 943413231. Email: faktura@silveroakltd.no.",
        "Registe o fornecedor Floresta Lda com número de organização 981154614. E-mail: faktura@florestalda.no.",
        "Registrieren Sie den Lieferanten Brückentor GmbH mit der Organisationsnummer 978195806. E-Mail: faktura@brckentorgmbh.no.",
        "Enregistrez le fournisseur Lumière SARL avec le numéro d'organisation 879852439. E-mail : faktura@lumiresarl.no.",
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        # Extract org number
        org_match = re.search(r'\b(\d{9})\b', prompt)
        if org_match:
            result["organizationNumber"] = org_match.group(1)

        # Extract email
        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', prompt)
        if email_match:
            result["email"] = email_match.group(0)

        # Extract name — between "supplier/fournisseur/fornecedor/Lieferant" and "with/avec/com/mit"
        name_patterns = [
            r'(?:supplier|fournisseur|fornecedor|Lieferanten?)\s+(.+?)(?:\s+(?:with|avec|com|mit)\b)',
            r'(?:supplier|fournisseur|fornecedor|Lieferanten?)\s+(.+?)(?:\s+\()',
        ]
        for pattern in name_patterns:
            m = re.search(pattern, prompt, re.IGNORECASE)
            if m:
                result["name"] = m.group(1).strip().strip("'\"")
                break

        return result

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        org_nr = expected.get("organizationNumber", "")

        # Search via /supplier endpoint
        resp = verifier.get("/supplier", {
            "organizationNumber": org_nr,
            "fields": "id,name,email,invoiceEmail,organizationNumber",
            "count": 1,
        })
        suppliers = resp.get("values", [])
        supplier = suppliers[0] if suppliers else None

        checks.append(Check(
            name="Supplier found",
            passed=supplier is not None,
            expected=f"org={org_nr}",
            actual="FOUND" if supplier else "NOT FOUND",
            points=2,
        ))

        if not supplier:
            return checks

        if "name" in expected:
            checks.append(Check(
                name="Name matches",
                passed=supplier.get("name", "").lower() == expected["name"].lower(),
                expected=expected["name"],
                actual=supplier.get("name", ""),
            ))

        checks.append(Check(
            name="Organization number matches",
            passed=supplier.get("organizationNumber") == org_nr,
            expected=org_nr,
            actual=supplier.get("organizationNumber", ""),
        ))

        if "email" in expected:
            checks.append(Check(
                name="Email matches",
                passed=supplier.get("email", "").lower() == expected["email"].lower(),
                expected=expected["email"],
                actual=supplier.get("email", ""),
            ))

            checks.append(Check(
                name="Invoice email matches",
                passed=supplier.get("invoiceEmail", "").lower() == expected["email"].lower(),
                expected=expected["email"],
                actual=supplier.get("invoiceEmail", ""),
            ))

        return checks
