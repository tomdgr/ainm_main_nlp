"""Task 19: Create employee from PDF employment contract."""

import base64
import io
import random

import pymupdf

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


# Contract templates with different employee data
_CONTRACTS = [
    {
        "firstName": "Kari",
        "lastName": "Nordmann",
        "dateOfBirth": "1988-11-22",
        "email": "kari.nordmann@example.org",
        "department": "Salg",
        "occupationCode": "3112",
        "employmentType": "Fast",
        "salaryType": "Fastlonn",
        "percentage": 80.0,
        "annualSalary": 540000,
        "startDate": "2026-05-01",
    },
    {
        "firstName": "Erik",
        "lastName": "Hansen",
        "dateOfBirth": "1992-06-15",
        "email": "erik.hansen@example.org",
        "department": "IT",
        "occupationCode": "2511",
        "employmentType": "Fast",
        "salaryType": "Fastlonn",
        "percentage": 100.0,
        "annualSalary": 620000,
        "startDate": "2026-04-01",
    },
    {
        "firstName": "Ingrid",
        "lastName": "Berg",
        "dateOfBirth": "1995-03-08",
        "email": "ingrid.berg@example.org",
        "department": "Okonomi",
        "occupationCode": "2411",
        "employmentType": "Fast",
        "salaryType": "Fastlonn",
        "percentage": 60.0,
        "annualSalary": 480000,
        "startDate": "2026-06-01",
    },
]


def _format_date_no(iso_date: str) -> str:
    """Convert ISO date to Norwegian format dd.mm.yyyy."""
    parts = iso_date.split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}"


def _generate_contract_pdf(contract: dict) -> bytes:
    """Generate a PDF employment contract using pymupdf."""
    doc = pymupdf.open()
    page = doc.new_page(width=450, height=700)

    y = 40
    # Title
    page.insert_text((130, y), "ARBEIDSKONTRAKT", fontsize=16, fontname="helv")
    y += 30

    # Separator
    page.draw_line((30, y), (420, y))
    y += 25

    # Company header
    page.insert_text((30, y), "Arbeidsgiver: Testselskap AS", fontsize=10, fontname="helv")
    y += 16
    page.insert_text((30, y), "Org.nr: 999 888 777", fontsize=9, fontname="helv")
    y += 25

    # Section: Personal details
    page.insert_text((30, y), "1. Arbeidstaker", fontsize=12, fontname="helv")
    y += 20

    full_name = f"{contract['firstName']} {contract['lastName']}"
    page.insert_text((40, y), f"Navn: {full_name}", fontsize=10, fontname="helv")
    y += 16

    dob_no = _format_date_no(contract["dateOfBirth"])
    page.insert_text((40, y), f"Fodselsdato: {dob_no}", fontsize=10, fontname="helv")
    y += 16

    page.insert_text((40, y), f"E-post: {contract['email']}", fontsize=10, fontname="helv")
    y += 25

    # Section: Position details
    page.insert_text((30, y), "2. Stilling og arbeidsforhold", fontsize=12, fontname="helv")
    y += 20

    page.insert_text((40, y), f"Avdeling: {contract['department']}", fontsize=10, fontname="helv")
    y += 16

    page.insert_text(
        (40, y),
        f"Stillingskode (STYRK): {contract['occupationCode']}",
        fontsize=10,
        fontname="helv",
    )
    y += 16

    page.insert_text(
        (40, y),
        f"Ansettelsesform: {contract['employmentType']}",
        fontsize=10,
        fontname="helv",
    )
    y += 16

    page.insert_text(
        (40, y),
        f"Stillingsandel: {contract['percentage']:.0f}%",
        fontsize=10,
        fontname="helv",
    )
    y += 25

    # Section: Salary
    page.insert_text((30, y), "3. Lonn", fontsize=12, fontname="helv")
    y += 20

    page.insert_text(
        (40, y),
        f"Lonnstype: {contract['salaryType']}",
        fontsize=10,
        fontname="helv",
    )
    y += 16

    page.insert_text(
        (40, y),
        f"Arslonn: {contract['annualSalary']:,} kr".replace(",", " "),
        fontsize=10,
        fontname="helv",
    )
    y += 25

    # Section: Start date
    page.insert_text((30, y), "4. Tiltredelse", fontsize=12, fontname="helv")
    y += 20

    start_no = _format_date_no(contract["startDate"])
    page.insert_text((40, y), f"Startdato: {start_no}", fontsize=10, fontname="helv")
    y += 30

    # Separator
    page.draw_line((30, y), (420, y))
    y += 20

    # Signature block
    page.insert_text((30, y), "Sted og dato: Oslo, " + start_no, fontsize=9, fontname="helv")
    y += 25
    page.insert_text((30, y), "____________________________", fontsize=9, fontname="helv")
    page.insert_text((250, y), "____________________________", fontsize=9, fontname="helv")
    y += 14
    page.insert_text((30, y), "Arbeidsgiver", fontsize=8, fontname="helv")
    page.insert_text((250, y), "Arbeidstaker", fontsize=8, fontname="helv")

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


