"""Task 20/21: Register supplier invoice from attached PDF (leverandørfaktura)."""

import base64
import io
import random

import pymupdf

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


def _generate_invoice_pdf(
    supplier_name: str,
    org_number: str,
    address: str,
    invoice_number: str,
    invoice_date: str,
    due_date: str,
    description: str,
    net_amount: float,
    vat_rate: float,
    gross_amount: float,
    vat_amount: float,
    account_number: int,
    bank_account: str,
) -> bytes:
    """Generate a supplier invoice PDF matching competition format.

    Layout (from real examples):
        Supplier Name (bold)
        Org.nr: XXXXXXXXX
        Street, PostalCode City

                    FAKTURA

        Fakturanummer:    INV-XXXX-XXXX
        Fakturadato:      DD.MM.YYYY
        Forfallsdato:     DD.MM.YYYY

        Til: Ditt firma

        Beskrivelse                             Belop
        ____________________________________________
        Item description                    XXXXX kr

                                MVA 25%: XXXXX kr
                              Totalt: XXXXX kr

        Konto: XXXX
        Bankkonto: XXXXXXXXXXX
    """
    doc = pymupdf.open()
    page = doc.new_page(width=595.28, height=841.89)  # A4

    # --- Supplier header (top-left) ---
    y = 50
    page.insert_text((50, y), supplier_name, fontsize=16, fontname="hebo")
    y += 20
    page.insert_text((50, y), f"Org.nr: {org_number}", fontsize=10, fontname="helv")
    y += 16
    page.insert_text((50, y), address, fontsize=10, fontname="helv")

    # --- FAKTURA header (centered) ---
    y += 50
    page.insert_text((210, y), "FAKTURA", fontsize=24, fontname="hebo")

    # --- Invoice details ---
    y += 50
    label_x = 50
    value_x = 210

    page.insert_text((label_x, y), "Fakturanummer:", fontsize=11, fontname="hebo")
    page.insert_text((value_x, y), invoice_number, fontsize=11, fontname="helv")
    y += 24

    page.insert_text((label_x, y), "Fakturadato:", fontsize=11, fontname="hebo")
    page.insert_text((value_x, y), invoice_date, fontsize=11, fontname="helv")
    y += 24

    page.insert_text((label_x, y), "Forfallsdato:", fontsize=11, fontname="hebo")
    page.insert_text((value_x, y), due_date, fontsize=11, fontname="helv")

    # --- Recipient ---
    y += 35
    page.insert_text((label_x, y), "Til: Ditt firma", fontsize=11, fontname="hebo")

    # --- Item table ---
    y += 40
    page.insert_text((label_x, y), "Beskrivelse", fontsize=11, fontname="hebo")
    page.insert_text((460, y), "Belop", fontsize=11, fontname="hebo")
    y += 5
    page.draw_line((label_x, y), (545, y))

    y += 20
    page.insert_text((label_x, y), description, fontsize=11, fontname="helv")
    amount_str = f"{int(net_amount)} kr"
    page.insert_text((460, y), amount_str, fontsize=11, fontname="helv")

    # --- VAT and Total ---
    y += 30
    vat_str = f"MVA {int(vat_rate * 100)}%: {int(vat_amount)} kr"
    page.insert_text((380, y), vat_str, fontsize=10, fontname="helv")

    y += 22
    total_str = f"Totalt: {int(gross_amount)} kr"
    page.insert_text((370, y), total_str, fontsize=12, fontname="hebo")

    # --- Account and bank info ---
    y += 40
    page.insert_text((label_x, y), f"Konto: {account_number}", fontsize=10, fontname="helv")
    y += 18
    page.insert_text((label_x, y), f"Bankkonto: {bank_account}", fontsize=10, fontname="helv")

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _random_bank_account() -> str:
    """Generate a random 11-digit Norwegian bank account number."""
    return "".join(str(random.randint(0, 9)) for _ in range(11))


def _random_invoice_number() -> str:
    """Generate a random invoice number like INV-2026-XXXX."""
    return f"INV-2026-{random.randint(1000, 9999)}"


