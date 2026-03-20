"""Task: Create departments."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class DepartmentsTask(BaseTask):
    name = "Create Departments"
    tier = 1
    optimal_calls = 3

    prompts = [
        'Créez trois départements dans Tripletex : "Utvikling", "Kundeservice" et "Innkjøp".',
        'Opprett tre avdelingar i Tripletex: "Økonomi", "Administrasjon" og "Innkjøp".',
        'Erstellen Sie drei Abteilungen in Tripletex: "Administrasjon", "Kundeservice" und "Markedsføring".',
        'Erstellen Sie drei Abteilungen in Tripletex: "Kvalitetskontroll", "Utvikling" und "Innkjøp".',
    ]

    def extract_expected(self, prompt: str) -> dict:
        # Extract quoted department names from the prompt
        names = re.findall(r'["\u201c\u201d]([^"\u201c\u201d]+)["\u201c\u201d]', prompt)
        return {"department_names": names}

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        names = expected.get("department_names", [])

        # Fetch all departments
        resp = verifier.get("/department", {"fields": "id,name,isInactive", "count": 100})
        departments = resp.get("values", [])
        dept_names = {d["name"].lower(): d for d in departments}

        for name in names:
            found = dept_names.get(name.lower())
            checks.append(Check(
                name=f"Department '{name}' found",
                passed=found is not None,
                expected=name,
                actual=found["name"] if found else "NOT FOUND",
            ))
            if found:
                checks.append(Check(
                    name=f"Department '{name}' is active",
                    passed=not found.get("isInactive", True),
                    expected="active",
                    actual="inactive" if found.get("isInactive") else "active",
                ))

        return checks
