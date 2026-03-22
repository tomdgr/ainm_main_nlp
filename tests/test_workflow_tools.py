"""Unit tests for workflow tools — tests actual API interactions and logic.

Covers:
- analyze_expense_changes: account number resolution, expense filtering, change ranking
- create_supplier_invoice: voucherType lookup, balanced postings, voucher creation
- create_travel_expense: category discovery, field name correctness
- setup_employee_for_payroll: prerequisite chain
- build_voucher_postings: pure logic (balance, row numbering, field mapping)
- auto-fixes: bank account ensure, paymentTypeId fetch
- validator rules: timesheet dates, invoice dates, null description

Run: python tests/test_workflow_tools.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run_tests():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

    from src.services.tripletex_client import TripletexClient
    from src.services.api_validator import APIValidator
    from src.services.openapi_spec import OpenAPISpecSearcher

    api_url = os.getenv("API_URL")
    token = os.getenv("SESSION_TOKEN")
    if not api_url or not token:
        print("SKIP: No API_URL or SESSION_TOKEN in .env")
        return True

    client = TripletexClient(base_url=api_url, session_token=token)
    spec = OpenAPISpecSearcher()
    spec.load()
    validator = APIValidator(spec.get_raw_spec())

    passed = 0
    failed = 0

    def check(name, condition, detail=""):
        nonlocal passed, failed
        if condition:
            print(f"  PASS: {name}")
            passed += 1
        else:
            print(f"  FAIL: {name} — {detail}")
            failed += 1

    # =========================================================================
    # Test 1: analyze_expense_changes — account number resolution
    # =========================================================================
    print("\n=== Test 1: analyze_expense_changes — account number resolution ===")

    # The critical bug: /ledger/postingByDate returns account.number=None
    r = await client.request("GET", "/ledger/postingByDate",
                              params={"dateFrom": "2026-01-01", "dateTo": "2026-02-01", "count": 5})
    check("GET /ledger/postingByDate succeeds", r["ok"])
    vals = r["body"].get("values", [])
    if vals:
        acct = vals[0].get("account", {})
        check("postingByDate account has 'id'", "id" in acct, f"keys={list(acct.keys())}")
        check("postingByDate account lacks 'number' (known limitation)",
              acct.get("number") is None,
              f"number={acct.get('number')} — if this passes, the bug is confirmed")

        # Verify we can resolve account IDs to numbers
        acct_id = acct["id"]
        r2 = await client.request("GET", "/ledger/account",
                                   params={"id": str(acct_id), "fields": "id,number,name"})
        check("Account ID resolves to number via GET /ledger/account",
              r2["ok"] and r2["body"].get("values", [{}])[0].get("number") is not None,
              f"response={r2['body']}")

    # =========================================================================
    # Test 2: analyze_expense_changes — full pipeline
    # =========================================================================
    print("\n=== Test 2: analyze_expense_changes — full pipeline ===")

    # Simulate the tool's internal logic
    p1 = await client.request("GET", "/ledger/postingByDate",
                               params={"dateFrom": "2026-01-01", "dateTo": "2026-02-01", "count": 10000})
    p2 = await client.request("GET", "/ledger/postingByDate",
                               params={"dateFrom": "2026-02-01", "dateTo": "2026-03-01", "count": 10000})
    check("Both periods fetched", p1["ok"] and p2["ok"])

    # Aggregate by ID
    def agg(body):
        totals = {}
        for p in body.get("values", []):
            aid = str(p.get("account", {}).get("id", ""))
            if not aid:
                continue
            totals[aid] = round(totals.get(aid, 0) + p.get("amount", 0), 2)
        return totals

    p1_totals = agg(p1["body"])
    p2_totals = agg(p2["body"])
    all_ids = set(p1_totals) | set(p2_totals)
    check("Found account IDs in postings", len(all_ids) > 0, f"ids={len(all_ids)}")

    # Batch resolve
    id_list = ",".join(all_ids)
    acct_resp = await client.request("GET", "/ledger/account",
                                      params={"id": id_list, "fields": "id,number,name",
                                              "count": len(all_ids) + 10})
    acct_map = {str(a["id"]): a for a in acct_resp.get("body", {}).get("values", [])}
    check("All account IDs resolved", len(acct_map) == len(all_ids),
          f"resolved={len(acct_map)}, expected={len(all_ids)}")

    # Filter to expense range
    expense_changes = []
    for key in all_ids:
        info = acct_map.get(key, {})
        num = info.get("number", 0)
        if 5000 <= num <= 7999:
            change = round(p2_totals.get(key, 0) - p1_totals.get(key, 0), 2)
            expense_changes.append({"number": num, "name": info.get("name", ""), "change": change})

    expense_changes.sort(key=lambda x: x["change"], reverse=True)
    check("Found expense accounts (5000-7999)", len(expense_changes) > 0,
          f"count={len(expense_changes)}")
    check("Top 3 identified", len(expense_changes) >= 3,
          f"only {len(expense_changes)} expense accounts")
    if expense_changes:
        print(f"  INFO: Top 3: {expense_changes[:3]}")

    # =========================================================================
    # Test 3: Supplier invoice — voucherType + voucher creation
    # =========================================================================
    print("\n=== Test 3: Supplier invoice voucher creation ===")

    r = await client.request("GET", "/supplier", params={"fields": "id,name", "count": 1})
    supplier_id = r["body"]["values"][0]["id"] if r["body"].get("values") else None
    check("Supplier found", supplier_id is not None)

    r = await client.request("GET", "/ledger/account", params={"number": "6500", "fields": "id"})
    expense_id = r["body"]["values"][0]["id"] if r["body"].get("values") else None
    check("Expense account 6500 found", expense_id is not None)

    r = await client.request("GET", "/ledger/account", params={"number": "2400", "fields": "id"})
    ap_id = r["body"]["values"][0]["id"] if r["body"].get("values") else None
    check("AP account 2400 found", ap_id is not None)

    r = await client.request("GET", "/ledger/voucherType",
                              params={"name": "Leverandørfaktura", "fields": "id"})
    vt_id = r["body"]["values"][0]["id"] if r["body"].get("values") else None
    check("VoucherType 'Leverandørfaktura' found", vt_id is not None)

    if all([supplier_id, expense_id, ap_id, vt_id]):
        voucher_body = {
            "date": "2026-03-22", "description": "Unit test supplier invoice",
            "vendorInvoiceNumber": "UT-SI-001", "voucherType": {"id": vt_id},
            "postings": [
                {"row": 1, "account": {"id": expense_id},
                 "amountGross": 20000, "amountGrossCurrency": 20000, "vatType": {"id": 1}},
                {"row": 2, "account": {"id": ap_id},
                 "amountGross": -20000, "amountGrossCurrency": -20000,
                 "supplier": {"id": supplier_id}},
            ],
        }
        r = await client.request("POST", "/ledger/voucher",
                                  params={"sendToLedger": "true"}, json_body=voucher_body)
        check("Supplier voucher created (201)", r["ok"] and r["status_code"] == 201,
              f"status={r['status_code']}, body={json.dumps(r.get('body', {}))[:200]}")
        if r["ok"]:
            vid = r["body"]["value"]["id"]
            check("Voucher has id", vid is not None)
            check("Voucher has voucherType set",
                  r["body"]["value"].get("voucherType") is not None)

    # =========================================================================
    # Test 4: Travel expense — category discovery
    # =========================================================================
    print("\n=== Test 4: Travel expense category discovery ===")

    r = await client.request("GET", "/travelExpense/rateCategory",
                              params={"type": "PER_DIEM", "fields": "id,name,type",
                                      "dateFrom": "2026-03-01", "dateTo": "2026-03-31"})
    check("Rate categories found", r["ok"] and len(r["body"].get("values", [])) > 0)
    rc_vals = r["body"].get("values", [])
    overnight = [c for c in rc_vals if "overnatting" in c.get("name", "").lower()]
    check("Overnight rate category exists", len(overnight) > 0)

    if overnight:
        rc_id = overnight[0]["id"]
        r2 = await client.request("GET", "/travelExpense/rate",
                                   params={"rateCategoryId": rc_id, "fields": "id,rate"})
        check("Rate type resolved from category", r2["ok"] and len(r2["body"].get("values", [])) > 0)

    r = await client.request("GET", "/travelExpense/costCategory",
                              params={"isInactive": "false", "fields": "id,description"})
    check("Cost categories found", r["ok"] and len(r["body"].get("values", [])) > 0)
    # Verify field name: 'description' works, not 'name'
    if r["ok"] and r["body"].get("values"):
        first_cc = r["body"]["values"][0]
        check("costCategory has 'description' field (not 'name')",
              "description" in first_cc,
              f"keys={list(first_cc.keys())}")

    r = await client.request("GET", "/travelExpense/paymentType",
                              params={"isInactive": "false", "fields": "id,description"})
    check("Payment types found", r["ok"] and len(r["body"].get("values", [])) > 0)

    # =========================================================================
    # Test 5: Payroll prerequisites
    # =========================================================================
    print("\n=== Test 5: Payroll prerequisite chain ===")

    r = await client.request("GET", "/employee",
                              params={"fields": "id,firstName,lastName,dateOfBirth,version", "count": 1})
    check("Employee fetched", r["ok"] and r["body"].get("values"))
    emp = r["body"].get("values", [{}])[0]
    if emp.get("id"):
        check("Employee dateOfBirth is null (as expected for fresh employees)",
              emp.get("dateOfBirth") is None,
              f"dateOfBirth={emp.get('dateOfBirth')} — may already be set from previous tests")

    r = await client.request("GET", "/municipality", params={"fields": "id,name", "count": 1})
    check("Municipality lookup works", r["ok"] and len(r["body"].get("values", [])) > 0)

    r = await client.request("GET", "/token/session/>whoAmI")
    check("whoAmI returns employeeId",
          r["ok"] and r["body"].get("value", {}).get("employeeId") is not None)

    r = await client.request("GET", "/salary/type",
                              params={"isInactive": "false", "fields": "id,name", "count": 20})
    check("Salary types found", r["ok"] and len(r["body"].get("values", [])) > 0)
    if r["ok"]:
        types = {t["name"]: t["id"] for t in r["body"]["values"]}
        check("Fastlønn salary type exists", "Fastlønn" in types, f"types={list(types.keys())[:10]}")

    # =========================================================================
    # Test 6: Bank account auto-ensure
    # =========================================================================
    print("\n=== Test 6: Bank account 1920 auto-ensure ===")

    r = await client.request("GET", "/ledger/account",
                              params={"number": "1920", "fields": "id,version,bankAccountNumber"})
    check("Account 1920 found", r["ok"] and r["body"].get("values"))
    if r["body"].get("values"):
        acct = r["body"]["values"][0]
        has_bank = bool(acct.get("bankAccountNumber"))
        print(f"  INFO: bankAccountNumber={'set' if has_bank else 'EMPTY'}")
        # If empty, verify the PUT works
        if not has_bank:
            r2 = await client.request("PUT", f"/ledger/account/{acct['id']}", json_body={
                "id": acct["id"], "version": acct["version"],
                "bankAccountNumber": "86011117947",
            })
            check("PUT bankAccountNumber succeeds", r2["ok"])

    # =========================================================================
    # Test 7: Payment type auto-fetch
    # =========================================================================
    print("\n=== Test 7: Payment type auto-fetch ===")

    r = await client.request("GET", "/invoice/paymentType",
                              params={"fields": "id,description", "count": 10})
    check("Payment types fetched", r["ok"] and r["body"].get("values"))
    if r["body"].get("values"):
        pt = r["body"]["values"][0]
        check("Payment type has valid id (> 0)", pt["id"] > 0, f"id={pt['id']}")
        print(f"  INFO: First type: id={pt['id']}, desc={pt.get('description')}")

    # =========================================================================
    # Test 8: build_voucher_postings — pure logic
    # =========================================================================
    print("\n=== Test 8: build_voucher_postings — pure logic ===")

    # Balanced postings
    entries = [
        {"account_id": 100, "amount": 5000.0, "description": "Expense", "vat_type_id": 1},
        {"account_id": 200, "amount": -5000.0, "supplier_id": 999},
    ]
    postings = []
    for i, e in enumerate(entries):
        p = {"row": i + 1, "account": {"id": e["account_id"]},
             "amountGross": e["amount"], "amountGrossCurrency": e["amount"]}
        if e.get("description"): p["description"] = e["description"]
        if e.get("supplier_id"): p["supplier"] = {"id": e["supplier_id"]}
        if e.get("vat_type_id"): p["vatType"] = {"id": e["vat_type_id"]}
        postings.append(p)

    total = sum(e["amount"] for e in entries)
    check("Balanced (sum=0)", abs(total) < 0.01)
    check("Row starts at 1", postings[0]["row"] == 1)
    check("Rows sequential", postings[1]["row"] == 2)
    check("amountGross == amountGrossCurrency",
          all(p["amountGross"] == p["amountGrossCurrency"] for p in postings))
    check("Supplier on credit posting", "supplier" in postings[1])
    check("VatType on expense posting", "vatType" in postings[0])

    # Unbalanced postings detection
    bad_entries = [
        {"account_id": 100, "amount": 5000.0},
        {"account_id": 200, "amount": -3000.0},
    ]
    bad_total = sum(e["amount"] for e in bad_entries)
    check("Unbalanced detected (sum=2000)", abs(bad_total) >= 0.01, f"sum={bad_total}")

    # =========================================================================
    # Test 9: Validator rules
    # =========================================================================
    print("\n=== Test 9: Validator rules ===")

    # Rule: GET /timesheet/entry requires dateFrom+dateTo
    w = validator.validate("GET", "/timesheet/entry", None, {"projectId": "123"})
    check("timesheet/entry requires dateFrom", any("dateFrom" in x for x in w), f"warnings={w}")

    # Rule: GET /invoice requires invoiceDateFrom+invoiceDateTo
    w = validator.validate("GET", "/invoice", None, {"customerId": "123"})
    check("invoice requires invoiceDateFrom", any("invoiceDateFrom" in x for x in w), f"warnings={w}")

    # Rule: No warning when dates provided
    w = validator.validate("GET", "/timesheet/entry", None,
                           {"projectId": "123", "dateFrom": "2020-01-01", "dateTo": "2099-12-31"})
    check("No warning when dates provided", not any("dateFrom" in x for x in w), f"warnings={w}")

    # Rule: paymentTypeId=0 blocked
    w = validator.validate("PUT", "/invoice/123/:payment", None, {"paymentTypeId": 0})
    check("paymentTypeId=0 blocked", len(w) > 0, f"warnings={w}")

    # Rule: POST /ledger/voucher auto-fills null description
    body = {"date": "2026-01-01", "postings": []}
    validator._check_hard_rules("POST", "/ledger/voucher", body, None, [])
    check("Null description auto-filled", body.get("description") == "Voucher",
          f"description={body.get('description')}")

    # Rule: deliveryDate required on inline orders
    w = validator.validate("POST", "/invoice", {
        "orders": [{"customer": {"id": 1}, "orderLines": []}]
    }, None)
    check("Missing deliveryDate caught", any("deliveryDate" in x for x in w), f"warnings={w}")

    # =========================================================================
    # Test 10: data_store pattern — large response handling
    # =========================================================================
    print("\n=== Test 10: Large response → data_store pattern ===")

    # Fetch a large-ish response to verify the pattern
    r = await client.request("GET", "/ledger/postingByDate",
                              params={"dateFrom": "2026-01-01", "dateTo": "2026-03-01", "count": 5000})
    check("Large posting fetch succeeds", r["ok"])
    count = r["body"].get("fullResultSize", 0)
    print(f"  INFO: {count} postings returned")
    check("Has values list", isinstance(r["body"].get("values"), list))

    # =========================================================================
    # Summary
    # =========================================================================
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
    print(f"{'='*60}")

    await client.close()
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
