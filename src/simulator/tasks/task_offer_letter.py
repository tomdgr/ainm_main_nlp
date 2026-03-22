"""Task 21: Employee onboarding from offer letter PDF (tilbudsbrev).

NOTE: These checks are APPROXIMATIONS. The real competition scorer likely validates
additional fields (percentage, salary amount, occupation code, standard time).
Local pass ≠ competition pass.

Similar to task_19 (employee from employment contract) but the PDF is an offer letter
rather than a formal contract. The fields are the same, minus nationalIdentityNumber.
"""

import base64
import io
import random

import pymupdf

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


_OFFERS = [
    {
        "firstName": "Lisa",
        "lastName": "Andersen",
        "dateOfBirth": "1991-07-14",
        "department": "HR",
        "position": "HR-rådgiver",
        "percentage": 100.0,
        "annualSalary": 580000,
        "startDate": "2026-08-01",
        "hoursPerDay": 7.5,
    },
    {
        "firstName": "Jonas",
        "lastName": "Nilsen",
        "dateOfBirth": "1994-02-28",
        "department": "Økonomi",
        "position": "Regnskapskonsulent",
        "percentage": 80.0,
        "annualSalary": 510000,
        "startDate": "2026-09-15",
        "hoursPerDay": 6.0,
    },
    {
        "firstName": "Maria",
        "lastName": "Olsen",
        "dateOfBirth": "1989-12-03",
        "department": "Salg",
        "position": "Salgsleder",
        "percentage": 100.0,
        "annualSalary": 720000,
        "startDate": "2026-07-01",
        "hoursPerDay": 7.5,
    },
]


def _format_date_no(iso_date: str) -> str:
    parts = iso_date.split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}"


def _generate_offer_pdf(offer: dict) -> bytes:
    """Generate a tilbudsbrev (offer letter) PDF."""
    doc = pymupdf.open()
    page = doc.new_page(width=450, height=700)

    y = 40
    page.insert_text((150, y), "TILBUDSBREV", fontsize=16, fontname="helv")
    y += 30
    page.draw_line((30, y), (420, y))
    y += 25

    page.insert_text((30, y), "Arbeidsgiver: Testselskap AS", fontsize=10, fontname="helv")
    y += 16
    page.insert_text((30, y), "Org.nr: 999 888 777", fontsize=9, fontname="helv")
    y += 25

    page.insert_text((30, y), "1. Kandidat", fontsize=12, fontname="helv")
    y += 20
    full_name = f"{offer['firstName']} {offer['lastName']}"
    page.insert_text((40, y), f"Navn: {full_name}", fontsize=10, fontname="helv")
    y += 16
    dob_no = _format_date_no(offer["dateOfBirth"])
    page.insert_text((40, y), f"Fødselsdato: {dob_no}", fontsize=10, fontname="helv")
    y += 25

    page.insert_text((30, y), "2. Stilling", fontsize=12, fontname="helv")
    y += 20
    page.insert_text((40, y), f"Stilling: {offer['position']}", fontsize=10, fontname="helv")
    y += 16
    page.insert_text((40, y), f"Avdeling: {offer['department']}", fontsize=10, fontname="helv")
    y += 16
    page.insert_text((40, y), f"Stillingsprosent: {offer['percentage']:.0f}%", fontsize=10, fontname="helv")
    y += 16
    page.insert_text((40, y), "Ansettelsesform: Fast stilling", fontsize=10, fontname="helv")
    y += 25

    page.insert_text((30, y), "3. Lønn", fontsize=12, fontname="helv")
    y += 20
    page.insert_text(
        (40, y),
        f"Årslønn: {offer['annualSalary']:,} kr".replace(",", " "),
        fontsize=10, fontname="helv",
    )
    y += 16
    page.insert_text(
        (40, y),
        f"Arbeidstid: {offer['hoursPerDay']:.1f} timer per dag",
        fontsize=10, fontname="helv",
    )
    y += 25

    page.insert_text((30, y), "4. Tiltredelse", fontsize=12, fontname="helv")
    y += 20
    start_no = _format_date_no(offer["startDate"])
    page.insert_text((40, y), f"Startdato: {start_no}", fontsize=10, fontname="helv")
    y += 30

    page.draw_line((30, y), (420, y))
    y += 20
    page.insert_text((30, y), f"Sted og dato: Oslo, {start_no}", fontsize=9, fontname="helv")

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


