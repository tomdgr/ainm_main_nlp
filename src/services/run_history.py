"""Dynamic lessons from previous runs.

Parses run logs, classifies incoming prompts to task types, and provides
task-specific playbooks (optimal API flow + pitfalls) for injection into
the agent's user message.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ParsedRun:
    task_type: str
    revision: str
    filepath: str
    prompt: str
    api_sequence: list[tuple[str, str, int]] = field(default_factory=list)  # (method, path, status)
    total_calls: int = 0
    total_errors: int = 0
    duration_s: float = 0.0
    error_messages: list[str] = field(default_factory=list)


@dataclass
class TaskPlaybook:
    task_type: str
    description: str
    golden_path: list[str]
    key_lessons: list[str]
    common_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Multilingual keyword classifier
# ---------------------------------------------------------------------------

TASK_KEYWORDS: dict[str, list[tuple[set[str], float]]] = {
    "task_01": [  # Create employee
        ({"employee", "ansatt", "tilsett", "mitarbeiter", "empleado", "empregado", "employé"}, 3.0),
        ({"born", "født", "fødd", "geboren", "nacido", "nascido", "né"}, 2.0),
        ({"email", "e-mail", "correo", "courriel"}, 0.5),
    ],
    "task_02": [  # Create customer
        ({"customer", "kunde", "client", "cliente"}, 2.0),
        ({"address", "adresse", "endereço", "dirección"}, 2.0),
        ({"organization", "organisasjon", "org"}, 1.0),
        ({"post@", "info@"}, 1.0),
    ],
    "task_03": [  # Create product
        ({"product", "produkt", "produit", "producto", "produto"}, 2.0),
        ({"product number", "produktnummer", "numéro de produit", "número de producto", "número de produto"}, 2.5),
        ({"price", "pris", "prix", "precio", "preço", "preis"}, 1.0),
        ({"25 %", "25%"}, 0.5),
    ],
    "task_04": [  # Register supplier
        ({"supplier", "leverandør", "lieferant", "fournisseur", "proveedor", "fornecedor"}, 3.0),
        ({"register", "registrer", "registrieren", "enregistrez", "registe"}, 1.5),
        ({"faktura@"}, 2.0),
    ],
    "task_05": [  # Create departments
        ({"department", "avdeling", "abteilung", "departamento", "département"}, 3.0),
        ({"three", "tre", "drei", "tres", "trois", "três"}, 2.0),
    ],
    "task_06": [  # Create & send invoice
        ({"invoice", "faktura", "rechnung", "facture", "factura", "fatura"}, 1.5),
        ({"send", "sende", "senden", "envoy", "enviar"}, 2.0),
        ({"excluding vat", "eksklusiv mva", "ohne mwst", "hors tva", "sin iva", "sem iva",
          "ekskl", "excl"}, 1.5),
    ],
    "task_07": [  # Register payment on existing invoice
        ({"outstanding", "utestående", "uteståande", "offene", "pendiente", "em aberto"}, 3.0),
        ({"payment", "betaling", "zahlung", "paiement", "pago", "pagamento"}, 2.0),
        ({"register", "registrer", "registrieren"}, 1.0),
    ],
    "task_08": [  # Create project
        ({"project", "prosjekt", "projekt", "projet", "proyecto", "projeto"}, 2.0),
        ({"project manager", "prosjektleder", "prosjektleiar", "projektleiter",
          "chef de projet", "gerente de projeto", "director del proyecto", "responsable du projet"}, 3.0),
    ],
    "task_09": [  # Multi-VAT invoice
        ({"invoice", "faktura", "fatura", "factura", "rechnung", "facture"}, 1.0),
        ({"three", "tre", "três", "tres", "trois", "drei"}, 1.0),
        ({"product line", "produktlinje", "ligne de produit", "línea de producto"}, 2.0),
        ({"15 %", "15%"}, 2.5),
        ({"0 %", "0%", "exempt", "isento", "exento", "exempté"}, 1.5),
    ],
    "task_10": [  # Order → invoice → payment
        ({"order", "ordre", "bestilling", "auftrag", "pedido", "commande"}, 2.0),
        ({"convert", "konverter", "umwandeln", "convertir", "converter"}, 2.5),
        ({"full payment", "full betaling", "vollständige zahlung", "pagamento completo",
          "pago completo", "paiement complet"}, 2.5),
    ],
    "task_11": [  # Supplier invoice (voucher)
        ({"inv-20"}, 3.0),
        ({"supplier invoice", "leverandørfaktura", "facture fournisseur",
          "fatura do fornecedor", "factura del proveedor", "lieferantenrechnung"}, 3.0),
        ({"ttc", "inkl mva", "inkl.", "incluído", "incluido", "inklusive"}, 2.0),
        ({"account", "konto", "compte", "conta", "cuenta"}, 0.5),
    ],
    "task_12": [  # Payroll
        ({"payroll", "lønn", "gehaltsabrechnung", "paie", "nómina", "folha de pagamento"}, 3.0),
        ({"salary", "grunnlønn", "grundgehalt", "salaire de base", "salario base"}, 2.0),
        ({"bonus", "engangsbonus", "prime", "prima"}, 2.0),
    ],
    "task_13": [  # Travel expense
        ({"travel expense", "reiseregning", "reise", "note de frais", "nota de gastos",
          "nota de despesas", "reisekostenabrechnung"}, 3.0),
        ({"per diem", "diett", "dieta", "indemnités", "dietas"}, 2.0),
        ({"days", "dager", "dagar", "jours", "días", "dias", "tage"}, 1.0),
    ],
    "task_14": [  # Credit note
        ({"credit note", "kreditnota", "gutschrift", "note de crédit", "nota de crédito"}, 3.0),
        ({"complained", "reklamert", "reklamiert", "reclamou", "reclamado", "réclamé"}, 2.5),
        ({"reverse", "reversere", "storniert"}, 1.0),
    ],
    "task_15": [  # Project fixed price + partial invoice
        ({"fixed price", "fastpris", "festpreis", "prix fixe", "precio fijo", "preço fixo"}, 3.0),
        ({"milestone", "delbetaling", "teilzahlung", "acompte"}, 2.0),
        ({"75%", "75 %", "50%", "50 %"}, 1.5),
    ],
    "task_16": [  # Time tracking + project invoice
        ({"log hours", "registrer timer", "stunden erfassen", "enregistrer heures",
          "registrar horas", "logg timer"}, 3.0),
        ({"hourly rate", "timesats", "stundensatz", "taux horaire", "tarifa por hora"}, 2.0),
        ({"activity", "aktivitet", "aktivität", "activité", "actividad", "atividade"}, 1.5),
    ],
    "task_17": [  # Custom accounting dimension + voucher
        ({"dimension", "regnskapsdimensjon", "dimensión contable", "dimension comptable"}, 3.0),
        ({"voucher", "bilag", "asiento", "écriture"}, 1.5),
        ({"values", "verdier", "valores", "valeurs", "werte"}, 1.0),
    ],
    "task_18": [  # Reverse bank payment
        ({"returned", "devolvido", "devuelto", "retourné", "zurückgegeben"}, 3.0),
        ({"reverse", "reverta", "revierta", "annuler", "rückgängig"}, 2.5),
        ({"bank"}, 1.0),
    ],
}


# ---------------------------------------------------------------------------
# Curated playbooks (domain knowledge not reliably extractable from logs)
# ---------------------------------------------------------------------------

CURATED_PLAYBOOKS: dict[str, dict] = {
    "task_01": {
        "description": "Create employee with employment record",
        "golden_path": [
            "GET /department (find a department ID — required for employee creation)",
            "POST /employee (with firstName, lastName, dateOfBirth, email, userType='STANDARD', department={id})",
            "POST /employee/employment (with employee={id}, startDate, taxDeductionCode='loennFraHovedarbeidsgiver')",
        ],
        "key_lessons": [
            "startDate does NOT exist on the employee object — it goes on the employment record",
            "userType and department are required on POST /employee",
            "taxDeductionCode on employment, e.g., 'loennFraHovedarbeidsgiver'",
        ],
    },
    "task_02": {
        "description": "Create customer with address",
        "golden_path": [
            "POST /customer (with name, organizationNumber, email, invoiceEmail, postalAddress={addressLine1, postalCode, city}, physicalAddress={...})",
        ],
        "key_lessons": [
            "Set BOTH email and invoiceEmail when email is provided",
            "postalAddress and physicalAddress are nested objects, not separate endpoints",
        ],
    },
    "task_03": {
        "description": "Create product with number and price",
        "golden_path": [
            "GET /ledger/vatType?id=3 (verify 25% outgoing VAT exists)",
            "POST /product (with name, number, priceExcludingVatCurrency, vatType={id:3})",
        ],
        "key_lessons": [
            "vatType id=3 is 'Utgående avgift, høy sats' (25% outgoing VAT for sales)",
            "Use priceExcludingVatCurrency for the price excluding VAT",
        ],
    },
    "task_04": {
        "description": "Register supplier",
        "golden_path": [
            "POST /supplier (with name, organizationNumber, email, invoiceEmail)",
        ],
        "key_lessons": [
            "Use /supplier endpoint, NOT /customer",
            "Set BOTH email and invoiceEmail",
        ],
    },
    "task_05": {
        "description": "Create three departments",
        "golden_path": [
            "POST /department (with name) — repeat for each department",
            "OR POST /department/list (with array of departments) for batch creation",
        ],
        "key_lessons": [
            "Department only requires 'name' field",
        ],
    },
    "task_06": {
        "description": "Create and send invoice to customer",
        "golden_path": [
            "GET /customer?organizationNumber=X (find existing customer)",
            "GET /ledger/account?number=1920 (check bank account — set up if empty)",
            "POST /order (with customer, deliveryDate, orderDate, orderLines with product/price/vatType)",
            "PUT /order/{id}/:invoice (with invoiceDate, sendToCustomer=true)",
        ],
        "key_lessons": [
            "MUST set up bank account 1920 before creating invoices",
            "Orders require deliveryDate — use today's date",
            "'Excluding VAT' means the stated price is without VAT, but 25% VAT still applies (vatType id=3)",
            "Use PUT /order/{id}/:invoice to convert order to invoice and send in one step",
        ],
    },
    "task_07": {
        "description": "Find existing invoice and register full payment",
        "golden_path": [
            "GET /customer?organizationNumber=X (find customer)",
            "GET /invoice?invoiceDateFrom=2020-01-01&invoiceDateTo=2030-12-31&customerId=X (find the outstanding invoice)",
            "GET /invoice/paymentType (find payment type ID for bank payment)",
            "POST /invoice/{id}/:payment (with paymentDate, paymentTypeId, paidAmount=total INCLUDING VAT)",
        ],
        "key_lessons": [
            "Use GET /invoice/paymentType, NOT /ledger/paymentType (which 404s)",
            "paidAmount must be the TOTAL amount INCLUDING VAT (amountOutstanding field)",
            "Invoice search requires invoiceDateFrom and invoiceDateTo — use wide range",
        ],
    },
    "task_08": {
        "description": "Create project linked to customer with project manager",
        "golden_path": [
            "GET /customer?organizationNumber=X (find or create customer)",
            "GET /employee (search for project manager by name/email — create if needed)",
            "POST /project (with name, customer={id}, projectManager={id}, startDate=today)",
        ],
        "key_lessons": [
            "Projects require startDate — use today's date",
            "Project manager must be an existing employee — search or create first",
        ],
    },
    "task_09": {
        "description": "Create invoice with multiple product lines and different VAT rates",
        "golden_path": [
            "GET /customer?organizationNumber=X",
            "GET /product?number=X (for each product)",
            "GET /ledger/vatType (find VAT type IDs: id=3 for 25%, id=31 for 15%, id=5 for 0%)",
            "GET /ledger/account?number=1920 (set up bank account if needed)",
            "POST /order (with customer, deliveryDate, orderLines with correct vatType per line)",
            "PUT /order/{id}/:invoice (with invoiceDate, invoiceDueDate)",
        ],
        "key_lessons": [
            "VAT types: id=3 (25% outgoing), id=31 (15% medium rate), id=5 (0% exempt within MVA law)",
            "Always include invoiceDueDate when creating invoices (e.g., 14 days after invoiceDate)",
            "Products may already exist — search by number first",
        ],
    },
    "task_10": {
        "description": "Create order, convert to invoice, register full payment",
        "golden_path": [
            "GET /customer?organizationNumber=X",
            "GET /product?number=X (for each product)",
            "GET /ledger/account?number=1920 (set up bank account if needed)",
            "POST /order (with customer, deliveryDate, orderLines)",
            "PUT /order/{id}/:invoice (with invoiceDate, sendToCustomer=false)",
            "GET /invoice/paymentType (find payment type for bank)",
            "POST /invoice/{id}/:payment (with paidAmount = total incl VAT)",
        ],
        "key_lessons": [
            "Order → invoice → payment is a 3-step flow",
            "paidAmount must equal the total including VAT",
        ],
    },
    "task_11": {
        "description": "Register supplier invoice via ledger voucher",
        "golden_path": [
            "GET /supplier?organizationNumber=X (find supplier)",
            "GET /ledger/vatType (find id=1 for ingoing 25% VAT: 'Fradrag inngående avgift, høy sats')",
            "GET /ledger/account?number=XXXX (verify expense account exists)",
            "GET /ledger/voucherType?name=Leverandør (find supplier invoice voucher type ID)",
            "POST /ledger/voucher (with voucherType, vendorInvoiceNumber, date, postings)",
        ],
        "key_lessons": [
            "There is NO POST /supplierInvoice endpoint — use POST /ledger/voucher",
            "MUST set both amountGross AND amountGrossCurrency (same value for NOK)",
            "Posting rows start at 1 (row 0 is system-generated)",
            "vendorInvoiceNumber carries the INV-XXXX reference",
            "Gross amount is TTC (VAT included). Net = gross / 1.25 for 25% VAT",
            "Debit: expense account with vatType={id:1}. Credit: account 2400 with supplier={id}",
        ],
    },
    "task_12": {
        "description": "Run payroll for employee with salary and bonus",
        "golden_path": [
            "GET /employee (search by email or name — may need to create)",
            "POST /employee/employment (if no employment exists)",
            "Search for salary-related endpoints via search_api_spec",
            "POST /salary/transaction (or equivalent — discover via API spec)",
        ],
        "key_lessons": [
            "Payroll is complex — use search_api_spec to discover salary endpoints",
            "Employee must have an employment record before processing payroll",
            "Look for /salary/type to find the right salary codes for base salary and bonus",
        ],
    },
    "task_13": {
        "description": "Register travel expense with per diem and costs",
        "golden_path": [
            "GET /employee (find or create the employee)",
            "POST /travelExpense (with employee, travelDetails={departureDate, returnDate, departureTime, returnTime, departureFrom, destination})",
            "GET /travelExpense/rateCategory + GET /travelExpense/rate (find rate IDs for per diem)",
            "POST /travelExpense/perDiemCompensation (with travelExpense={id}, rateType, rateCategory, overnightAccommodation, location, count)",
            "GET /travelExpense/costCategory (find categories for flights, taxi, etc.)",
            "GET /travelExpense/paymentType (find payment type)",
            "POST /travelExpense/cost (for each expense item, with category, paymentType, amountCurrencyIncVat, date)",
        ],
        "key_lessons": [
            "Departure/return dates go on the PARENT TravelExpense via travelDetails, NOT on sub-objects",
            "perDiemCompensation has 'count' (number of days) — cost does NOT have count",
            "cost requires: category={id}, paymentType={id}, amountCurrencyIncVat, date",
            "overnightAccommodation enum: NONE, HOTEL, BOARDING_HOUSE_WITHOUT_COOKING, BOARDING_HOUSE_WITH_COOKING",
        ],
    },
    "task_14": {
        "description": "Create credit note for complained invoice",
        "golden_path": [
            "GET /customer?organizationNumber=X",
            "GET /invoice?customerId=X&invoiceDateFrom=2020-01-01&invoiceDateTo=2030-12-31 (find the invoice)",
            "PUT /invoice/{id}/:createCreditNote (with date, comment)",
        ],
        "key_lessons": [
            "Use PUT /invoice/{id}/:createCreditNote — don't manually create negative invoices",
            "Search for the invoice by customer and product description to identify the right one",
        ],
    },
    "task_15": {
        "description": "Set fixed price on project and invoice partial amount",
        "golden_path": [
            "GET /customer?organizationNumber=X",
            "GET or POST /employee (project manager)",
            "POST /project (with name, customer, projectManager, startDate)",
            "Use search_api_spec to find project fixed price/order endpoints",
            "POST /order (with project={id}, customer={id}, orderLines for the partial amount)",
            "PUT /order/{id}/:invoice (to create the invoice)",
        ],
        "key_lessons": [
            "The partial percentage applies to the fixed price — calculate the amount",
            "Project must be created before setting fixed price",
        ],
    },
    "task_16": {
        "description": "Log hours on project activity and generate project invoice",
        "golden_path": [
            "GET /customer?organizationNumber=X",
            "GET or POST /employee",
            "POST /project (with customer, projectManager)",
            "Use search_api_spec to find timesheet and project invoice endpoints",
            "POST /timesheet/entry (with employee, project, activity, hours, date)",
            "Generate project invoice via relevant endpoint",
        ],
        "key_lessons": [
            "Discover timesheet endpoints via search_api_spec('timesheet entry')",
            "Activity must be found/created and linked to the project",
            "Hourly rate may need to be set on the project or employee level",
        ],
    },
    "task_17": {
        "description": "Create custom accounting dimension with values and post voucher",
        "golden_path": [
            "POST /ledger/accountingDimensionName (create the dimension)",
            "POST /ledger/accountingDimensionValue (create each value, linked to dimension)",
            "POST /ledger/voucher (with postings referencing the dimension value)",
        ],
        "key_lessons": [
            "Create dimension name first, then values referencing that dimension",
            "Voucher postings need the dimension value reference",
            "Always set both amountGross AND amountGrossCurrency on postings",
        ],
    },
    "task_18": {
        "description": "Reverse a bank payment that was returned",
        "golden_path": [
            "GET /customer?organizationNumber=X",
            "GET /invoice?customerId=X&invoiceDateFrom=2020-01-01&invoiceDateTo=2030-12-31",
            "Find the payment on the invoice",
            "Reverse/delete the payment so the invoice becomes outstanding again",
        ],
        "key_lessons": [
            "The goal is to make the invoice outstanding again after payment was returned by bank",
            "Look for payment reversal or deletion endpoints via search_api_spec",
        ],
    },
}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

# Regex patterns for log parsing
_RE_PROMPT = re.compile(r"\[PROMPT\]")
_RE_API = re.compile(r"\[API\]\s+(\w+)\s+(.+?)\s+->\s+(\d+)")
_RE_DONE = re.compile(r"\[DONE\]\s+duration=([0-9.]+)s\s+api_calls=(\d+)\s+api_errors=(\d+)")
_RE_VALIDATION_MSG = re.compile(r'"message"\s*:\s*"([^"]+)"')
_RE_SEPARATOR = re.compile(r"^-{10,}$")


class RunHistoryService:
    """Loads previous run logs and provides task-specific playbooks."""

    def __init__(self):
        self._runs: list[ParsedRun] = []
        self._playbooks: dict[str, TaskPlaybook] = {}

    def load(self, log_dirs: list[str] | None = None):
        """Parse run logs and build playbooks. Called once at startup."""
        if log_dirs is None:
            log_dirs = ["example_runs/tripletex-agent"]

        # 1. Find and parse all run logs from revision 15+
        run_files = self._discover_run_files(log_dirs)
        for filepath in run_files:
            parsed = self._parse_run_log(filepath)
            if parsed:
                self._runs.append(parsed)

        logger.info(f"Parsed {len(self._runs)} run logs")

        # 2. Build playbooks from curated + log-derived data
        self._build_playbooks()

        logger.info(f"Built {len(self._playbooks)} task playbooks")

    def classify_prompt(self, prompt: str) -> tuple[str | None, float]:
        """Classify prompt to task type using keyword scoring."""
        prompt_lower = prompt.lower()
        scores: dict[str, float] = {}

        for task_type, keyword_rules in TASK_KEYWORDS.items():
            score = 0.0
            for keywords, weight in keyword_rules:
                if any(kw in prompt_lower for kw in keywords):
                    score += weight
            if score > 0:
                scores[task_type] = score

        if not scores:
            return None, 0.0

        best = max(scores, key=scores.get)
        max_possible = sum(w for _, w in TASK_KEYWORDS[best])
        confidence = scores[best] / max_possible

        # Check ambiguity: if top two are too close, lower confidence
        sorted_scores = sorted(scores.values(), reverse=True)
        if len(sorted_scores) >= 2:
            gap = sorted_scores[0] - sorted_scores[1]
            if gap < 1.0:
                confidence *= 0.7  # penalize ambiguous matches

        return best, confidence

    def get_lessons(self, prompt: str) -> str | None:
        """Main entry: classify prompt → lookup playbook → format for injection.

        Returns formatted lesson text or None if no confident match.
        """
        task_type, confidence = self.classify_prompt(prompt)
        if not task_type or confidence < 0.3:
            logger.debug(f"No confident task match (best={task_type}, conf={confidence:.2f})")
            return None

        playbook = self._playbooks.get(task_type)
        if not playbook:
            return None

        logger.info(f"Classified as {task_type} (confidence={confidence:.2f}), injecting playbook")
        return self._format_playbook(playbook)

    # -----------------------------------------------------------------------
    # Log discovery and parsing
    # -----------------------------------------------------------------------

    def _discover_run_files(self, log_dirs: list[str]) -> list[str]:
        """Find all *_run.txt files from revision 15+."""
        files = []
        for base_dir in log_dirs:
            if not os.path.exists(base_dir):
                logger.warning(f"Log directory not found: {base_dir}")
                continue
            for root, dirs, filenames in os.walk(base_dir):
                # Only include revisions 15+
                path_parts = root.split(os.sep)
                rev_part = [p for p in path_parts if p.startswith("tripletex-agent-000")]
                if rev_part:
                    rev_num = rev_part[0].split("-")[2]  # "00015" from "tripletex-agent-00015-dtp"
                    try:
                        if int(rev_num) < 15:
                            continue
                    except ValueError:
                        continue

                for f in filenames:
                    if f.endswith("_run.txt"):
                        files.append(os.path.join(root, f))

        logger.info(f"Discovered {len(files)} run log files")
        return sorted(files)

    def _parse_run_log(self, filepath: str) -> ParsedRun | None:
        """Parse a single run log file into a ParsedRun."""
        try:
            # Extract task_type from path (e.g., .../task_11/...)
            parts = Path(filepath).parts
            task_type = None
            revision = ""
            for p in parts:
                if p.startswith("task_"):
                    task_type = p
                if p.startswith("tripletex-agent-000"):
                    revision = p.split("-")[2]  # "00017"

            if not task_type or task_type == "unclassified":
                return None

            with open(filepath, encoding="utf-8", errors="replace") as f:
                content = f.read()

            # Extract prompt
            prompt = ""
            lines = content.split("\n")
            in_prompt = False
            for line in lines:
                if "[PROMPT]" in line:
                    in_prompt = True
                    continue
                if in_prompt:
                    if _RE_SEPARATOR.match(line.strip()):
                        break
                    prompt += line.strip() + " "

            prompt = prompt.strip()

            # Extract API calls
            api_sequence = []
            for match in _RE_API.finditer(content):
                method, path, status = match.groups()
                api_sequence.append((method, path.strip(), int(status)))

            # Extract DONE metrics
            total_calls = 0
            total_errors = 0
            duration_s = 0.0
            done_match = _RE_DONE.search(content)
            if done_match:
                duration_s = float(done_match.group(1))
                total_calls = int(done_match.group(2))
                total_errors = int(done_match.group(3))

            # Extract error messages from validation failures
            error_messages = []
            for line in lines:
                if "422" in line and "validationMessages" in line:
                    for msg_match in _RE_VALIDATION_MSG.finditer(line):
                        error_messages.append(msg_match.group(1))

            return ParsedRun(
                task_type=task_type,
                revision=revision,
                filepath=filepath,
                prompt=prompt,
                api_sequence=api_sequence,
                total_calls=total_calls,
                total_errors=total_errors,
                duration_s=duration_s,
                error_messages=error_messages,
            )

        except Exception as e:
            logger.warning(f"Failed to parse {filepath}: {e}")
            return None

    # -----------------------------------------------------------------------
    # Playbook building
    # -----------------------------------------------------------------------

    def _build_playbooks(self):
        """Build playbooks from curated data + run log analysis."""
        # Group runs by task type
        runs_by_task: dict[str, list[ParsedRun]] = {}
        for run in self._runs:
            runs_by_task.setdefault(run.task_type, []).append(run)

        # For each known task type, build a playbook
        for task_type in TASK_KEYWORDS:
            curated = CURATED_PLAYBOOKS.get(task_type)
            runs = runs_by_task.get(task_type, [])

            if curated:
                # Use curated playbook as base
                playbook = TaskPlaybook(
                    task_type=task_type,
                    description=curated["description"],
                    golden_path=curated["golden_path"],
                    key_lessons=curated["key_lessons"],
                )

                # Supplement with common errors from logs
                errors = set()
                for run in runs:
                    for msg in run.error_messages:
                        errors.add(msg)
                playbook.common_errors = list(errors)[:5]

            elif runs:
                # No curated playbook — derive from best run
                clean_runs = [r for r in runs if r.total_errors == 0]
                if clean_runs:
                    best = min(clean_runs, key=lambda r: r.total_calls)
                else:
                    best = min(runs, key=lambda r: r.total_errors)

                golden_path = []
                seen = set()
                for method, path, status in best.api_sequence:
                    if status < 400:
                        key = f"{method} {path}"
                        if key not in seen:
                            golden_path.append(key)
                            seen.add(key)

                errors = set()
                for run in runs:
                    for msg in run.error_messages:
                        errors.add(msg)

                playbook = TaskPlaybook(
                    task_type=task_type,
                    description=f"Task {task_type}",
                    golden_path=golden_path,
                    key_lessons=[],
                    common_errors=list(errors)[:5],
                )
            else:
                continue  # No data at all

            self._playbooks[task_type] = playbook

    # -----------------------------------------------------------------------
    # Formatting
    # -----------------------------------------------------------------------

    def _format_playbook(self, playbook: TaskPlaybook) -> str:
        """Format a playbook as injection text for the user message."""
        lines = [
            f"=== CONTEXT FROM PREVIOUS RUNS ({playbook.description}) ===",
            "",
            "RECOMMENDED API FLOW:",
        ]
        for i, step in enumerate(playbook.golden_path, 1):
            lines.append(f"{i}. {step}")

        if playbook.key_lessons:
            lines.append("")
            lines.append("KEY PITFALLS:")
            for lesson in playbook.key_lessons:
                lines.append(f"- {lesson}")

        if playbook.common_errors:
            lines.append("")
            lines.append("COMMON ERRORS FROM PAST RUNS:")
            for err in playbook.common_errors[:3]:
                lines.append(f"- {err}")

        lines.append("")
        lines.append("Adapt the flow to the specific prompt below.")
        lines.append("===")
        return "\n".join(lines)