class EmployeePDFTask(BaseTask):
    """Tier 3 task: Create employee from a PDF employment contract.

    Simulates task_19 from the competition. The agent receives a PDF containing
    an employment contract and must extract all details and create the employee
    in Tripletex with correct employment record.
    """

    name = "Employee from PDF Contract"
    tier = 3
    optimal_calls = 5  # dept + occupationCode + POST employee + POST employment + POST details

    def __init__(self, task_id: str):
        super().__init__(task_id)
        self._current_contract: dict | None = None

    @property
    def prompts(self) -> list[str]:
        return [
            "Registrer den ansatte basert pa informasjonen i den vedlagte arbeidskontrakten.",
            "Du har mottatt en arbeidskontrakt (se vedlagt PDF). Opprett den ansatte i Tripletex med alle detaljer fra kontrakten: fodselsdato, avdeling, stillingskode, lonn, stillingsprosent og startdato.",
            "Vennligst opprett den nye ansatte basert pa den vedlagte arbeidskontrakten. Bruk alle opplysninger fra PDF-en.",
        ]

    def extract_expected(self, prompt: str) -> dict:
        # Pick a random contract for each run
        contract = random.choice(_CONTRACTS)
        self._current_contract = contract
        return dict(contract)

    def get_files(self, expected: dict) -> list[dict]:
        """Generate and return a PDF employment contract."""
        contract = self._current_contract or _CONTRACTS[0]
        pdf_bytes = _generate_contract_pdf(contract)
        return [
            {
                "filename": "arbeidskontrakt.pdf",
                "content_base64": base64.b64encode(pdf_bytes).decode(),
                "mime_type": "application/pdf",
            }
        ]

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Ensure the department from the contract exists."""
        dept_name = expected.get("department", "")
        if not dept_name:
            return

        resp = self._api(base_url, session_token, "GET", "/department", {
            "name": dept_name, "fields": "id,name", "count": 1,
        })
        depts = resp.get("values", [])
        if depts:
            print(f"  Department '{dept_name}' exists: id={depts[0]['id']}")
        else:
            self._api(base_url, session_token, "POST", "/department", json_body={
                "name": dept_name,
            })
            print(f"  Created department '{dept_name}'")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        first_name = expected.get("firstName", "")
        last_name = expected.get("lastName", "")
        email = expected.get("email", "")
        dob = expected.get("dateOfBirth", "")
        start_date = expected.get("startDate", "")

        # --- Find the employee ---

        # Try by email first
        resp = verifier.get("/employee", {
            "email": email,
            "fields": "id,firstName,lastName,email,dateOfBirth,department(*),employments(*)",
            "count": 5,
        })
        employees = resp.get("values", [])

        # Fallback: search by name if email didn't match
        if not employees:
            resp = verifier.get("/employee", {
                "firstName": first_name,
                "fields": "id,firstName,lastName,email,dateOfBirth,department(*),employments(*)",
                "count": 10,
            })
            candidates = resp.get("values", [])
            employees = [
                e for e in candidates
                if e.get("lastName", "").lower() == last_name.lower()
            ]

        employee = employees[0] if employees else None

        # Check 1: Employee exists with correct name
        checks.append(Check(
            name="Employee found with correct name",
            passed=(
                employee is not None
                and employee.get("firstName", "").lower() == first_name.lower()
                and employee.get("lastName", "").lower() == last_name.lower()
            ),
            expected=f"{first_name} {last_name}",
            actual=(
                f"{employee.get('firstName', '')} {employee.get('lastName', '')}"
                if employee else "NOT FOUND"
            ),
            points=2,
        ))

        if not employee:
            return checks

        # Check 2: Correct email
        actual_email = employee.get("email", "") or ""
        checks.append(Check(
            name="Email correct",
            passed=actual_email.lower() == email.lower(),
            expected=email,
            actual=actual_email or "NONE",
            points=2,
        ))

        # Check 3: Correct date of birth
        actual_dob = employee.get("dateOfBirth", "") or ""
        checks.append(Check(
            name="Date of birth correct",
            passed=actual_dob == dob,
            expected=dob,
            actual=actual_dob or "NONE",
            points=2,
        ))

        # Check 4: Employment record exists with correct start date
        employments = employee.get("employments", [])
        employment_ok = False
        actual_start = "NO EMPLOYMENT"

        if employments:
            for emp in employments:
                emp_start = emp.get("startDate", "")
                if emp_start == start_date:
                    employment_ok = True
                    actual_start = emp_start
                    break
            if not employment_ok:
                # Accept any employment as partial credit indicator
                actual_start = employments[0].get("startDate", "UNKNOWN")

        checks.append(Check(
            name="Employment with correct start date",
            passed=employment_ok,
            expected=f"employment starting {start_date}",
            actual=actual_start,
            points=3,
        ))

        return checks