class OfferLetterTask(BaseTask):
    """Tier 3 task: Employee onboarding from offer letter PDF.

    The agent receives a tilbudsbrev (offer letter) and must create the employee
    with department, employment details, salary, percentage, and standard time.
    """

    name = "Employee from Offer Letter"
    tier = 3
    optimal_calls = 5

    def __init__(self, task_id: str):
        super().__init__(task_id)
        self._current_offer: dict | None = None

    @property
    def prompts(self) -> list[str]:
        return [
            "Du har mottatt et tilbudsbrev (se vedlagt PDF) for en ny ansatt. Gjennomfør komplett onboarding: opprett den ansatte, tildel riktig avdeling, sett opp ansettelsesdetaljer med prosent og årslønn, og konfigurer standard arbeidstid.",
            "You received an offer letter (see attached PDF) for a new employee. Complete the onboarding: create the employee, assign the correct department, set up employment details with percentage and annual salary, and configure standard working hours.",
        ]

    def extract_expected(self, prompt: str) -> dict:
        offer = random.choice(_OFFERS)
        self._current_offer = offer
        return dict(offer)

    def get_files(self, expected: dict) -> list[dict]:
        offer = self._current_offer or _OFFERS[0]
        pdf_bytes = _generate_offer_pdf(offer)
        return [{
            "filename": "tilbudsbrev.pdf",
            "content_base64": base64.b64encode(pdf_bytes).decode(),
            "mime_type": "application/pdf",
        }]

    def setup(self, base_url: str, session_token: str, expected: dict):
        dept_name = expected.get("department", "")
        if not dept_name:
            return
        resp = self._api(base_url, session_token, "GET", "/department", {
            "name": dept_name, "fields": "id,name", "count": 1,
        })
        if resp.get("values"):
            print(f"  Department '{dept_name}' exists")
        else:
            self._api(base_url, session_token, "POST", "/department", json_body={"name": dept_name})
            print(f"  Created department '{dept_name}'")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        first_name = expected.get("firstName", "")
        last_name = expected.get("lastName", "")
        dob = expected.get("dateOfBirth", "")
        start_date = expected.get("startDate", "")
        department = expected.get("department", "")
        percentage = expected.get("percentage", 100)
        salary = expected.get("annualSalary", 0)

        # Find employee by name
        resp = verifier.get("/employee", {
            "firstName": first_name,
            "fields": "id,firstName,lastName,dateOfBirth,department(*),employments(*)",
            "count": 10,
        })
        candidates = resp.get("values", [])
        employee = next(
            (e for e in candidates if e.get("lastName", "").lower() == last_name.lower()),
            None,
        )

        checks.append(Check(
            name="Employee found with correct name",
            passed=employee is not None,
            expected=f"{first_name} {last_name}",
            actual=f"{employee['firstName']} {employee['lastName']}" if employee else "NOT FOUND",
            points=2,
        ))

        if not employee:
            return checks

        # Check DOB
        checks.append(Check(
            name="Date of birth correct",
            passed=employee.get("dateOfBirth") == dob,
            expected=dob,
            actual=employee.get("dateOfBirth") or "NONE",
            points=1,
        ))

        # Check department
        dept = employee.get("department", {})
        dept_name_actual = dept.get("name", "") if dept else ""
        checks.append(Check(
            name="Department correct",
            passed=dept_name_actual.lower() == department.lower(),
            expected=department,
            actual=dept_name_actual or "NONE",
            points=1,
        ))

        # Check employment exists with start date
        employments = employee.get("employments", [])
        emp_ok = any(e.get("startDate") == start_date for e in employments)
        checks.append(Check(
            name="Employment with correct start date",
            passed=emp_ok,
            expected=start_date,
            actual=employments[0].get("startDate", "NONE") if employments else "NO EMPLOYMENT",
            points=2,
        ))

        # Check employment details (salary + percentage) via employment details endpoint
        if employments:
            emp_id = employments[0].get("id")
            if emp_id:
                det_resp = verifier.get("/employee/employment/details", {
                    "employmentId": emp_id,
                    "fields": "id,annualSalary,percentageOfFullTimeEquivalent",
                    "count": 1,
                })
                details = det_resp.get("values", [])
                if details:
                    actual_salary = details[0].get("annualSalary", 0)
                    actual_pct = details[0].get("percentageOfFullTimeEquivalent", 0)
                    checks.append(Check(
                        name="Annual salary correct",
                        passed=abs(actual_salary - salary) < 1000,
                        expected=str(salary),
                        actual=str(actual_salary),
                        points=2,
                    ))
                    checks.append(Check(
                        name="Percentage correct",
                        passed=abs(actual_pct - percentage) < 1,
                        expected=f"{percentage}%",
                        actual=f"{actual_pct}%",
                        points=1,
                    ))
                else:
                    checks.append(Check(name="Annual salary correct", passed=False,
                                       expected=str(salary), actual="NO DETAILS", points=2))
                    checks.append(Check(name="Percentage correct", passed=False,
                                       expected=f"{percentage}%", actual="NO DETAILS", points=1))
        else:
            checks.append(Check(name="Annual salary correct", passed=False,
                               expected=str(salary), actual="NO EMPLOYMENT", points=2))
            checks.append(Check(name="Percentage correct", passed=False,
                               expected=f"{percentage}%", actual="NO EMPLOYMENT", points=1))

        return checks
