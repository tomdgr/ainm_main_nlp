"""Task: Register supplier invoice (without PDF — details given in text)."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class SupplierInvoiceTask(BaseTask):
    """Tier 2 task: Register a supplier invoice from text details (no PDF).

    The prompt gives supplier name, org number, invoice number, amount incl. VAT,
    expense account, and due date. The agent must create the supplier invoice
    via voucher + PUT /supplierInvoice/voucher/{id}/postings.
    """

    name = "Register Supplier Invoice"
    tier = 2
    optimal_calls = 5  # supplier lookup + account lookup + voucherType lookup + POST voucher + PUT supplierInvoice

    prompts = [
        "Leverandøren Fjordservice AS (org.nr 912345678) har sendt faktura INV-2026-1234 på 87500 kr inkl. MVA. Konto: 6340. Forfall: 2026-02-15. Registrer leverandørfakturaen.",
        "We have received invoice INV-2026-3205 from the supplier Ironbridge Ltd (org no. 828254375) for 24500 NOK including VAT. The amount relates to office services (account 6590). Due date: 2026-03-01. Register the supplier invoice with the correct input VAT (25%).",
        "Leverandøren Nordlys Teknikk AS (org.nr 976543210) har sendt faktura INV-2026-0871 på 43750 kr inkl. MVA. Kontér på konto 6300. Forfallsdato: 2026-04-10. Registrer leverandørfakturaen med korrekt inngående MVA.",
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        # Extract org number (9 digits)
        org_match = re.search(r'\b(\d{9})\b', prompt)
        if org_match:
            result["organizationNumber"] = org_match.group(1)

        # Extract supplier name — between common patterns
        name_patterns = [
            r'[Ll]everandøren\s+(.+?)\s*\(org',
            r'supplier\s+(.+?)\s*\(org',
        ]
        for pattern in name_patterns:
            m = re.search(pattern, prompt, re.IGNORECASE)
            if m:
                result["supplier_name"] = m.group(1).strip()
                break

        # Extract invoice number (INV-XXXX-NNNN)
        inv_match = re.search(r'(INV-[\d-]+)', prompt, re.IGNORECASE)
        if inv_match:
            result["invoice_number"] = inv_match.group(1)

        # Extract amount (incl. VAT)
        amount_match = re.search(r'(\d[\d\s]*\d)\s*(?:kr|NOK)', prompt)
        if amount_match:
            result["amount_incl_vat"] = float(amount_match.group(1).replace(" ", ""))

        # Extract account number (4-digit)
        acct_match = re.search(r'(?:konto|account)[:\s]*(\d{4})', prompt, re.IGNORECASE)
        if acct_match:
            result["account_number"] = int(acct_match.group(1))

        # Extract due date
        due_match = re.search(r'(?:forfall|forfallsdato|[Dd]ue\s*date)[:\s]*(\d{4}-\d{2}-\d{2})', prompt)
        if due_match:
            result["due_date"] = due_match.group(1)

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Pre-create the supplier so the agent can find it."""
        org_nr = expected.get("organizationNumber", "")
        name = expected.get("supplier_name", "Test Supplier")

        if not org_nr:
            return

        # Check if supplier already exists
        resp = self._api(base_url, session_token, "GET", "/supplier", {
            "organizationNumber": org_nr, "fields": "id,name", "count": 1,
        })
        suppliers = resp.get("values", [])

        if suppliers:
            supplier_id = suppliers[0]["id"]
            print(f"  Supplier already exists: id={supplier_id}, name={suppliers[0].get('name')}")
        else:
            resp = self._api(base_url, session_token, "POST", "/supplier", json_body={
                "name": name,
                "organizationNumber": org_nr,
                "isSupplier": True,
            })
            supplier_id = resp.get("value", {}).get("id")
            print(f"  Created supplier: id={supplier_id}, name={name}")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        org_nr = expected.get("organizationNumber", "")
        invoice_number = expected.get("invoice_number", "")
        amount_incl_vat = expected.get("amount_incl_vat", 0)
        account_number = expected.get("account_number", 0)

        # 1. Check supplier exists
        resp = verifier.get("/supplier", {
            "organizationNumber": org_nr,
            "fields": "id,name,organizationNumber",
            "count": 1,
        })
        suppliers = resp.get("values", [])
        supplier = suppliers[0] if suppliers else None

        checks.append(Check(
            name="Supplier exists",
            passed=supplier is not None,
            expected=f"org={org_nr}",
            actual="FOUND" if supplier else "NOT FOUND",
        ))

        if not supplier:
            return checks

        supplier_id = supplier["id"]

        # 2. Check for supplier invoice via /supplierInvoice
        resp = verifier.get("/supplierInvoice", {
            "supplierId": str(supplier_id),
            "invoiceDateFrom": "2020-01-01",
            "invoiceDateTo": "2099-12-31",
            "fields": "id,invoiceNumber,amount,amountCurrency,supplier(*),voucher(*)",
            "count": 20,
        })
        supplier_invoices = resp.get("values", [])

        # Try to match by invoice number
        matched_si = None
        for si in supplier_invoices:
            si_inv_nr = si.get("invoiceNumber", "") or ""
            if invoice_number and invoice_number.lower() in si_inv_nr.lower():
                matched_si = si
                break

        # If no match by invoice number, take the most recent one
        if not matched_si and supplier_invoices:
            matched_si = supplier_invoices[-1]

        checks.append(Check(
            name="Supplier invoice registered",
            passed=matched_si is not None,
            expected=f"supplierInvoice with inv={invoice_number}",
            actual=f"FOUND (id={matched_si.get('id')})" if matched_si else "NOT FOUND — check /supplierInvoice",
            points=3,
        ))

        if not matched_si:
            # Fall back: check for a voucher with matching postings
            return self._check_voucher_fallback(verifier, expected, checks)

        # 3. Check amount on the supplier invoice
        si_amount = matched_si.get("amount", 0) or matched_si.get("amountCurrency", 0)
        checks.append(Check(
            name="Invoice amount correct",
            passed=abs(si_amount - amount_incl_vat) < 10,
            expected=str(amount_incl_vat),
            actual=str(si_amount),
            points=2,
        ))

        # 4. Check voucher postings (expense account debit + 2400 credit)
        voucher_ref = matched_si.get("voucher", {})
        voucher_id = voucher_ref.get("id") if voucher_ref else None

        if voucher_id:
            self._check_voucher_postings(verifier, voucher_id, expected, checks)

        return checks

    def _check_voucher_fallback(self, verifier, expected: dict, checks: list[Check]) -> list[Check]:
        """If no supplierInvoice found, check if at least a voucher was created with the right postings."""
        account_number = expected.get("account_number", 0)
        amount_incl_vat = expected.get("amount_incl_vat", 0)

        # Find the expense account ID
        acct_resp = verifier.get("/ledger/account", {
            "number": str(account_number), "fields": "id,number,name", "count": 1,
        })
        acct_values = acct_resp.get("values", [])
        if not acct_values:
            return checks

        acct_id = acct_values[0].get("id")

        # Search recent vouchers
        resp = verifier.get("/ledger/voucher", {
            "dateFrom": "2026-01-01",
            "dateTo": "2099-12-31",
            "count": 10,
            "sorting": "-number",
        })
        vouchers = resp.get("values", [])

        for v in vouchers[:5]:
            v_detail = verifier.get(f"/ledger/voucher/{v['id']}", {
                "fields": "id,description,vendorInvoiceNumber,postings(*)",
            })
            v_data = v_detail.get("value", {})
            for p in v_data.get("postings", []):
                p_acct_id = p.get("account", {}).get("id")
                p_amount = abs(p.get("amountGross", 0) or 0)
                if p_acct_id == acct_id and abs(p_amount - amount_incl_vat) < 10:
                    checks.append(Check(
                        name="Voucher with expense posting found (fallback)",
                        passed=True,
                        expected=f"posting on account {account_number} for ~{amount_incl_vat}",
                        actual=f"voucher {v['id']} has matching posting",
                        points=1,
                    ))
                    return checks

        checks.append(Check(
            name="Voucher with expense posting (fallback)",
            passed=False,
            expected=f"posting on account {account_number} for ~{amount_incl_vat}",
            actual="No matching voucher found",
            points=1,
        ))
        return checks

    def _check_voucher_postings(self, verifier, voucher_id: int, expected: dict, checks: list[Check]):
        """Verify voucher has correct expense account debit and 2400 credit."""
        account_number = expected.get("account_number", 0)
        amount_incl_vat = expected.get("amount_incl_vat", 0)

        v_detail = verifier.get(f"/ledger/voucher/{voucher_id}", {
            "fields": "id,description,vendorInvoiceNumber,postings(*)",
        })
        v_data = v_detail.get("value", {})
        postings = v_data.get("postings", [])

        # Check for expense account posting (debit, positive amount)
        has_expense = False
        for p in postings:
            p_acct = p.get("account", {})
            p_acct_nr = p_acct.get("number", 0)
            p_amount = p.get("amountGross", 0) or 0
            if p_acct_nr == account_number and p_amount > 0:
                has_expense = True
                break

        checks.append(Check(
            name=f"Expense posting on account {account_number}",
            passed=has_expense,
            expected=f"debit on {account_number}",
            actual="FOUND" if has_expense else "NOT FOUND in voucher postings",
        ))

        # Check for account 2400 (Leverandørgjeld) credit posting
        has_2400_credit = False
        for p in postings:
            p_acct = p.get("account", {})
            p_acct_nr = p_acct.get("number", 0)
            p_amount = p.get("amountGross", 0) or 0
            if p_acct_nr == 2400 and p_amount < 0:
                has_2400_credit = True
                break

        checks.append(Check(
            name="Credit posting on account 2400 (Leverandørgjeld)",
            passed=has_2400_credit,
            expected="credit on 2400",
            actual="FOUND" if has_2400_credit else "NOT FOUND in voucher postings",
        ))