# Invoice templates — each defines a scenario the agent must handle
_INVOICES = [
    {
        "supplier_name": "Nordvik Konsult AS",
        "org_number": "912345678",
        "address": "Storgata 45, 0182 Oslo",
        "postal_code": "0182",
        "city": "Oslo",
        "street": "Storgata 45",
        "description": "IT-konsulenttjenester",
        "net_amount": 24000.0,
        "vat_rate": 0.25,
        "account_number": 6300,
        "prompt": (
            "Du har mottatt en leverandørfaktura (se vedlagt PDF). "
            "Registrer fakturaen i Tripletex. Opprett leverandøren om den ikke finnes. "
            "Bruk riktig utgiftskonto og inngående MVA."
        ),
    },
    {
        "supplier_name": "Bergen Renhold AS",
        "org_number": "987654321",
        "address": "Vestre Strømkaien 7, 5008 Bergen",
        "postal_code": "5008",
        "city": "Bergen",
        "street": "Vestre Strømkaien 7",
        "description": "Kontorrengjøring",
        "net_amount": 18500.0,
        "vat_rate": 0.25,
        "account_number": 6340,
        "prompt": (
            "Vi har mottatt vedlagt leverandørfaktura (PDF). "
            "Registrer den i Tripletex og opprett leverandøren hvis den ikke allerede finnes. "
            "Bruk riktig utgiftskonto og sørg for korrekt inngående MVA."
        ),
    },
    {
        "supplier_name": "Tromsø Data AS",
        "org_number": "976543210",
        "address": "Sjøgata 12, 9008 Tromsø",
        "postal_code": "9008",
        "city": "Tromsø",
        "street": "Sjøgata 12",
        "description": "Programvarelisenser",
        "net_amount": 45000.0,
        "vat_rate": 0.25,
        "account_number": 6540,
        "prompt": (
            "Vedlagt finner du en leverandørfaktura i PDF-format. "
            "Registrer fakturaen i Tripletex. Opprett leverandøren om nødvendig. "
            "Bruk utgiftskontoen som er angitt i fakturaen og korrekt MVA-behandling."
        ),
    },
]


