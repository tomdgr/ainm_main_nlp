"""Task 13: Register travel expense with per diem and costs."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class TravelExpenseTask(BaseTask):
    """Tier 2 task: Register a travel expense with per diem compensation and costs.

    The agent must:
    1. Find the employee by email
    2. Look up rate categories, rate types, cost categories, payment types
    3. Create a travel expense with per diem compensations and costs
    4. Deliver the travel expense

    Setup pre-creates the employee so the agent can focus on the travel expense.
    """

    name = "Travel Expense (Per Diem + Costs)"
    tier = 2
    optimal_calls = 7  # employee + rateCategory + rate + costCategory + paymentType + POST travelExpense + PUT deliver

    prompts = [
        'Registrer en reiseregning for Kari Olsen (kari.olsen@example.org). Reise fra Oslo til Bergen, avreise 10.03.2026, retur 12.03.2026. Diettsats: 800 NOK/natt, 2 overnattinger. Flybillett: 3500 NOK. Taxi: 450 NOK.',
        'Register a travel expense for Erik Hansen (erik.hansen@example.org). Travel from Oslo to Trondheim, departure 15.03.2026, return 17.03.2026. Per diem rate: 900 NOK/night, 2 nights. Flight: 4200 NOK. Taxi: 350 NOK.',
        'Erstellen Sie eine Reisekostenabrechnung für Lars Berg (lars.berg@example.org). Reise von Oslo nach Stavanger, Abreise 20.03.2026, Rückkehr 22.03.2026. Tagessatz: 750 NOK/Nacht, 2 Übernachtungen. Flug: 2800 NOK. Taxi: 500 NOK.',
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        # Extract email
        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', prompt)
        if email_match:
            result["email"] = email_match.group(0)

        # Extract name — between "for" and the opening paren with email
        name_match = re.search(r'(?:für|for)\s+(.+?)\s*\(', prompt, re.IGNORECASE)
        if name_match:
            parts = name_match.group(1).strip().split()
            result["firstName"] = parts[0]
            result["lastName"] = parts[-1] if len(parts) > 1 else ""

        # Extract departure city (from X)
        from_match = re.search(r'(?:fra|from|von)\s+(\w+)', prompt, re.IGNORECASE)
        if from_match:
            result["departureFrom"] = from_match.group(1)

        # Extract destination city (to/til/nach X)
        to_match = re.search(r'(?:til|to|nach)\s+(\w+)', prompt, re.IGNORECASE)
        if to_match:
            result["destination"] = to_match.group(1)

        # Extract departure date
        dep_match = re.search(r'(?:avreise|departure|Abreise)\s+(\d{1,2})[./](\d{1,2})[./](\d{4})', prompt, re.IGNORECASE)
        if dep_match:
            result["departureDate"] = f"{dep_match.group(3)}-{dep_match.group(2).zfill(2)}-{dep_match.group(1).zfill(2)}"

        # Extract return date
        ret_match = re.search(r'(?:retur|return|Rückkehr)\s+(\d{1,2})[./](\d{1,2})[./](\d{4})', prompt, re.IGNORECASE)
        if ret_match:
            result["returnDate"] = f"{ret_match.group(3)}-{ret_match.group(2).zfill(2)}-{ret_match.group(1).zfill(2)}"

        # Extract per diem rate
        rate_match = re.search(r'(\d+)\s*NOK\s*/\s*(?:natt|night|Nacht)', prompt, re.IGNORECASE)
        if rate_match:
            result["perDiemRate"] = float(rate_match.group(1))

        # Extract number of nights
        nights_match = re.search(r'(\d+)\s*(?:overnattinger|nights|Übernachtungen)', prompt, re.IGNORECASE)
        if nights_match:
            result["nights"] = int(nights_match.group(1))

        # Extract flight amount
        flight_match = re.search(r'(?:Flybillett|Flight|Flug)[:\s]*(\d+)\s*NOK', prompt, re.IGNORECASE)
        if flight_match:
            result["flightAmount"] = float(flight_match.group(1))

        # Extract taxi amount
        taxi_match = re.search(r'(?:Taxi)[:\s]*(\d+)\s*NOK', prompt, re.IGNORECASE)
        if taxi_match:
            result["taxiAmount"] = float(taxi_match.group(1))

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Pre-create the employee and ensure a department exists."""
        email = expected.get("email", "")
        first_name = expected.get("firstName", "Test")
        last_name = expected.get("lastName", "User")

        # Ensure department exists
        dept_name = "Administrasjon"
        resp = self._api(base_url, session_token, "GET", "/department", {
            "name": dept_name, "fields": "id,name", "count": 1,
        })
        depts = resp.get("values", [])
        if depts:
            dept_id = depts[0]["id"]
            print(f"  Department '{dept_name}' exists: id={dept_id}")
        else:
            resp = self._api(base_url, session_token, "POST", "/department", json_body={
                "name": dept_name,
            })
            dept_id = resp.get("value", {}).get("id")
            print(f"  Created department '{dept_name}': id={dept_id}")

        # Check if employee already exists
        resp = self._api(base_url, session_token, "GET", "/employee", {
            "email": email, "fields": "id,firstName,lastName,email", "count": 1,
        })
        employees = resp.get("values", [])
        if employees:
            employee_id = employees[0]["id"]
            print(f"  Employee '{first_name} {last_name}' already exists: id={employee_id}")
            expected["_employee_id"] = employee_id
            return

        # Create employee
        resp = self._api(base_url, session_token, "POST", "/employee", json_body={
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "dateOfBirth": "1990-01-15",
            "department": {"id": dept_id} if dept_id else None,
        })
        employee_id = resp.get("value", {}).get("id")
        if employee_id:
            print(f"  Created employee '{first_name} {last_name}': id={employee_id}")
            expected["_employee_id"] = employee_id

            # Create employment record for the employee (required for travel expenses)
            start_date = "2025-01-01"
            resp = self._api(base_url, session_token, "POST", "/employee/employment", json_body={
                "employee": {"id": employee_id},
                "startDate": start_date,
                "employmentType": "ORDINARY",
            })
            employment_id = resp.get("value", {}).get("id")
            if employment_id:
                print(f"  Created employment: id={employment_id}")

                # Create employment details
                self._api(base_url, session_token, "POST", "/employee/employment/details", json_body={
                    "employment": {"id": employment_id},
                    "date": start_date,
                    "employmentType": "ORDINARY",
                    "remunerationType": "MONTHLY_WAGE",
                    "workingHoursScheme": "NOT_SHIFT",
                    "percentageOfFullTimeEquivalent": 100.0,
                    "annualSalary": 500000,
                    "monthlySalary": 41667,
                })
                print(f"  Created employment details")
        else:
            print(f"  ERROR: Failed to create employee")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        email = expected.get("email", "")
        first_name = expected.get("firstName", "")
        last_name = expected.get("lastName", "")

        # Step 1: Find the employee
        resp = verifier.get("/employee", {
            "email": email,
            "fields": "id,firstName,lastName,email",
            "count": 1,
        })
        employees = resp.get("values", [])
        employee = employees[0] if employees else None
        employee_id = employee.get("id") if employee else None

        if not employee_id:
            checks.append(Check(
                name="Employee found",
                passed=False,
                expected=f"{first_name} {last_name} ({email})",
                actual="NOT FOUND",
                points=1,
            ))
            return checks

        # Step 2: Look for travel expenses for this employee
        resp = verifier.get("/travelExpense", {
            "employeeId": str(employee_id),
            "count": 10,
            "sorting": "-id",
            "fields": "id,title,state,isCompleted,travelDetails(*),perDiemCompensations(*),costs(*)",
        })
        travel_expenses = resp.get("values", [])

        checks.append(Check(
            name="Travel expense exists",
            passed=len(travel_expenses) > 0,
            expected=f"at least 1 travel expense for employee {employee_id}",
            actual=f"{len(travel_expenses)} travel expenses found",
            points=3,
        ))

        if not travel_expenses:
            return checks

        te = travel_expenses[0]

        # Step 3: Check travel details exist
        travel_details = te.get("travelDetails") or {}
        has_destination = bool(travel_details.get("destination"))
        has_departure = bool(travel_details.get("departureDate"))

        checks.append(Check(
            name="Travel details present",
            passed=has_destination or has_departure,
            expected=f"destination={expected.get('destination', '?')}, departure={expected.get('departureDate', '?')}",
            actual=f"destination={travel_details.get('destination', 'NONE')}, departure={travel_details.get('departureDate', 'NONE')}",
            points=1,
        ))

        # Step 4: Check per diem compensations exist
        per_diems = te.get("perDiemCompensations") or []
        checks.append(Check(
            name="Per diem compensation exists",
            passed=len(per_diems) > 0,
            expected=f"per diem: {expected.get('nights', '?')} nights at {expected.get('perDiemRate', '?')} NOK",
            actual=f"{len(per_diems)} per diem entries" if per_diems else "NO per diem compensations",
            points=2,
        ))

        # Step 5: Check costs exist
        costs = te.get("costs") or []
        checks.append(Check(
            name="Costs exist",
            passed=len(costs) >= 2,
            expected=f"at least 2 costs (flight + taxi)",
            actual=f"{len(costs)} cost entries",
            points=2,
        ))

        # Step 6: Check if travel expense is delivered (not draft)
        state = te.get("state", "")
        is_completed = te.get("isCompleted", False)
        is_delivered = state == "DELIVERED" or is_completed is True
        checks.append(Check(
            name="Travel expense delivered",
            passed=is_delivered,
            expected="state=DELIVERED or isCompleted=True",
            actual=f"state={state}, isCompleted={is_completed}",
            points=2,
        ))

        # Step 7: Per diem count matches expected nights (1pt)
        expected_nights = expected.get("nights", 0)
        if expected_nights > 0:
            checks.append(Check(
                name="Per diem count matches",
                passed=len(per_diems) == expected_nights,
                expected=f"{expected_nights} per diem entries",
                actual=f"{len(per_diems)} per diem entries",
                points=1,
            ))

        # Step 8: Flight cost amount correct (1pt)
        expected_flight = expected.get("flightAmount", 0)
        if expected_flight > 0:
            flight_found = any(
                abs((c.get("amountCurrencyIncVat") or c.get("amount") or 0) - expected_flight) < 500
                for c in costs
            )
            checks.append(Check(
                name="Flight cost amount correct",
                passed=flight_found,
                expected=f"~{expected_flight} NOK",
                actual=f"costs: {[c.get('amountCurrencyIncVat') or c.get('amount') for c in costs]}",
                points=1,
            ))

        # Step 9: Taxi cost amount correct (1pt)
        expected_taxi = expected.get("taxiAmount", 0)
        if expected_taxi > 0:
            taxi_found = any(
                abs((c.get("amountCurrencyIncVat") or c.get("amount") or 0) - expected_taxi) < 200
                for c in costs
            )
            checks.append(Check(
                name="Taxi cost amount correct",
                passed=taxi_found,
                expected=f"~{expected_taxi} NOK",
                actual=f"costs: {[c.get('amountCurrencyIncVat') or c.get('amount') for c in costs]}",
                points=1,
            ))

        # Step 10: Destination correct (1pt)
        expected_dest = expected.get("destination", "")
        if expected_dest:
            actual_dest = (travel_details.get("destination") or "").lower()
            checks.append(Check(
                name="Destination correct",
                passed=expected_dest.lower() in actual_dest,
                expected=expected_dest,
                actual=travel_details.get("destination", "NONE"),
                points=1,
            ))

        return checks
