"""Dynamic lessons from previous runs.

Parses run logs, classifies incoming prompts to task types, and provides
task-specific playbooks (optimal API flow + pitfalls) for injection into
the agent's user message.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _strip_accents(text: str) -> str:
    """Remove diacritical marks (accents) for fuzzy keyword matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


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
        ({"payroll", "lønn", "lønnsslipp", "gehaltsabrechnung", "paie", "nómina", "folha de pagamento", "busta paga"}, 3.0),
        ({"salary", "salaire", "salario", "salário", "grunnlønn", "grundgehalt", "salaire de base", "salario base", "salário base", "stipendio base", "gehalt"}, 2.5),
        ({"bonus", "engangsbonus", "prime unique", "prima", "bónus", "bonificación", "bonificação"}, 2.5),
        ({"processe", "processar", "process payroll", "ejecute", "exécutez", "run payroll", "kjør lønn"}, 1.5),
    ],
    "task_13": [  # Travel expense
        ({"travel expense", "reiseregning", "reise", "note de frais", "nota de gastos",
          "nota de despesas", "reisekostenabrechnung", "despesa de viagem", "gastos de viaje"}, 3.0),
        ({"per diem", "diett", "dieta", "indemnités", "dietas", "ajudas de custo",
          "indemnités journalières", "taux journalier", "taxa diária", "tarifa diaria"}, 2.0),
        ({"flight", "flybillett", "billet d'avion", "billete de avión", "bilhete de avião", "flug"}, 1.5),
        ({"taxi"}, 1.0),
        ({"days", "dager", "dagar", "jours", "días", "dias", "tage"}, 0.5),
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
          "registrar horas", "logg timer", "erfassen sie", "erfassen"}, 3.0),
        ({"hourly rate", "timesats", "stundensatz", "taux horaire", "tarifa por hora"}, 2.0),
        ({"activity", "aktivitet", "aktivität", "activité", "actividad", "atividade"}, 1.5),
        ({"stunden", "timer", "hours", "heures", "horas", "ore"}, 1.0),
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
    "task_20": [  # Supplier invoice from PDF (leverandørfaktura) — also covers task_21
        ({"leverandørfaktura", "leverandorfaktura", "supplier invoice", "facture fournisseur",
          "factura del proveedor", "fatura do fornecedor", "lieferantenrechnung", "fattura fornitore"}, 4.0),
        ({"vedlagt", "attached", "ci-joint", "adjunto", "anexo", "beigefügt", "allegato"}, 2.0),
        ({"registrer fakturaen", "register the invoice", "enregistrer la facture",
          "registrar la factura", "registrar a fatura", "rechnung erfassen"}, 2.0),
        ({"utgiftskonto", "expense account", "compte de charges", "cuenta de gastos", "aufwandskonto",
          "inngående mva", "input vat", "tva déductible", "iva deducible", "vorsteuer",
          "inngaaande mva"}, 1.5),
    ],
    # NOTE: task_21 keywords REMOVED — identical to task_20, causing ambiguity penalty.
    # task_21 prompts now classify as task_20, which has the same playbook (task_21 is aliased).
    "task_23": [  # Bank reconciliation from CSV
        ({"bankutskrift", "bank statement", "relevé bancaire", "extracto bancario",
          "extrato bancário", "kontoauszug", "estratto conto"}, 4.0),
        ({"avstemming", "avstem", "reconciliation", "rapprochement", "conciliación",
          "conciliação", "abstimmung", "riconciliazione"}, 3.0),
        ({"csv", ".csv"}, 2.0),
        ({"innbetaling", "payment", "paiement", "pago", "pagamento", "zahlung"}, 1.0),
    ],
    "task_28": [  # Analyze expense increase + create internal projects
        ({"aumento", "augmenté", "aumentaram", "increase", "incremento", "økt", "gestiegen"}, 3.0),
        ({"gastos", "charges", "despesa", "expense", "kostnader", "kosten"}, 2.0),
        ({"proyecto", "projet", "projeto", "project", "prosjekt", "projekt"}, 2.0),
        ({"actividad", "activité", "atividade", "activity", "aktivitet", "aktivität"}, 1.5),
        ({"libro mayor", "grand livre", "livro razão", "general ledger", "hovedbok", "hauptbuch"}, 1.5),
    ],
    "task_22": [  # Expense posting from receipt/PDF to department
        ({"kvittering", "receipt", "reçu", "recibo", "quittung", "ricevuta"}, 4.0),
        ({"bokfør", "bokført", "post", "enregistrer", "registrar", "buchen"}, 2.0),
        ({"utgiftskonto", "expense account", "compte de charges", "cuenta de gastos", "aufwandskonto"}, 2.0),
        ({"avdeling", "department", "département", "departamento", "abteilung"}, 1.5),
        ({"mva-behandling", "vat treatment", "traitement tva", "tratamiento iva", "mwst-behandlung"}, 1.5),
    ],
    "task_19": [  # Employee from PDF (employment contract)
        ({"arbeidskontrakt", "employment contract", "contrat de travail", "contrato de trabajo", "contrato de trabalho", "arbeitsvertrag"}, 4.0),
        ({"vedlagt pdf", "attached pdf", "pdf ci-joint", "pdf adjunto", "pdf anexo", "beigefügte pdf"}, 3.0),
        ({"personnummer", "national identity", "numéro d'identité", "número de identidad", "número de identidade", "personalnummer"}, 2.0),
        ({"stillingskode", "occupation code", "code profession", "código de ocupación"}, 1.5),
    ],
    "task_24": [  # Ledger error correction (find & fix 4 errors)
        ({"feil", "errors", "erreurs", "errores", "erros", "fehler"}, 3.0),
        ({"korriger", "correct", "corrigez", "corrija", "corrija", "korrigieren"}, 3.0),
        ({"bilag", "voucher", "écriture", "asiento", "voucher", "beleg"}, 2.0),
        ({"feil konto", "wrong account", "mauvais compte", "cuenta incorrecta", "falsche konto"}, 2.0),
        ({"duplisert", "duplicate", "dupliqué", "duplicado", "dupliziert"}, 1.5),
        ({"manglende mva", "missing vat", "tva manquant", "iva faltante", "fehlende mwst"}, 1.5),
    ],
    "task_25": [  # Overdue invoice + reminder fee + partial payment
        ({"forfalt", "overdue", "überfällig", "vencida", "vencido", "échue"}, 3.0),
        ({"mahngebühr", "mahng", "reminder fee", "purregebyr", "frais de rappel", "cargo por mora"}, 3.0),
        ({"teilzahlung", "partial payment", "delbetaling", "paiement partiel", "pago parcial"}, 2.0),
        ({"1500", "3400"}, 1.5),  # Account numbers for receivables/fees
    ],
    "task_26": [  # Month-end closing OR currency exchange gain/loss (agio/disagio)
        ({"agio", "disagio"}, 4.0),
        ({"valutadifferanse", "currency difference", "différence de change", "diferencia de cambio", "währungsdifferenz"}, 3.0),
        ({"månedsavslutning", "month-end", "clôture mensuelle", "cierre mensual", "monatsabschluss",
          "chiusura mensile", "fechamento mensal"}, 4.0),
        ({"periodisering", "periodization", "périodisation", "periodización", "periodisierung",
          "forskuddsbetalt", "prepaid", "constatées d'avance", "gastos anticipados"}, 3.0),
        ({"kurs", "exchange rate", "taux de change", "tipo de cambio", "wechselkurs"}, 2.0),
        ({"eur", "usd", "gbp", "sek", "dkk"}, 1.5),
        ({"8060", "8160"}, 1.5),  # Agio/disagio accounts
    ],
    "task_29": [  # Full project lifecycle (budget + hours + supplier costs + customer invoice)
        # "ciclo de vida" matches Spanish, "projektzyklus" German, "prosjektsyklus" Norwegian
        ({"projektzyklus", "project lifecycle", "project cycle", "ciclo de vida",
          "ciclo do projeto", "cycle de vie", "prosjektsyklus", "ciclo di vita",
          "ciclo de vida completo"}, 5.0),
        # "vollstandigen" matches German "vollständigen", "completo" matches Spanish/Portuguese/Italian
        ({"vollstandig", "full project", "complete project", "completo del proyecto",
          "cycle complet", "fullstendig prosjekt", "vida completo"}, 3.0),
        # "registre horas" matches Spanish imperative, "erfassen sie stunden" German
        ({"stunden erfassen", "erfassen sie stunden", "log hours", "registrer timer",
          "enregistrer heures", "registrar horas", "registre horas", "registrare ore",
          "registre as horas"}, 3.0),
        # "costo de proveedor" matches Spanish, "leverandorkost" Norwegian
        ({"lieferantenkosten", "supplier cost", "costo de proveedor", "costes proveedor",
          "custo fornecedor", "custos fornecedor", "couts fournisseur", "cout fournisseur",
          "leverandorkost", "costo fornitore"}, 3.0),
        # "factura al cliente" matches Spanish, "kundefaktura" Norwegian
        ({"kundenrechnung", "customer invoice", "factura al cliente", "factura cliente",
          "fatura ao cliente", "fatura cliente", "facture au client", "facture client",
          "kundefaktura", "fattura al cliente"}, 2.5),
        ({"budget", "presupuesto", "orcamento", "orçamento", "bilancio"}, 2.0),
    ],
    "task_30": [  # Year-end closing with depreciation + tax provision (multiple assets)
        ({"årlig", "annuel", "anual", "annual", "jährlich", "årsavslutning", "jahresabschluss", "year-end", "clôture annuelle", "cierre anual"}, 3.0),
        ({"avskrivning", "depreciation", "amortissement", "depreciación", "depreciação", "abschreibung"}, 4.0),
        ({"immobilisations", "anlagen", "activos fijos", "ativos fixos", "fixed assets", "anleggsmidler"}, 3.0),
        ({"programvare", "kontormaskiner", "kjøretøy", "it-utstyr", "inventar"}, 3.0),  # Norwegian asset names commonly in prompts
        ({"skatteberegning", "tax provision", "provision d'impôt", "provisión fiscal", "steuerrückstellung"}, 2.5),
        ({"forskuddsbetalt", "prepaid", "constatées d'avance", "gastos anticipados", "vorausbezahlt"}, 2.0),
        ({"6010", "1209"}, 1.5),  # Depreciation expense / accumulated depreciation accounts
        ({"8700", "2920"}, 1.5),  # Tax expense / tax payable accounts
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
            "Do NOT make a verification GET call after creating the employee — the 201 response confirms success. 3 calls is optimal. Every extra call hurts your efficiency score.",
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
        "description": "Create multiple departments",
        "golden_path": [
            "POST /department/list (with array of department objects) — creates ALL departments in ONE call",
        ],
        "key_lessons": [
            "ALWAYS use POST /department/list for batch creation — VERIFIED working.",
            "CRITICAL: The request body for POST /department/list is a RAW JSON ARRAY, NOT wrapped in an object. Correct: [{\"name\": \"Dept1\"}, {\"name\": \"Dept2\"}, {\"name\": \"Dept3\"}]. WRONG: {\"values\": [...]} — this causes 422 'Invalid json'.",
            "Department only requires 'name' field. departmentNumber is optional.",
            "1 API call is optimal for this task.",
        ],
    },
    "task_06": {
        "description": "Create and send invoice to customer",
        "golden_path": [
            "GET /customer?organizationNumber=X (find existing customer)",
            "GET /ledger/account?number=1920&fields=id,number,version,bankAccountNumber (check bank account)",
            "IF bankAccountNumber is empty: PUT /ledger/account/{id} with {id, version, bankAccountNumber: '86011117947'} — MUST do this BEFORE invoicing",
            "POST /order (with customer, deliveryDate, orderDate, orderLines with description/unitPriceExcludingVatCurrency/count/vatType)",
            "PUT /order/{id}/:invoice?invoiceDate=YYYY-MM-DD&sendToCustomer=true (creates invoice and sends in one step)",
        ],
        "key_lessons": [
            "CRITICAL: Check bank account 1920 FIRST and set bankAccountNumber if empty. Without it, invoice creation returns 422 'selskapet har ikke registrert et bankkontonummer'. PROACTIVELY set it up before trying to create an invoice.",
            "If customer has empty invoiceEmail, set it via PUT /customer/{id} before sending invoice",
            "Orders require deliveryDate — use today's date",
            "'Excluding VAT' means the stated price is without VAT, but 25% VAT still applies (vatType id=3)",
            "orderLines don't need a product — you can use description + unitPriceExcludingVatCurrency + count + vatType directly",
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
            "GET /customer?organizationNumber=X (find customer)",
            "GET /ledger/account?number=1920&fields=id,number,version,bankAccountNumber (check bank account — run in PARALLEL with customer+product lookups)",
            "IF bankAccountNumber empty: PUT /ledger/account/{id} with bankAccountNumber='86011117947' — MUST do before invoicing",
            "GET /product?productNumber=NUM1&productNumber=NUM2&productNumber=NUM3 (find ALL products in ONE call — use REPEATED productNumber params)",
            "POST /order (with customer={id}, deliveryDate=today, orderDate=today, orderLines: 3 lines with product={id}, unitPriceExcludingVatCurrency, count=1, vatType={id} per line)",
            "PUT /order/{id}/:invoice?invoiceDate=today (creates invoice from order)",
        ],
        "key_lessons": [
            "CRITICAL: ALL invoice creation (both PUT /order/:invoice AND POST /invoice) requires bank account 1920 to have a bankAccountNumber. ALWAYS check and set up bank account BEFORE creating an invoice. Without it: 422 'selskapet har ikke registrert et bankkontonummer'.",
            "VAT type IDs: id=3 for 25% (Utgående avgift, høy sats), id=31 for 15% (Utgående avgift, middels sats/næringsmiddel/food), id=6 for 0% exempt (Ingen utgående avgift, utenfor mva-loven/avgiftsfri). Do NOT use id=5 for exempt — use id=6.",
            "BATCH product lookup: GET /product?productNumber=NUM1&productNumber=NUM2&productNumber=NUM3 fetches all 3 products in ONE call. Do NOT use comma-separated syntax (productNumber=1,2,3) — that returns 0 results. Do NOT make 3 separate GET /product calls.",
            "Products already exist — search by productNumber. Do NOT create new products.",
            "Do NOT verify orderlines after invoice creation — the response confirms success.",
            "The prompt does NOT ask to send the invoice — just create it. Do NOT set sendToCustomer=true.",
        ],
    },
    "task_10": {
        "description": "Create order, convert to invoice, register full payment — minimal API calls",
        "golden_path": [
            "GET /customer?organizationNumber=X (find customer — 1 call)",
            "GET /product?productNumber=NUM1&productNumber=NUM2 (find BOTH products in ONE call — use productNumber param for each)",
            "GET /invoice/paymentType (find payment type ID — look for 'Betalt til bank')",
            "POST /invoice?sendToCustomer=false&paymentTypeId=X&paidAmount=TOTAL_INCL_VAT (create order+invoice+payment in ONE call — see body below)",
        ],
        "key_lessons": [
            "MOST EFFICIENT: POST /invoice can create orders and orderLines INLINE. The docs say 'Related Order and OrderLines can be created first, or included as new objects inside the Invoice.' Use this to skip the separate POST /order + PUT /order/:invoice steps.",
            "POST /invoice body with inline order: {invoiceDate: 'YYYY-MM-DD', invoiceDueDate: '14 days later', orders: [{customer: {id: CUST_ID}, orderDate: 'YYYY-MM-DD', deliveryDate: 'YYYY-MM-DD', orderLines: [{product: {id: PROD1_ID}, count: 1, unitPriceExcludingVatCurrency: PRICE1, vatType: {id: 3}}, {product: {id: PROD2_ID}, count: 1, unitPriceExcludingVatCurrency: PRICE2, vatType: {id: 3}}]}]}",
            "POST /invoice accepts paymentTypeId and paidAmount as QUERY PARAMS (not body). This registers payment at invoice creation time.",
            "FALLBACK if POST /invoice with inline order fails: use POST /order then PUT /order/{id}/:invoice?paymentTypeId=X&paidAmount=Y (the two-step approach).",
            "GET /product supports fetching MULTIPLE products in one call: ?productNumber=NUM1&productNumber=NUM2. This saves an API call vs searching one at a time.",
            "paidAmount = total INCLUDING VAT. Calculate: sum of all line prices × 1.25 for 25% VAT.",
            "Do NOT make verification GET calls after creation — the 201 response confirms success.",
        ],
    },
    "task_11": {
        "description": "Register supplier invoice (WITHOUT PDF — details given in text)",
        "golden_path": [
            "GET /supplier?organizationNumber=X (find supplier — already exists for task_11)",
            "GET /ledger/account?number=XXXX,2400 (batch get expense + supplier debt accounts)",
            "GET /ledger/voucherType?name=Leverandørfaktura (get voucherType ID — REQUIRED)",
            "POST /ledger/voucher?sendToLedger=false — create voucher with voucherType=Leverandørfaktura, BOTH debit+credit postings, vendorInvoiceNumber=INV-XXXX",
            "PUT /supplierInvoice/voucher/{VOUCHER_ID}/postings — THIS IS THE CRITICAL STEP that creates a proper SupplierInvoice record. Body: [{posting: {account: {id: EXPENSE_ACCT_ID}, amountGross: GROSS, amountGrossCurrency: GROSS, vatType: {id: 1}}}]. Without this step, the scorer finds NO supplierInvoice and scores 0.",
        ],
        "key_lessons": [
            "CRITICAL: POST /incomingInvoice ALWAYS returns 403 on the competition proxy. Do NOT try it — it wastes an API call and adds an error. Skip directly to the voucher approach.",
            "CRITICAL: Creating a voucher alone (POST /ledger/voucher) does NOT create a SupplierInvoice record. The scorer checks GET /supplierInvoice and finds nothing → score 0.00. You MUST call PUT /supplierInvoice/voucher/{id}/postings after creating the voucher to register it as a proper SupplierInvoice.",
            "Step 1: POST /ledger/voucher?sendToLedger=false with BOTH postings (the voucher MUST balance — single-posting vouchers fail with 422 'sum not 0'). Body: {date: 'YYYY-MM-DD', description: 'Supplier name - services', vendorInvoiceNumber: 'INV-XXXX-NNNN', voucherType: {id: VOUCHER_TYPE_ID}, postings: [{row: 1, account: {id: EXPENSE_ACCT_ID}, amountGross: GROSS_AMOUNT, amountGrossCurrency: GROSS_AMOUNT, vatType: {id: 1}, description: 'description'}, {row: 2, account: {id: ACCT_2400_ID}, amountGross: -GROSS_AMOUNT, amountGrossCurrency: -GROSS_AMOUNT, supplier: {id: SUPPLIER_ID}, invoiceNumber: 'INV-XXXX-NNNN', termOfPayment: 'DUE_DATE_YYYY-MM-DD', description: 'Supplier name'}]}",
            "Step 2: PUT /supplierInvoice/voucher/{VOUCHER_ID}/postings — body is a JSON ARRAY (not dict): [{\"posting\": {\"account\": {\"id\": EXPENSE_ACCT_ID}, \"amountGross\": GROSS_AMOUNT, \"amountGrossCurrency\": GROSS_AMOUNT, \"vatType\": {\"id\": 1}}}]. This converts the voucher into a SupplierInvoice. If it returns 403, fall back: PUT /ledger/voucher/{VOUCHER_ID}?sendToLedger=true (with the voucher body) to at least book the voucher to ledger.",
            "IMPORTANT: The json_body for PUT /supplierInvoice/voucher/{id}/postings is a JSON ARRAY (list), not a dict. Pass it as: [{\"posting\": {\"account\": {\"id\": X}, \"amountGross\": Y, \"amountGrossCurrency\": Y, \"vatType\": {\"id\": 1}}}]",
            "voucherType MUST be set to Leverandørfaktura (get ID from GET /ledger/voucherType?name=Leverandørfaktura). Without it the voucher is not recognized as a supplier invoice.",
            "Gross amount is TTC (VAT included). Net = gross / 1.25 for 25% VAT. vatTypeId=1 is '25% ingoing VAT deduction'.",
            "vendorInvoiceNumber on the voucher carries the invoice reference (INV-XXXX-NNNN).",
            "MUST set both amountGross AND amountGrossCurrency (same value for NOK).",
            "Set invoiceNumber and termOfPayment on the credit posting (account 2400) — invoiceNumber='INV-XXXX-NNNN', termOfPayment='YYYY-MM-DD' (due date, typically 30 days from invoice date).",
            "Due date: if not specified in the prompt, use invoice date + 30 days.",
        ],
    },
    "task_12": {
        "description": "Run payroll for employee with base salary and bonus",
        "golden_path": [
            "STEP 1 (parallel) — GET /employee?email=X&fields=id,firstName,lastName,dateOfBirth AND GET /salary/type?isInactive=false&fields=id,name (WARNING: field 'code' does NOT exist — only id,name) AND GET /employee/employment?employeeId=X&fields=id,startDate,division",
            "STEP 2 (MANDATORY — dateOfBirth is ALWAYS null) — PUT /employee/{id} with {id:ID, version:VERSION, firstName:FIRST, lastName:LAST, dateOfBirth:'1990-01-15'} — you already have version from step 1",
            "STEP 3 (if no employment) — POST /employee/employment with ONLY {employee:{id:EMP_ID}, startDate:'2026-01-01', taxDeductionCode:'loennFraHovedarbeidsgiver', isMainEmployer:true} — do NOT nest employmentDetails here",
            "STEP 4 — POST /employee/employment/details with {employment:{id:EMPLOYMENT_ID}, date:'2026-01-01', employmentType:'ORDINARY', remunerationType:'MONTHLY_WAGE', workingHoursScheme:'NOT_SHIFT', percentageOfFullTimeEquivalent:100.0, annualSalary:BASE_SALARY*12, monthlySalary:BASE_SALARY}",
            "STEP 5 (parallel, if no division) — GET /municipality?count=1&fields=id,name AND GET /token/session/>whoAmI (NO fields param!) then GET /company/{companyId}?fields=id,name,organizationNumber",
            "STEP 6 — POST /division with {name:'Hovedavdeling', organizationNumber:'DIFFERENT_9_DIGITS', startDate:'2026-01-01', municipalityDate:'2026-01-01', municipality:{id:MUNI_ID}} — org number: take company's, change first digit (e.g. 668→968)",
            "STEP 7 — PUT /employee/employment/{id} with {id:EMPL_ID, version:VER, employee:{id:EMP_ID}, startDate:'2026-01-01', taxDeductionCode:'loennFraHovedarbeidsgiver', isMainEmployer:true, division:{id:DIV_ID}}",
            "STEP 8 — POST /salary/transaction?generateTaxDeduction=true — EXACT body: {date:'YYYY-MM-DD', year:YYYY, month:M, payslips:[{employee:{id:EMP_ID}, date:'YYYY-MM-DD', year:YYYY, month:M, specifications:[{salaryType:{id:FASTLONN_ID}, rate:BASE_AMT, count:1, amount:BASE_AMT}, {salaryType:{id:BONUS_ID}, rate:BONUS_AMT, count:1, amount:BONUS_AMT}]}]}",
        ],
        "key_lessons": [
            "FOLLOW STEPS IN ORDER. Do NOT skip ahead. dateOfBirth (step 2) BEFORE employment (step 3). Division (step 6) BEFORE linking (step 7). Linked employment (step 7) BEFORE salary transaction (step 8).",
            "CRITICAL — Division organizationNumber MUST be DIFFERENT from the company's org number. Using the company's gives 422. Solution: take company org number and change first digit (e.g. 668491863 → 968491863).",
            "CRITICAL — GET /salary/type: use fields=id,name ONLY. 'code' does NOT exist on SalaryTypeDTO. Find name='Fastlønn' for base salary and name='Bonus' for bonus.",
            "CRITICAL — GET /token/session/>whoAmI: do NOT pass fields=id,company. Use NO fields param at all. Response has companyId at top level.",
            "CRITICAL — Employee dateOfBirth is ALWAYS null. You MUST set it via PUT /employee BEFORE creating employment.",
            "CRITICAL — POST /employee/employment/details: SEPARATE call. String enum values: employmentType='ORDINARY', remunerationType='MONTHLY_WAGE', workingHoursScheme='NOT_SHIFT'. Include annualSalary and monthlySalary.",
            "Division POST requires ALL of: name, organizationNumber, startDate, municipalityDate, municipality:{id}. Missing any → 422.",
            "ALWAYS include amount=rate*count on each salary specification. Salary type IDs vary per sandbox — discover via GET /salary/type.",
        ],
    },
    "task_13": {
        "description": "Register travel expense with per diem and costs, then deliver it",
        "golden_path": [
            "GET /employee?email=X&fields=id,firstName,lastName,email (find the employee)",
            "GET /travelExpense/rateCategory?type=PER_DIEM&dateFrom=YYYY-MM-01&dateTo=YYYY-MM-28&name=Overnatting&fields=id,name,type (find overnight rate category — MUST filter by dateFrom/dateTo)",
            "GET /travelExpense/rate?rateCategoryId=X&fields=id,rate (find rateType ID — ONLY use fields id,rate — 'name' and 'type' do NOT exist on TravelExpenseRateDTO)",
            "GET /travelExpense/costCategory?isInactive=false&fields=id,description (find IDs — 'name' does NOT exist, use 'description'. Look for 'Fly' for flight, 'Taxi' for taxi)",
            "GET /travelExpense/paymentType?isInactive=false&fields=id,description (find payment type — 'name' does NOT exist, use 'description')",
            "POST /travelExpense with nested perDiemCompensations and costs (see exact body below)",
            "PUT /travelExpense/:deliver?id=TRAVEL_EXPENSE_ID (CRITICAL — deliver/submit the expense! Without this step the expense stays in draft and the task is incomplete)",
        ],
        "key_lessons": [
            "EXACT POST /travelExpense body — follow this structure precisely:\n"
            "{\n"
            "  employee: {id: EMPLOYEE_ID},\n"
            "  title: 'EXACT title from prompt in quotes',\n"
            "  travelDetails: {\n"
            "    departureDate: 'YYYY-MM-DD', returnDate: 'YYYY-MM-DD',\n"
            "    departureTime: '08:00', returnTime: '18:00',\n"
            "    departureFrom: 'Oslo', destination: 'DESTINATION_CITY',\n"
            "    purpose: 'SAME as title from the prompt',\n"
            "    isForeignTravel: false, isDayTrip: false\n"
            "  },\n"
            "  perDiemCompensations: [{\n"
            "    rateCategory: {id: RATE_CATEGORY_ID},\n"
            "    rateType: {id: RATE_TYPE_ID},\n"
            "    overnightAccommodation: 'HOTEL',\n"
            "    location: 'DESTINATION_CITY',\n"
            "    count: NUMBER_OF_DAYS,\n"
            "    rate: DAILY_RATE_FROM_PROMPT\n"
            "  }],\n"
            "  costs: [\n"
            "    {costCategory: {id: FLIGHT_CAT_ID}, paymentType: {id: PAY_TYPE_ID}, amountCurrencyIncVat: FLIGHT_AMOUNT, date: 'DEPARTURE_DATE', comments: 'Flybillett'},\n"
            "    {costCategory: {id: TAXI_CAT_ID}, paymentType: {id: PAY_TYPE_ID}, amountCurrencyIncVat: TAXI_AMOUNT, date: 'RETURN_DATE', comments: 'Taxi'}\n"
            "  ]\n"
            "}",
            "AFTER creating the travel expense: MUST call PUT /travelExpense/:deliver?id=EXPENSE_ID to deliver/submit it. Without delivery the expense stays in draft status and is scored as incomplete.",
            "FIELD NAME RULES: 'title' goes on the TravelExpense root (NOT in travelDetails). 'purpose' goes inside travelDetails and should match the title. departureDate/returnDate go inside travelDetails (NOT on root).",
            "ALWAYS set 'rate' on per diem to the daily rate specified in the prompt (e.g. 800). This overrides the system default rate.",
            "Cost fields: use 'costCategory' (NOT 'category'), 'comments' (NOT 'description'), 'amountCurrencyIncVat' (NOT 'amount'). Cost does NOT have 'count' field — 'count' causes 422.",
            "Per diem: use 'count' for number of days (NOT 'countDays'). MUST include 'rateType': {id: RATE_ID} from GET /travelExpense/rate. Do NOT include 'countryCode' — it causes 'Country not enabled for travel expense' error.",
            "CRITICAL: Rate categories are date-dependent. Use dateFrom/dateTo matching the travel dates. Without date filter, you get old expired categories that cause 422 'dato samsvarer ikke med valgt satskategori'.",
            "FIELD NAME WARNING: TravelExpenseRateDTO has 'id' and 'rate' fields but NOT 'name' or 'type'. TravelCostCategoryDTO has 'id' and 'description' but NOT 'name'. Using wrong field names in ?fields= causes 400 errors. Always use fields=id,description for costCategory and fields=id,rate for rate.",
            "overnightAccommodation enum: NONE, HOTEL, BOARDING_HOUSE_WITHOUT_COOKING, BOARDING_HOUSE_WITH_COOKING. Use HOTEL for multi-day trips.",
            "Creating everything nested in one POST avoids 409 RevisionException that occurs when creating costs separately.",
            "Date calculation: For N-day trip, set departureDate = today minus N days, returnDate = today minus 1 day (or yesterday). The trip should be in the past.",
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
        "description": "Set fixed price on project and invoice partial amount (e.g., 50% milestone)",
        "golden_path": [
            "GET /customer?organizationNumber=X (find customer)",
            "GET /employee?email=X (find project manager)",
            "GET /ledger/account?number=1920&fields=id,number,version,bankAccountNumber (check bank account)",
            "IF bankAccountNumber empty: PUT /ledger/account/{id} with bankAccountNumber='86011117947' — MUST do before invoicing",
            "POST /project (with name, customer={id}, projectManager={id}, startDate=today, fixedprice=TOTAL_AMOUNT)",
            "POST /order (with project={id}, customer={id}, orderDate=today, deliveryDate=today, orderLines: [{description: 'Delbetaling X%', count:1, unitPriceExcludingVatCurrency: PARTIAL_AMOUNT, vatType:{id:3}}])",
            "PUT /order/{id}/:invoice?invoiceDate=today (creates the invoice)",
        ],
        "key_lessons": [
            "PROACTIVELY set up bank account 1920 before invoice creation — otherwise 422 'selskapet har ikke registrert et bankkontonummer'.",
            "Calculate partial amount: e.g., 50% of fixed price = fixedPrice * 0.50",
            "Project must be created before the order — the order links to the project",
            "The project's fixedprice field stores the total contract value",
            "Use PUT /order/{id}/:invoice to create invoice — this avoids needing POST /invoice with bank account",
        ],
    },
    "task_16": {
        "description": "Log hours on project activity and generate project invoice",
        "golden_path": [
            "GET /customer?organizationNumber=X (find customer)",
            "GET /employee?email=X (find employee)",
            "GET /ledger/account?number=1920&fields=id,number,version,bankAccountNumber (check bank account)",
            "IF bankAccountNumber empty: PUT /ledger/account/{id} with bankAccountNumber='86011117947'",
            "POST /activity with {name: 'ActivityName', activityType: 'PROJECT_GENERAL_ACTIVITY', isChargeable: true} — MUST include activityType",
            "POST /project (with name, customer={id}, projectManager={id}, startDate=today)",
            "POST /project/projectActivity with {project: {id}, activity: {id}, startDate: today} — links activity to project",
            "POST /timesheet/entry with {employee: {id}, project: {id}, activity: {id}, date: today, hours: X}",
            "POST /order with {customer: {id}, project: {id}, orderDate: today, deliveryDate: today, orderLines: [{description: 'X timer Activity @ RATE', count: HOURS, unitPriceExcludingVatCurrency: RATE, vatType: {id: 3}}]}",
            "PUT /order/{id}/:invoice?invoiceDate=today",
        ],
        "key_lessons": [
            "CRITICAL: Activity creation REQUIRES 'activityType'. Use 'PROJECT_GENERAL_ACTIVITY' for project activities. Without it you get 422 'activityType kan ikke være null'. Enum: GENERAL_ACTIVITY, PROJECT_GENERAL_ACTIVITY, PROJECT_SPECIFIC_ACTIVITY, TASK.",
            "Activity must be linked to project via POST /project/projectActivity BEFORE timesheet entry. Without it: 422 'Aktiviteten kan ikke benyttes'.",
            "PROACTIVELY set up bank account 1920 before invoicing.",
            "Project creation does NOT need projectCategory — omit it entirely if categories don't exist.",
            "Timesheet entry: employee, project, activity, date, hours. VERIFIED: these are the only required fields.",
            "Order deliveryDate is REQUIRED — always include it.",
            "Hourly rate goes on orderLine.unitPriceExcludingVatCurrency, count=HOURS.",
            "EXACT flow (10 calls, 0 errors): GET customer, GET employee, GET+PUT bank, POST activity, POST project, POST projectActivity, POST timesheet, POST order, PUT order/:invoice.",
        ],
    },
    "task_17": {
        "description": "Create custom accounting dimension with values and post voucher",
        "golden_path": [
            "POST /ledger/accountingDimensionName {dimensionName: 'X'} → returns {id, dimensionIndex} (dimensionIndex is 1, 2, or 3)",
            "POST /ledger/accountingDimensionValue {displayName: 'Value1', dimensionIndex: N} → returns {id: VAL1_ID}",
            "POST /ledger/accountingDimensionValue {displayName: 'Value2', dimensionIndex: N} → returns {id: VAL2_ID}",
            "GET /ledger/account?number=XXXX,1920 → returns BOTH accounts in one call (batch lookup)",
            "POST /ledger/voucher?sendToLedger=true with freeAccountingDimension1 on the expense posting (EXACT body below)",
        ],
        "key_lessons": [
            "CRITICAL: The field to link a dimension value to a posting is 'freeAccountingDimension1' — NOT 'accountingDimensionValue1', NOT 'dimension1', NOT 'accountingDimension1'. Only 'freeAccountingDimension1' works. This has been VERIFIED against the live API.",
            "freeAccountingDimension1 takes an object with id: {'id': <dimensionValueId>} — use the VALUE id, not the dimension name id",
            "freeAccountingDimension1 works directly in POST /ledger/voucher — VERIFIED, no separate PUT needed. The POST response may not show it expanded, but it IS set.",
            "POST /ledger/accountingDimensionName: field is 'dimensionName' (NOT 'name')",
            "POST /ledger/accountingDimensionValue: use 'displayName' + 'dimensionIndex' (NOT dimensionName as object ref)",
            "Debit and credit postings MUST use DIFFERENT accounts (expense account + bank account 1920)",
            "Include row: 1, row: 2 on postings (NEVER row 0, that's system-generated). Include both amountGross AND amountGrossCurrency.",
            "Batch account lookup: GET /ledger/account?number=6300,1920 returns both accounts in one call",
            "EXACT voucher body (5 calls total, 0 errors expected): {date: 'YYYY-MM-DD', description: '...', postings: [{date: 'YYYY-MM-DD', description: '...', account: {id: EXPENSE_ACCT_ID}, amountGross: AMOUNT, amountGrossCurrency: AMOUNT, row: 1, freeAccountingDimension1: {id: DIMENSION_VALUE_ID}}, {date: 'YYYY-MM-DD', description: '...', account: {id: BANK_ACCT_ID}, amountGross: -AMOUNT, amountGrossCurrency: -AMOUNT, row: 2}]}",
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
    "task_28": {
        "description": "Analyze expense increase between months + create internal projects with unique activities",
        "golden_path": [
            "GET /token/session/>whoAmI (get employee ID for projectManager — REQUIRED before creating projects)",
            "GET /ledger/postingByDate?dateFrom=2026-01-01&dateTo=2026-02-01&count=5000 (January postings — NO fields param!)",
            "GET /ledger/postingByDate?dateFrom=2026-02-01&dateTo=2026-03-01&count=5000 (February postings)",
            "Use aggregate_postings tool on EACH month's response to get per-account sums (don't calculate manually!)",
            "Compare the aggregated results: for each expense account (5000-7999), compute Feb total - Jan total",
            "GET /ledger/account?id=X,Y,Z (get account names for the top 3 account IDs by increase)",
            "For EACH of the 3 accounts: POST /activity with {name: 'Kostnadsanalyse - ACCOUNT_NAME', activityType: 'PROJECT_GENERAL_ACTIVITY'}",
            "For EACH of the 3 accounts: POST /project with {name: ACCOUNT_NAME, projectManager:{id}, isInternal:true, startDate:today}",
            "For EACH project: POST /project/projectActivity with {project:{id:PROJECT_ID}, activity:{id:NEW_ACTIVITY_ID}} to link the NEW activity",
        ],
        "key_lessons": [
            "CRITICAL: Create a NEW activity for EACH project via POST /activity. Do NOT reuse existing activities. The task says 'create an activity FOR EACH project' — the scoring checks that each project has its own unique activity.",
            "CRITICAL: Use the aggregate_postings tool to sum postings by account — don't manually sum in your reasoning. Pass the raw API response body to the tool.",
            "CRITICAL: GET /ledger/postingByDate does NOT support the fields parameter — always returns 422 if you include it. Omit fields entirely.",
            "CRITICAL: POST /project REQUIRES projectManager field — always do GET /token/session/>whoAmI FIRST to get employee ID.",
            "POST /project also REQUIRES startDate — use today's date.",
            "Create projects SEQUENTIALLY, not in parallel — parallel creation causes 500/409 errors from race conditions.",
            "dateFrom is inclusive, dateTo is EXCLUSIVE (so dateTo=2026-02-01 covers all of January).",
            "Expense accounts are in the 5000-7999 range in Norwegian chart of accounts.",
            "POST /activity body: {name: 'Kostnadsanalyse - AccountName', activityType: 'PROJECT_GENERAL_ACTIVITY'}. The activity is then linked via POST /project/projectActivity.",
            "Do NOT link the default 'Prosjektadministrasjon' activity — that's auto-created and doesn't count for scoring.",
        ],
    },
    "task_22": {
        "description": "Post expense from receipt/PDF to correct account and department with VAT",
        "golden_path": [
            "READ the attached PDF/receipt — extract: item description, amount (incl/excl VAT), VAT amount/rate, date",
            "GET /department?name=X (find the department mentioned in the prompt)",
            "GET /ledger/account?number=XXXX (find the expense account — match item to Norwegian standard chart of accounts)",
            "GET /ledger/vatType (find correct VAT type — usually id=3 for 25% or check receipt for actual rate)",
            "POST /ledger/voucher?sendToLedger=true with postings:",
            "  Debit: expense account with amountGross=total incl VAT, vatType={id}, department={id}",
            "  Credit: bank account 1920 with amountGross=-total incl VAT",
        ],
        "key_lessons": [
            "The PDF contains a receipt with item name, amount, and often VAT details",
            "Match the item to the correct Norwegian expense account (e.g., Kontorrekvisita→6300, Reisekostnad→7140, Togbillett→7140, Drivstoff→7000, Kaffemøte→7350)",
            "Include department={id} on the DEBIT posting to link the expense to the correct department",
            "The amount from the receipt is usually INCLUSIVE of VAT — use amountGross for the full amount",
            "Let the VAT system auto-calculate by setting vatType on the debit posting",
            "Postings row starts at 1, amountGross must equal amountGrossCurrency",
            "Common VAT rates: 25% general (id=3), 15% food (id=31), 12% transport (id=5), 0% exempt (id=6)",
        ],
    },
    "task_20": {
        "description": "Register supplier invoice from attached PDF (leverandørfaktura) — also covers task_21",
        "golden_path": [
            "READ the attached PDF — extract ALL fields: supplier name, org number, address, invoice number (INV-XXXX-NNNN), invoice date, due date, net amount (ex VAT), VAT amount, gross amount (incl VAT), expense account (Konto: XXXX), bank account number",
            "GET /supplier?organizationNumber=X (check if supplier already exists — search by org number for exact match)",
            "If not found: POST /supplier with {name, organizationNumber, postalAddress: {addressLine1, postalCode, city, country: {id: 161}}, bankAccountPresentation: [{bban: 'BANK_ACCOUNT_NUMBER'}]}",
            "GET /ledger/account?number=XXXX,2400 (batch get expense + supplier debt accounts)",
            "GET /ledger/voucherType?name=Leverandørfaktura (get voucherType ID — REQUIRED)",
            "POST /ledger/voucher?sendToLedger=false — create voucher with voucherType=Leverandørfaktura, BOTH debit+credit postings, vendorInvoiceNumber=INV-XXXX",
            "PUT /supplierInvoice/voucher/{VOUCHER_ID}/postings — creates proper SupplierInvoice record. Body: [{posting: {account: {id: EXPENSE_ACCT_ID}, amountGross: GROSS, amountGrossCurrency: GROSS, vatType: {id: 1}}}]",
        ],
        "key_lessons": [
            "CRITICAL: POST /incomingInvoice ALWAYS returns 403 on the competition proxy. Do NOT try it — skip directly to the voucher approach.",
            "CRITICAL: Creating a voucher alone does NOT create a SupplierInvoice record. You MUST call PUT /supplierInvoice/voucher/{id}/postings to register it as a proper SupplierInvoice. Without this, the scorer finds no SupplierInvoice.",
            "POST /supplier body uses bankAccountPresentation: [{bban: '12345678901'}] — NOT bankAccountNumber.",
            "Include the supplier's address: parse street (addressLine1), postal code, and city from the PDF. country: {id: 161} is Norway.",
            "The PDF text is pre-extracted — read it directly from the message, no need to decode base64.",
            "Step 1: POST /ledger/voucher?sendToLedger=false with BOTH postings (must balance). Body: {"
            "date: 'INVOICE_DATE', description: 'SUPPLIER_NAME - description', "
            "voucherType: {id: VOUCHER_TYPE_ID}, vendorInvoiceNumber: 'INV-XXXX-NNNN', "
            "postings: ["
            "  {row: 1, account: {id: EXPENSE_ACCT_ID}, amountGross: GROSS_TOTAL, amountGrossCurrency: GROSS_TOTAL, vatType: {id: 1}, description: 'ITEM DESCRIPTION'}, "
            "  {row: 2, account: {id: ACCT_2400_ID}, amountGross: -GROSS_TOTAL, amountGrossCurrency: -GROSS_TOTAL, supplier: {id: SUPPLIER_ID}, "
            "   invoiceNumber: 'INV-XXXX-NNNN', termOfPayment: 'DUE_DATE', description: 'SUPPLIER_NAME'}]}",
            "Step 2: PUT /supplierInvoice/voucher/{VOUCHER_ID}/postings — body is a JSON ARRAY (not dict): [{\"posting\": {\"account\": {\"id\": EXPENSE_ACCT_ID}, \"amountGross\": GROSS_TOTAL, \"amountGrossCurrency\": GROSS_TOTAL, \"vatType\": {\"id\": 1}}}]. This converts the voucher into a SupplierInvoice. If it returns 403, fall back: PUT /ledger/voucher/{VOUCHER_ID}?sendToLedger=true.",
            "IMPORTANT: The json_body for PUT /supplierInvoice/voucher/{id}/postings is a JSON ARRAY (list), not a dict.",
            "voucherType MUST be set to Leverandørfaktura — get ID from GET /ledger/voucherType?name=Leverandørfaktura.",
            "Row 1 (debit): expense account, POSITIVE amountGross with vatType={id:1}. Row 2 (credit): account 2400, NEGATIVE amountGross with supplier reference.",
            "vatTypeId=1 is 'Fradrag inngående avgift, høy sats' (25% incoming VAT deduction).",
        ],
    },
    "task_21": {
        "description": "Register supplier invoice from attached PDF (same as task_20)",
        "alias": "task_20",
    },
    "task_23": {
        "description": "Reconcile bank statement (CSV) against open invoices and supplier payments",
        "golden_path": [
            "READ the attached CSV file — use the parse_structured_data tool with format='ssv' (semicolon-separated) or 'csv' to get structured rows",
            "GET /invoice?invoiceDateFrom=2020-01-01&invoiceDateTo=2030-12-31&fields=id,invoiceNumber,amount,amountOutstanding,customer(*) (find customer invoices)",
            "For each INCOMING payment row in CSV: match to an invoice by amount or reference (e.g., 'Faktura 1001')",
            "GET /invoice/paymentType?fields=id,description — find the ID where description='Betalt til bank' (MUST do before any payment calls)",
            "PUT /invoice/{id}/:payment?paymentDate=DATE&paidAmount=AMOUNT&paymentTypeId=BANK_TYPE_ID (use discovered ID, NOT 0)",
            "For partial payments: paidAmount = the CSV amount (less than full invoice amount)",
            "For supplier payments ('Betaling Fornecedor/Leverandor X'): GET /supplier?name=X to find supplier ID",
            "POST /ledger/voucher with postings: Row 1 debit 2400 with supplier:{id:X}, Row 2 credit 1920 — MUST include supplier reference on 2400 posting",
            "For fees (Bankgebyr): POST /ledger/voucher debit 7770 or 8150 + credit 1920",
            "For interest (Renteinntekter): check if Inn or Ut column — Inn=income (debit 1920, credit 8050), Ut=expense (debit 8150, credit 1920)",
        ],
        "key_lessons": [
            "CRITICAL: paymentTypeId=0 causes HTTP 500 errors. ALWAYS GET /invoice/paymentType first and use the ID where description='Betalt til bank'. The field is 'description' NOT 'name'.",
            "CRITICAL: Voucher postings on account 2400 (Leverandørgjeld) MUST include supplier:{id:X}. Without it → 422 'Leverandør mangler'. GET /supplier first to find the ID.",
            "CRITICAL: Voucher date field is 'date' NOT 'voucherDate'. 'voucherDate' does not exist and gets blocked by the validator.",
            "PUT /invoice/{id}/:payment requires QUERY params only — paymentDate, paidAmount, paymentTypeId. Do NOT send a JSON body.",
            "InvoiceDTO fields: 'amount' and 'amountOutstanding' exist. 'amountRemaining' and 'amountRemainingCurrency' do NOT exist (cause 400).",
            "CSV columns: Dato (date), Forklaring (description), Inn (incoming/positive), Ut (outgoing/negative), Saldo (balance). Use absolute values for amounts.",
            "Match invoices by 'Faktura NNNN' reference in the CSV Forklaring column. Invoice numbers in Tripletex start from 1.",
            "For partial payments, set paidAmount to the CSV amount. The system tracks remaining outstanding balance automatically.",
            "Accounts: 1920 (Bank), 1500 (Kundefordringer), 2400 (Leverandørgjeld), 8050 (Annen renteinntekt), 8150 (Annen rentekostnad), 7770 (Bankgebyr).",
        ],
    },
    "task_19": {
        "description": "Create employee from PDF employment contract (with national ID, occupation code, salary details)",
        "golden_path": [
            "READ the attached PDF carefully — extract ALL fields: name, nationalIdentityNumber, dateOfBirth, department, occupationCode, salary, percentageOfFullTimeEquivalent, startDate",
            "GET /department?name=X (find or create the department mentioned in the contract)",
            "GET /employee/employment/occupationCode?code=XXXX (find the occupation code ID — code is 4+ digits like 2320)",
            "POST /employee with: firstName, lastName, nationalIdentityNumber, dateOfBirth, email (if given), userType='NO_ACCESS', department={id}",
            "POST /employee/employment with: employee={id}, startDate, taxDeductionCode='loennFraHovedarbeidsgiver', employmentDetails=[{date, employmentType='ORDINARY', employmentForm='PERMANENT', remunerationType='MONTHLY_WAGE', workingHoursScheme='NOT_SHIFT', occupationCode={id}, percentageOfFullTimeEquivalent, annualSalary}]",
        ],
        "key_lessons": [
            "This is task_01 but with a PDF attachment containing the employment contract details",
            "nationalIdentityNumber (11-digit Norwegian personnummer) may be REJECTED by the competition proxy with 'Ugyldig format' — if so, OMIT it and continue. Partial credit is better than 0.",
            "The occupationCode is a 4-digit code like 2320 — search with ?code=2320 (returns all sub-codes like 2320102, 2320104). Pick the most general one.",
            "Include employmentDetails INLINE in POST /employee/employment to save an API call (no separate POST /employee/employment/details)",
            "If department doesn't exist, create it with POST /department before creating the employee",
        ],
    },
    "task_24": {
        "description": "Find and correct 4 specific ledger errors with correction vouchers",
        "golden_path": [
            "GET /ledger/voucher?dateFrom=2026-01-01&dateTo=2026-02-28&count=100 (get ALL vouchers in the date range)",
            "GET /ledger/posting?dateFrom=2026-01-01&dateTo=2026-02-28&count=5000 (get ALL postings to find the errors)",
            "GET /ledger/account?number=XXXX,YYYY,ZZZZ (batch lookup for accounts mentioned in prompt)",
            "GET /ledger/vatType?number=1 (for VAT corrections if needed)",
            "For DUPLICATE voucher: PUT /ledger/voucher/{id}/:reverse (cleanest way to reverse)",
            "For WRONG ACCOUNT: POST /ledger/voucher with correction postings (reverse from wrong, post to correct)",
            "For MISSING VAT: POST /ledger/voucher to add the missing VAT posting",
            "For WRONG AMOUNT: POST /ledger/voucher with difference postings (correct minus incorrect amount)",
        ],
        "key_lessons": [
            "The prompt tells you EXACTLY what the 4 errors are — use this info to search efficiently",
            "PUT /ledger/voucher/{id}/:reverse is the cleanest way to reverse a voucher — use it for duplicates",
            "For correction vouchers: postings row starts at 1 (NEVER 0), amountGross MUST equal amountGrossCurrency",
            "Match vouchers to errors by: account number + amount. The prompt gives account numbers and amounts for each error.",
            "GET /ledger/posting returns all postings — filter by account number to find specific errors",
            "Each correction should be a SEPARATE voucher (don't combine corrections into one voucher)",
            "Use sendToLedger=true on POST /ledger/voucher to finalize the correction",
            "For wrong amount corrections: post the DIFFERENCE (correct - incorrect), not the full correct amount",
        ],
    },
    "task_25": {
        "description": "Find overdue invoice, book reminder fee, create fee invoice, register partial payment",
        "golden_path": [
            "GET /invoice?invoiceDateFrom=2020-01-01&invoiceDateTo=2030-12-31 (find all invoices, identify overdue one)",
            "GET /ledger/account?number=1500,3400,1920 (batch: receivables, reminder fees, bank account)",
            "GET /invoice/paymentType (find payment type ID for bank payment)",
            "GET /ledger/vatType?number=6 (VAT exempt type for fees)",
            "POST /ledger/voucher?sendToLedger=true (book reminder fee: debit 1500, credit 3400)",
            "POST /order (create order for fee invoice to the customer)",
            "PUT /order/{id}/:invoice (convert order to invoice — fee invoice to customer)",
            "PUT /invoice/{overdueId}/:payment?paidAmount=X&paymentTypeId=Y&paymentDate=Z (register partial payment on overdue invoice)",
        ],
        "key_lessons": [
            "This task scored 5.25/6.0 on a successful run — follow the golden path closely",
            "The reminder fee voucher is a journal entry (debit receivables 1500, credit fee income 3400)",
            "The fee invoice is a SEPARATE invoice to the same customer (POST /order then PUT /:invoice)",
            "Partial payment uses PUT /invoice/{id}/:payment with the partial amount as query param",
            "PUT /:payment params: paidAmount, paymentTypeId, paymentDate — these are QUERY PARAMS not body",
            "Use vatType id=6 (exempt) for the fee — reminder fees are not subject to VAT",
            "Only write calls count for efficiency — GET calls are free. Do lookups upfront.",
        ],
    },
    "task_26": {
        "description": "Register payment in foreign currency and book exchange rate difference (agio/disagio)",
        "golden_path": [
            "Parse the prompt: identify invoice currency (EUR/USD/etc), original rate, payment rate, and amount",
            "GET /invoice?invoiceDateFrom=2020-01-01&invoiceDateTo=2030-12-31 (find the invoice)",
            "GET /ledger/account?number=1500,1920,8060,8160 (receivables, bank, agio, disagio accounts)",
            "GET /invoice/paymentType (find payment type ID)",
            "Calculate: agio (gain) if payment rate > invoice rate, disagio (loss) if payment rate < invoice rate",
            "Calculate: difference = amount_foreign_currency × (payment_rate - invoice_rate)",
            "PUT /invoice/{id}/:payment (register payment at the ORIGINAL invoice amount in NOK)",
            "POST /ledger/voucher?sendToLedger=true (book the exchange difference: debit/credit bank 1920 + agio 8060 or disagio 8160)",
        ],
        "key_lessons": [
            "Agio = currency GAIN (favorable rate change) → credit account 8060",
            "Disagio = currency LOSS (unfavorable rate change) → debit account 8160",
            "The payment is registered at the ORIGINAL NOK amount (from the invoice), not the new rate amount",
            "The exchange difference is booked as a SEPARATE voucher (not part of the payment)",
            "If rate went UP (e.g. 10.02 → 10.29 for receivable): agio — you receive MORE NOK than expected",
            "If rate went DOWN (e.g. 11.54 → 10.95 for receivable): disagio — you receive LESS NOK than expected",
            "The voucher for the difference: debit bank 1920 + credit 8060 (agio) OR debit 8160 + credit bank 1920 (disagio)",
        ],
    },
    "task_29": {
        "description": "Full project lifecycle: create project with budget, log timesheet hours, register supplier costs, create customer invoice",
        "golden_path": [
            "STEP 0 — Parallel lookups (do ALL of these in one batch):",
            "  GET /customer?organizationNumber=ORG_NR (find the customer)",
            "  GET /employee?email=EMAIL_1 (find project manager employee)",
            "  GET /employee?email=EMAIL_2 (find consultant employee)",
            "  GET /supplier?organizationNumber=SUPPLIER_ORG (find the supplier)",
            "",
            "STEP 1 — Create project with budget:",
            "  POST /project with: {name, projectManager:{id}, customer:{id}, startDate:'YYYY-MM-DD' (today), isInternal:false, isFixedPrice:true, fixedprice:BUDGET_AMOUNT}",
            "  NOTE: The budget amount goes in 'fixedprice' field (NOT 'budget' — that field does NOT exist!)",
            "",
            "STEP 2 — Find activities for the project, then log timesheet hours:",
            "  GET /activity/>forTimeSheet?projectId=PROJECT_ID&employeeId=EMPLOYEE_ID&date=TODAY",
            "  This returns project-specific activities. Use 'Fakturerbart arbeid' (billable work) activity ID.",
            "  POST /timesheet/entry {employee:{id:EMP1_ID}, project:{id:PROJECT_ID}, activity:{id:ACTIVITY_ID}, date:TODAY, hours:HOURS_1}",
            "  WAIT for the first entry to succeed, THEN send the second:",
            "  POST /timesheet/entry {employee:{id:EMP2_ID}, project:{id:PROJECT_ID}, activity:{id:ACTIVITY_ID}, date:TOMORROW, hours:HOURS_2}",
            "  CRITICAL: Send timesheet entries ONE AT A TIME (sequentially, not in parallel) AND use DIFFERENT dates!",
            "  Using the same date causes 409 Duplicate Entry errors even for different employees (API race condition).",
            "  All dates must be >= project startDate — cannot register hours before the project starts.",
            "",
            "STEP 3 — Register supplier cost as a ledger voucher (NOT project/orderline — it doesn't link to supplier!):",
            "  First find expense account and AP account in parallel:",
            "  GET /ledger/account?number=4300,6300,6900,2400&fields=id,number,name (try 4300 first, fallback to 6300 or 6900 if 4300 doesn't exist)",
            "  POST /ledger/voucher?sendToLedger=true with body:",
            "    {date:'TODAY', description:'Supplier cost SUPPLIER_NAME - PROJECT_NAME', postings:[",
            "      {row:1, account:{id:EXPENSE_ACCT_ID}, supplier:{id:SUPPLIER_ID}, project:{id:PROJECT_ID}, vatType:{id:1}, amountGross:COST_AMOUNT, amountGrossCurrency:COST_AMOUNT, description:'Supplier cost SUPPLIER_NAME'},",
            "      {row:2, account:{id:AP_2400_ID}, supplier:{id:SUPPLIER_ID}, amountGross:-COST_AMOUNT, amountGrossCurrency:-COST_AMOUNT, description:'Accounts payable SUPPLIER_NAME'}",
            "    ]}",
            "  vatType:{id:1} = 'Fradrag inngående avgift, høy sats' (25% input VAT deduction) for the expense posting",
            "  The credit posting (negative amount) goes to accounts payable 2400 — no VAT on the AP posting",
            "",
            "STEP 4 — Ensure bank account 1920 has a bank account number (required for invoicing):",
            "  GET /ledger/account?number=1920&fields=id,number,version,bankAccountNumber",
            "  IF bankAccountNumber is empty: PUT /ledger/account/{id} with {id, version, bankAccountNumber:'86011117947'}",
            "",
            "STEP 5 — Create customer invoice for the project (use POST /invoice with inline order):",
            "  POST /invoice?sendToCustomer=false with body: {invoiceDate:'TODAY', invoiceDueDate:'TODAY+30days', customer:{id:CUST_ID}, orders:[{customer:{id:CUST_ID}, project:{id:PROJECT_ID}, orderDate:'TODAY', deliveryDate:'TODAY', orderLines:[{description:'Project services', count:1, unitPriceExcludingVatCurrency:BUDGET_AMOUNT, vatType:{id:3}}]}]}",
            "  CRITICAL: invoiceDueDate is REQUIRED — set it to ~30 days after invoiceDate. Omitting it causes 422.",
            "  The invoice amount should equal the project budget/fixedprice amount.",
        ],
        "key_lessons": [
            "This is a 4-part task: (1) create project with budget, (2) log hours, (3) supplier cost, (4) invoice",
            "POST /project does NOT accept 'budget' field — use 'fixedprice' + 'isFixedPrice:true' instead",
            "For timesheet: first call GET /activity/>forTimeSheet?projectId=X to find valid activities for the project",
            "Do NOT use GET /activity?isGeneral=true — those general activities may NOT work for the specific project",
            "Use DIFFERENT dates for each employee's timesheet entry to avoid 409 Duplicate Entry conflicts",
            "Timesheet dates must be >= project startDate — cannot register hours before the project starts",
            "Supplier cost: use POST /ledger/voucher with supplier+project linked in the expense posting (debit expense/credit AP 2400)",
            "POST /invoice REQUIRES invoiceDueDate — set to invoiceDate + 30 days",
            "vatType:{id:3} = 'Utgående avgift, høy sats' (25% output VAT) — standard for Norwegian invoices",
            "The invoice order line amount should be the project budget (fixedprice) amount",
        ],
    },
    "task_30": {
        "description": "Simplified year-end closing: depreciation of multiple assets + prepaid expense reversal + tax provision",
        "golden_path": [
            "Step 1: Parse the prompt CAREFULLY — extract for EACH asset: name, cost (NOK), useful life (years), asset account number. Also extract prepaid amount and account 1700.",
            "Step 2: Use calculate_accounting tool for EACH asset: calculate_accounting(operation='depreciation', cost=ASSET_COST, useful_life_years=YEARS) — this returns the exact annual amount, correctly rounded.",
            "Step 3: GET /ledger/account?number=COMMA_SEPARATED_LIST — batch lookup ALL accounts mentioned in the prompt (asset accounts, 1209, 1700, 2920, 6010, 6300, 8700). Include 6300 for prepaid expense reversal. IMPORTANT: accounts 1209 and 8700 often DO NOT EXIST in the sandbox chart of accounts.",
            "Step 4: If account 1209 is missing from results: POST /ledger/account with {number: 1209, name: 'Akkumulerte avskrivninger'}. If 8700 is missing: POST /ledger/account with {number: 8700, name: 'Skattekostnad'}. Create both in parallel if both are missing.",
            "Step 5: POST /ledger/voucher?sendToLedger=true for EACH asset (3 SEPARATE vouchers, in parallel). Body: {date: '2025-12-31', description: 'Avskrivning ASSET_NAME 2025', postings: [{account: {id: ACCT_6010_ID}, amountGross: ANNUAL_AMOUNT, amountGrossCurrency: ANNUAL_AMOUNT, row: 1}, {account: {id: ACCT_1209_ID}, amountGross: -ANNUAL_AMOUNT, amountGrossCurrency: -ANNUAL_AMOUNT, row: 2}]}",
            "Step 6: POST /ledger/voucher?sendToLedger=true for prepaid expense reversal. The expense account for 1700 (Forskuddsbetalt leiekostnad) is 6300 (Leie lokale). Body: {date: '2025-12-31', description: 'Oppløsning forskuddsbetalte kostnader 2025', postings: [{account: {id: ACCT_6300_ID}, amountGross: PREPAID_AMOUNT, amountGrossCurrency: PREPAID_AMOUNT, row: 1}, {account: {id: ACCT_1700_ID}, amountGross: -PREPAID_AMOUNT, amountGrossCurrency: -PREPAID_AMOUNT, row: 2}]}",
            "Step 7: GET /balanceSheet?dateFrom=2025-01-01&dateTo=2026-01-01&accountNumberFrom=3000&accountNumberTo=8699 — returns per-account balances for ALL P&L accounts including the new depreciation and prepaid postings. Do NOT use the 'fields' parameter — it causes 400 errors (BalanceSheetAccountDTO only supports: account, balanceIn, balanceChange, balanceOut, startDate, endDate). Response: {values: [{account: {id, number, name}, balanceIn, balanceChange, balanceOut}, ...]}.",
            "Step 8: Calculate taxable profit from balanceSheet response: Sum all 'balanceChange' values for accounts 3000-8699 (EXCLUDE 8700 if it appears — that's the tax expense account you haven't populated yet). Revenue accounts (3xxx) show NEGATIVE balanceChange (credit), expense accounts (4xxx-8xxx) show POSITIVE balanceChange (debit). Taxable profit = -1 * SUM(all balanceChange values). Tax = taxable_profit * 0.22. Round to 2 decimals. IMPORTANT: Use balanceChange (NOT balanceOut) since we want only 2025 P&L activity, not cumulative balance.",
            "Step 9: POST /ledger/voucher?sendToLedger=true for tax provision. Body: {date: '2025-12-31', description: 'Skatteavsetning 2025 (22%)', postings: [{account: {id: ACCT_8700_ID}, amountGross: TAX_AMOUNT, amountGrossCurrency: TAX_AMOUNT, row: 1}, {account: {id: ACCT_2920_ID}, amountGross: -TAX_AMOUNT, amountGrossCurrency: -TAX_AMOUNT, row: 2}]}",
        ],
        "key_lessons": [
            "CRITICAL: EACH depreciation is a SEPARATE voucher (the prompt says this explicitly). You need 3 depreciation vouchers + 1 prepaid + 1 tax = 5 vouchers total.",
            "CRITICAL: Use calculate_accounting(operation='depreciation', cost=X, useful_life_years=Y) for EACH asset instead of manual math. It handles rounding correctly.",
            "CRITICAL: Use GET /balanceSheet (NOT /ledger/postingByDate) to get P&L account totals for the tax calculation. The balanceSheet endpoint returns balanceIn/balanceChange/balanceOut per account — much more efficient than summing individual postings. Use 'balanceChange' for the P&L calculation.",
            "CRITICAL: Accounts 1209 (accumulated depreciation) and 8700 (tax expense) are NOT in the default chart of accounts. You MUST check if they exist and create them if missing. Also check 2920 — it usually exists but may have a different name like 'Gjeld til selskap i samme konsern'.",
            "CRITICAL: The expense account for prepaid rent reversal (1700 Forskuddsbetalt leiekostnad) should be 6300 (Leie lokale). If the prompt specifies a different expense account, use that instead.",
            "Postings: row starts at 1 (never 0). amountGross = amountGrossCurrency. Positive = debit, negative = credit.",
            "voucherType can be omitted (null) — Tripletex auto-assigns a default type.",
            "Tax provision: Norwegian corporate tax rate is 22%. Taxable profit = revenue minus all expenses (including depreciation and prepaid reversal you just booked). The GET /balanceSheet call AFTER steps 5-6 will include those new postings.",
            "IMPORTANT: The balanceSheet dateTo is EXCLUSIVE. Use dateTo=2026-01-01 to include all of 2025. accountNumberFrom and accountNumberTo filter the account range.",
            "EFFICIENCY: Steps 2 (calculate) and 3 (GET accounts) can run in parallel. Steps 5 (3 depreciation vouchers) can run in parallel. Step 7 MUST wait for steps 5-6 to complete. Total: ~8-10 API calls.",
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
        """Classify prompt to task type using keyword scoring.

        Uses accent-insensitive matching so accented multilingual prompts
        (e.g. Portuguese 'salário') match ASCII keywords ('salario').
        """
        prompt_lower = _strip_accents(prompt.lower())
        scores: dict[str, float] = {}

        for task_type, keyword_rules in TASK_KEYWORDS.items():
            score = 0.0
            for keywords, weight in keyword_rules:
                if any(_strip_accents(kw) in prompt_lower for kw in keywords):
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

            # Resolve aliases (e.g., task_21 → task_20)
            if curated and "alias" in curated:
                curated = CURATED_PLAYBOOKS.get(curated["alias"])

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
