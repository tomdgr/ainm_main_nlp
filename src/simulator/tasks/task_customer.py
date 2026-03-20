"""Task: Create customer."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class CustomerTask(BaseTask):
    name = "Create Customer"
    tier = 1
    optimal_calls = 1

    prompts = [
        "Crie o cliente Floresta Lda com número de organização 893475656. O endereço é Kirkegata 132, 7010 Trondheim. E-mail: post@floresta.no.",
        "Create the customer Nordlys AS with organization number 872778330. Address: Storgata 45, 0182 Oslo. Email: contact@nordlys.no.",
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        # Extract org number (9 digits)
        org_match = re.search(r'\b(\d{9})\b', prompt)
        if org_match:
            result["organizationNumber"] = org_match.group(1)

        # Extract email
        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', prompt)
        if email_match:
            result["email"] = email_match.group(0)

        # Extract name — text between "cliente/customer" and "com/with"
        # Try multiple language patterns
        name_patterns = [
            r'(?:cliente|customer|Kunde|Kunden)\s+(.+?)(?:\s+(?:com|with|mit|avec|con)\b)',
            r'(?:cliente|customer|Kunde)\s+(.+?)(?:\s+\()',
        ]
        for pattern in name_patterns:
            m = re.search(pattern, prompt, re.IGNORECASE)
            if m:
                result["name"] = m.group(1).strip().strip("'\"")
                break

        # Extract address parts (street, postal code, city)
        addr_match = re.search(r'(?:endereço|address|Adresse|adresse)\s*(?:é|is|:)?\s*(.+?)(?:\.|$)', prompt, re.IGNORECASE)
        if addr_match:
            addr_str = addr_match.group(1).strip()
            # Try to parse "Street 123, 1234 City"
            parts = re.match(r'(.+?),\s*(\d{4})\s+(.+)', addr_str)
            if parts:
                result["addressLine1"] = parts.group(1).strip()
                result["postalCode"] = parts.group(2)
                result["city"] = parts.group(3).strip()

        return result

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        org_nr = expected.get("organizationNumber", "")

        # Find customer by org number
        resp = verifier.get("/customer", {
            "organizationNumber": org_nr,
            "fields": "id,name,email,invoiceEmail,organizationNumber,isCustomer,postalAddress(*)",
            "count": 1,
        })
        customers = resp.get("values", [])
        customer = customers[0] if customers else None

        checks.append(Check(
            name="Customer found",
            passed=customer is not None,
            expected=f"org={org_nr}",
            actual="FOUND" if customer else "NOT FOUND",
            points=2,
        ))

        if not customer:
            return checks

        if "name" in expected:
            checks.append(Check(
                name="Name matches",
                passed=customer.get("name", "").lower() == expected["name"].lower(),
                expected=expected["name"],
                actual=customer.get("name", ""),
            ))

        checks.append(Check(
            name="Organization number matches",
            passed=customer.get("organizationNumber") == org_nr,
            expected=org_nr,
            actual=customer.get("organizationNumber", ""),
        ))

        if "email" in expected:
            checks.append(Check(
                name="Email matches",
                passed=customer.get("email", "").lower() == expected["email"].lower(),
                expected=expected["email"],
                actual=customer.get("email", ""),
            ))

        checks.append(Check(
            name="isCustomer is true",
            passed=customer.get("isCustomer") is True,
            expected="true",
            actual=str(customer.get("isCustomer")),
        ))

        # Address checks
        address = customer.get("postalAddress", {}) or {}
        if "addressLine1" in expected:
            checks.append(Check(
                name="Address line matches",
                passed=expected["addressLine1"].lower() in (address.get("addressLine1", "") or "").lower(),
                expected=expected["addressLine1"],
                actual=address.get("addressLine1", ""),
            ))
        if "postalCode" in expected:
            checks.append(Check(
                name="Postal code matches",
                passed=address.get("postalCode", "") == expected["postalCode"],
                expected=expected["postalCode"],
                actual=address.get("postalCode", ""),
            ))
        if "city" in expected:
            checks.append(Check(
                name="City matches",
                passed=expected["city"].lower() in (address.get("city", "") or "").lower(),
                expected=expected["city"],
                actual=address.get("city", ""),
            ))

        return checks