class SupplierInvoicePDFTask(BaseTask):
    """Tier 3 task: Register supplier invoice from attached PDF.

    This simulates task_20/task_21 from the competition. The agent receives a PDF
    supplier invoice and must:
    1. Read/extract all fields from the PDF
    2. Create the supplier if it doesn't exist (with org number, address)
    3. Create a voucher with correct expense posting and leverandørgjeld (2400)
    """

    name = "Register Supplier Invoice (PDF)"
    tier = 3
    optimal_calls = 6  # supplier check + supplier create + account lookups + voucherType + POST voucher

    def __init__(self, task_id: str):
        super().__init__(task_id)
        self._current_invoice: dict | None = None

    @property
    def prompts(self) -> list[str]:
        return [inv["prompt"] for inv in _INVOICES]

    def _pick_invoice(self, prompt: str) -> dict:
        """Match prompt to invoice template."""
        for inv in _INVOICES:
            if prompt == inv["prompt"]:
                return inv
        # Default to first
        return _INVOICES[0]

    def _build_invoice_data(self, invoice: dict) -> dict:
        """Build full invoice data with computed fields."""
        net = invoice["net_amount"]
        vat_rate = invoice["vat_rate"]
        vat_amount = round(net * vat_rate)
        gross = net + vat_amount
        bank_account = _random_bank_account()
        inv_number = _random_invoice_number()

        # Random dates in 2026
        month = random.randint(1, 6)
        day = random.randint(1, 28)
        invoice_date = f"{day:02d}.{month:02d}.2026"
        due_month = month + 1 if month < 12 else 12
        due_date = f"{day:02d}.{due_month:02d}.2026"

        # ISO date for API queries
        iso_invoice_date = f"2026-{month:02d}-{day:02d}"
        iso_due_date = f"2026-{due_month:02d}-{day:02d}"

        return {
            **invoice,
            "vat_amount": vat_amount,
            "gross_amount": gross,
            "bank_account": bank_account,
            "invoice_number": inv_number,
            "invoice_date": invoice_date,
            "due_date": due_date,
            "iso_invoice_date": iso_invoice_date,
            "iso_due_date": iso_due_date,
        }

    def extract_expected(self, prompt: str) -> dict:
        invoice = self._pick_invoice(prompt)
        data = self._build_invoice_data(invoice)
        self._current_invoice = data
        return {
            "supplier_name": data["supplier_name"],
            "org_number": data["org_number"],
            "address": data["address"],
            "street": data["street"],
            "postal_code": data["postal_code"],
            "city": data["city"],
            "description": data["description"],
            "net_amount": data["net_amount"],
            "vat_amount": data["vat_amount"],
            "gross_amount": data["gross_amount"],
            "vat_rate": data["vat_rate"],
            "account_number": data["account_number"],
            "bank_account": data["bank_account"],
            "invoice_number": data["invoice_number"],
            "invoice_date": data["invoice_date"],
            "due_date": data["due_date"],
            "iso_invoice_date": data["iso_invoice_date"],
        }

    def get_files(self, expected: dict) -> list[dict]:
        """Generate and return a PDF supplier invoice."""
        data = self._current_invoice
        if not data:
            data = expected  # fallback

        pdf_bytes = _generate_invoice_pdf(
            supplier_name=data["supplier_name"],
            org_number=data["org_number"],
            address=data["address"],
            invoice_number=data["invoice_number"],
            invoice_date=data["invoice_date"],
            due_date=data["due_date"],
            description=data["description"],
            net_amount=data["net_amount"],
            vat_rate=data["vat_rate"],
            gross_amount=data["gross_amount"],
            vat_amount=data["vat_amount"],
            account_number=data["account_number"],
            bank_account=data["bank_account"],
        )
        return [{
            "filename": "leverandorfaktura.pdf",
            "content_base64": base64.b64encode(pdf_bytes).decode(),
            "mime_type": "application/pdf",
        }]

    def setup(self, base_url: str, session_token: str, expected: dict):
        """No setup needed — the agent creates the supplier itself."""
        pass

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        org_number = expected.get("org_number", "")
        gross_amount = expected.get("gross_amount", 0)
        account_number = expected.get("account_number", 0)

        # ── Check 1: Supplier exists with correct org number ──
        resp = verifier.get("/supplier", {
            "organizationNumber": org_number,
            "fields": "id,name,organizationNumber",
            "count": 5,
        })
        suppliers = resp.get("values", [])
        supplier = suppliers[0] if suppliers else None

        checks.append(Check(
            name="Supplier created with correct org number",
            passed=supplier is not None,
            expected=f"supplier with org={org_number}",
            actual=f"{supplier['name']} (id={supplier['id']})" if supplier else "NOT FOUND",
            points=2,
        ))

        if not supplier:
            return checks

        # ── Check 2: Voucher exists with posting on the expense account ──
        # Look up the expense account ID
        acct_resp = verifier.get("/ledger/account", {
            "number": str(account_number),
            "fields": "id,number,name",
            "count": 1,
        })
        expense_acct_values = acct_resp.get("values", [])
        expense_acct_id = expense_acct_values[0]["id"] if expense_acct_values else None

        # Look up account 2400 (Leverandørgjeld)
        acct2400_resp = verifier.get("/ledger/account", {
            "number": "2400",
            "fields": "id,number,name",
            "count": 1,
        })
        acct2400_values = acct2400_resp.get("values", [])
        acct2400_id = acct2400_values[0]["id"] if acct2400_values else None

        # Search recent vouchers for matching postings
        iso_date = expected.get("iso_invoice_date", "2026-01-01")
        resp = verifier.get("/ledger/voucher", {
            "dateFrom": iso_date,
            "dateTo": "2099-12-31",
            "count": 20,
            "sorting": "-id",
        })
        vouchers = resp.get("values", [])

        expense_posting = None
        credit_posting = None
        matching_voucher = None

        for v in vouchers[:15]:
            v_detail = verifier.get(f"/ledger/voucher/{v['id']}", {
                "fields": "id,description,postings(*)",
            })
            postings = v_detail.get("value", {}).get("postings", [])

            for p in postings:
                p_acct_id = p.get("account", {}).get("id")
                p_amount_gross = p.get("amountGross") or 0

                # Match expense posting: correct account, positive or negative gross close to expected
                if expense_acct_id and p_acct_id == expense_acct_id:
                    # The expense posting can be positive (debit) or negative depending on convention
                    if abs(abs(p_amount_gross) - gross_amount) < 500:
                        expense_posting = p
                        matching_voucher = v

                # Match credit posting on 2400 (leverandørgjeld)
                if acct2400_id and p_acct_id == acct2400_id:
                    if abs(abs(p_amount_gross) - gross_amount) < 500:
                        credit_posting = p

            if expense_posting:
                break

        checks.append(Check(
            name="Voucher with expense posting on correct account",
            passed=expense_posting is not None,
            expected=f"posting on account {account_number} for ~{gross_amount}",
            actual=(
                f"account {account_number}, amountGross={expense_posting.get('amountGross', 0)}"
                if expense_posting else "NOT FOUND"
            ),
            points=3,
        ))

        # ── Check 3: Voucher has posting on account 2400 (Leverandørgjeld) ──
        checks.append(Check(
            name="Credit posting on account 2400 (Leverandørgjeld)",
            passed=credit_posting is not None,
            expected=f"posting on account 2400 for ~{gross_amount}",
            actual=(
                f"amountGross={credit_posting.get('amountGross', 0)}"
                if credit_posting else "NOT FOUND"
            ),
            points=2,
        ))

        return checks
