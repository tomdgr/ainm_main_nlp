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
    "task_26": [  # Currency exchange gain/loss (agio/disagio)
        ({"agio", "disagio"}, 4.0),
        ({"valutadifferanse", "currency difference", "différence de change", "diferencia de cambio", "währungsdifferenz"}, 3.0),
        ({"kurs", "exchange rate", "taux de change", "tipo de cambio", "wechselkurs"}, 2.0),
        ({"eur", "usd", "gbp", "sek", "dkk"}, 1.5),
        ({"8060", "8160"}, 1.5),  # Agio/disagio accounts
    ],
    "task_29": [  # Full project lifecycle (budget + hours + supplier costs + customer invoice)
        ({"projektzyklus", "project cycle", "ciclo de proyecto", "ciclo do projeto", "cycle de projet", "prosjektsyklus"}, 5.0),
        ({"projektabrechnungszyklus", "project billing cycle"}, 5.0),
        ({"stunden erfassen", "log hours", "registrer timer", "enregistrer heures", "registrar horas"}, 3.0),
        ({"lieferantenkosten", "supplier costs", "costes proveedor", "custos fornecedor", "coûts fournisseur", "leverandørkostnader"}, 3.0),
        ({"kundenrechnung", "customer invoice", "factura cliente", "fatura cliente", "facture client", "kundefaktura"}, 2.5),
        ({"budget"}, 2.0),
    ],
    "task_30": [  # Year-end closing with depreciation + tax provision (multiple assets)
        ({"årlig", "annuel", "anual", "annual", "jährlich", "årsavslutning", "jahresabschluss", "year-end", "clôture annuelle", "cierre anual"}, 3.0),
        ({"avskrivning", "depreciation", "amortissement", "depreciación", "depreciação", "abschreibung"}, 4.0),
        ({"immobilisations", "anlagen", "activos fijos", "ativos fixos", "fixed assets", "anleggsmidler"}, 3.0),
        ({"programvare", "kontormaskiner", "kjøretøy", "it-utstyr"}, 3.0),  # Norwegian asset names commonly in prompts
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
        "description": "Register supplier invoice",
        "golden_path": [
            "GET /supplier?organizationNumber=X (find supplier)",
            "GET /ledger/account?number=XXXX,2400 (batch get expense + supplier debt accounts)",
            "Try approach A: POST /incomingInvoice?sendTo=ledger (BETA — single call, ideal if it works)",
            "If 403: Try approach B: POST /ledger/voucher + PUT /supplierInvoice/voucher/{id}/postings",
            "If both fail: Fallback C: POST /ledger/voucher with voucherType=Leverandørfaktura and both postings",
        ],
        "key_lessons": [
            "APPROACH A (preferred): POST /incomingInvoice?sendTo=ledger — creates supplier invoice in ONE call. Body: {invoiceHeader: {vendorId: SUPPLIER_ID, invoiceDate: 'YYYY-MM-DD', dueDate: 'YYYY-MM-DD', invoiceAmount: GROSS_AMOUNT}, orderLines: [{externalId: 'line-1', row: 1, description: '...', accountId: EXPENSE_ACCT_ID, amountInclVat: GROSS_AMOUNT, vatTypeId: 1}]}. NOTE: externalId is REQUIRED on each orderLine. vatTypeId=1 for 25% ingoing VAT.",
            "APPROACH B (if A returns 403): Two-step process: 1) POST /ledger/voucher?sendToLedger=false with ONLY credit posting: {date, vendorInvoiceNumber, postings: [{row:1, account:{id:2400_ID}, amountGross:-GROSS, amountGrossCurrency:-GROSS, supplier:{id}}]}. 2) PUT /supplierInvoice/voucher/{id}/postings?sendToLedger=true with OrderLinePosting schema: [{posting: {account: {id: EXPENSE_ID}, amountGross: GROSS, amountGrossCurrency: GROSS, vatType: {id: 1}}}]. CRITICAL: voucher in step 1 must have ONLY ONE posting (the credit), because PUT fails with 'Can not put postings on a voucher that already have postings'.",
            "FALLBACK C: POST /ledger/voucher with voucherType=Leverandørfaktura and both debit+credit postings. This creates a voucher but may NOT appear in GET /supplierInvoice — use as last resort.",
            "Gross amount is TTC (VAT included). Net = gross / 1.25 for 25% VAT.",
            "vendorInvoiceNumber carries the invoice reference (INV-XXXX).",
            "MUST set both amountGross AND amountGrossCurrency (same value for NOK).",
        ],
    },
    "task_12": {
        "description": "Run payroll for employee with base salary and bonus",
        "golden_path": [
            "GET /employee?email=X (find the employee)",
            "GET /salary/type?isInactive=false (find salary type IDs — look for 'Fastlønn'/'Fast lønn' for base salary and 'Bonus' for bonus)",
            "GET /employee/employment?employeeId=X (check if employment exists)",
            "If no employment: set up employment (see prerequisites below), then retry",
            "POST /salary/transaction?generateTaxDeduction=true (with nested payslips array — see body structure below)",
        ],
        "key_lessons": [
            "POST /salary/transaction body: {date: 'YYYY-MM-DD', year: YYYY, month: M, payslips: [{employee: {id}, date: 'YYYY-MM-DD', year: YYYY, month: M, specifications: [{salaryType: {id: FASTLONN_ID}, rate: BASE_AMOUNT, count: 1}, {salaryType: {id: BONUS_ID}, rate: BONUS_AMOUNT, count: 1}]}]}",
            "Use generateTaxDeduction=true query param to auto-calculate tax.",
            "PREREQUISITE: Employee MUST have an active employment record linked to a division. Without this you get 422 'Ansatt er ikke registrert med et arbeidsforhold i perioden'.",
            "Employment setup if needed: 1) PUT /employee to set dateOfBirth if missing, 2) POST /employee/employment with {employee:{id}, startDate, taxDeductionCode:'loennFraHovedarbeidsgiver', isMainEmployer:true}, 3) POST /employee/employment/details with {employment:{id}, date:startDate, employmentType:'ORDINARY', remunerationType:'MONTHLY_WAGE', workingHoursScheme:'NOT_SHIFT', percentageOfFullTimeEquivalent:100.0}, 4) POST /division to create a division, 5) PUT /employee/employment/{id} to link division={id}.",
            "After linking the division you get 422 'Arbeidsforholdet er ikke knyttet mot en virksomhet' if division is missing.",
            "Salary type IDs vary per sandbox — always GET /salary/type first to discover the correct IDs. Search for name containing 'Fast' for base salary and 'Bonus' for bonus.",
            "employmentType enum: ORDINARY, MARITIME, FREELANCE, NOT_CHOSEN. remunerationType enum: MONTHLY_WAGE, HOURLY_WAGE, COMMISION_PERCENTAGE, FEE, NOT_CHOSEN, PIECEWORK_WAGE. workingHoursScheme enum: NOT_SHIFT, ROUND_THE_CLOCK, SHIFT_365, OFFSHORE_336, CONTINUOUS, OTHER_SHIFT, NOT_CHOSEN.",
        ],
    },
    "task_13": {
        "description": "Register travel expense with per diem and costs",
        "golden_path": [
            "GET /employee?email=X (find the employee)",
            "GET /travelExpense/rateCategory?type=PER_DIEM&dateFrom=YYYY-MM-01&dateTo=YYYY-MM-28&name=Overnatting&fields=id,name,type (find overnight rate category — MUST filter by dateFrom/dateTo)",
            "GET /travelExpense/rate?rateCategoryId=X&fields=id,rate (find rateType ID — ONLY use fields id,rate — 'name' and 'type' do NOT exist on TravelExpenseRateDTO)",
            "GET /travelExpense/costCategory?isInactive=false&fields=id,description (find IDs — 'name' does NOT exist, use 'description'. Look for 'Fly'/'Flybillett' for flight, 'Taxi'/'Drosje' for taxi)",
            "GET /travelExpense/paymentType?isInactive=false&fields=id,description (find payment type — 'name' does NOT exist, use 'description')",
            "POST /travelExpense with nested perDiemCompensations and costs (see exact body below)",
        ],
        "key_lessons": [
            "EXACT POST /travelExpense body — follow this structure precisely:\n"
            "{\n"
            "  employee: {id: EMPLOYEE_ID},\n"
            "  title: 'Trip title here',\n"
            "  travelDetails: {\n"
            "    departureDate: 'YYYY-MM-DD', returnDate: 'YYYY-MM-DD',\n"
            "    departureTime: '08:00', returnTime: '18:00',\n"
            "    departureFrom: 'Oslo', destination: 'Bergen',\n"
            "    purpose: 'Trip purpose here',\n"
            "    isForeignTravel: false, isDayTrip: false\n"
            "  },\n"
            "  perDiemCompensations: [{\n"
            "    rateCategory: {id: RATE_CATEGORY_ID},\n"
            "    rateType: {id: RATE_TYPE_ID},\n"
            "    overnightAccommodation: 'HOTEL',\n"
            "    location: 'Destination city',\n"
            "    count: NUMBER_OF_DAYS\n"
            "  }],\n"
            "  costs: [\n"
            "    {costCategory: {id: FLIGHT_CAT_ID}, paymentType: {id: PAY_TYPE_ID}, amountCurrencyIncVat: FLIGHT_AMOUNT, date: 'YYYY-MM-DD', comments: 'Flight'},\n"
            "    {costCategory: {id: TAXI_CAT_ID}, paymentType: {id: PAY_TYPE_ID}, amountCurrencyIncVat: TAXI_AMOUNT, date: 'YYYY-MM-DD', comments: 'Taxi'}\n"
            "  ]\n"
            "}",
            "FIELD NAME RULES: 'title' goes on the TravelExpense root (NOT in travelDetails). 'purpose' goes inside travelDetails. departureDate/returnDate go inside travelDetails (NOT on root).",
            "Cost fields: use 'costCategory' (NOT 'category'), 'comments' (NOT 'description'), 'amountCurrencyIncVat' (NOT 'amount'). Cost does NOT have 'count' field — VERIFIED: 'count' causes 422 'Feltet eksisterer ikke i objektet'.",
            "Per diem: use 'count' for number of days (NOT 'countDays'). MUST include 'rateType': {id: RATE_ID} from GET /travelExpense/rate. Do NOT include 'countryCode' — it causes 'Country not enabled for travel expense' error.",
            "CRITICAL: Rate categories are date-dependent. Use dateFrom/dateTo matching the travel dates. Without date filter, you get old expired categories that cause 422 'dato samsvarer ikke med valgt satskategori'.",
            "FIELD NAME WARNING: TravelExpenseRateDTO has 'id' and 'rate' fields but NOT 'name' or 'type'. TravelCostCategoryDTO has 'id' and 'description' but NOT 'name'. Using wrong field names in ?fields= causes 400 errors. Always use fields=id,description for costCategory and fields=id,rate for rate.",
            "Do NOT set manual rate/amount on per diem unless the prompt specifies a custom rate. Let the system calculate from rateCategory.",
            "overnightAccommodation enum: NONE, HOTEL, BOARDING_HOUSE_WITHOUT_COOKING, BOARDING_HOUSE_WITH_COOKING. Use HOTEL for multi-day trips.",
            "Creating everything nested in one POST avoids 409 RevisionException that occurs when creating costs separately.",
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
        "description": "Analyze expense increase between months + create internal projects with activities",
        "golden_path": [
            "GET /token/session/>whoAmI (get employee ID for projectManager — REQUIRED before creating projects)",
            "GET /ledger/postingByDate?dateFrom=2026-01-01&dateTo=2026-02-01&count=5000 (January postings — NO fields param!)",
            "GET /ledger/postingByDate?dateFrom=2026-02-01&dateTo=2026-03-01&count=5000 (February postings)",
            "GET /activity?isProjectActivity=true&count=1 (find an existing project activity to link)",
            "Analyze: sum amounts per account (expense accounts 5000-7999), compute Feb-Jan delta, find top 3 increases",
            "GET /ledger/account?id=X,Y,Z (get account names for the top 3 account IDs)",
            "POST /project for EACH of the 3 accounts (name=account name, projectManager={id}, isInternal=true, startDate=today)",
            "POST /project/projectActivity for EACH project (link the activity found in step 4)",
        ],
        "key_lessons": [
            "CRITICAL: GET /ledger/postingByDate does NOT support the fields parameter — always returns 422 if you include it. Omit fields entirely.",
            "CRITICAL: POST /project REQUIRES projectManager field — always do GET /token/session/>whoAmI FIRST to get employee ID",
            "POST /project also REQUIRES startDate — use today's date",
            "Create projects SEQUENTIALLY, not in parallel — parallel creation causes 500/409 errors from race conditions",
            "Use GET /ledger/postingByDate (not /ledger/posting) — it returns postings grouped by date and is more efficient",
            "dateFrom is inclusive, dateTo is EXCLUSIVE (so dateTo=2026-02-01 covers all of January)",
            "Expense accounts are in the 5000-7999 range in Norwegian chart of accounts",
            "The activity linked to each project should be an existing PROJECT_GENERAL_ACTIVITY type",
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
        "description": "Full project lifecycle: create project, log hours, register supplier costs, create customer invoice",
        "golden_path": [
            "GET /token/session/>whoAmI (get employee ID for project manager)",
            "GET /customer?organizationNumber=X (find or create the customer)",
            "GET /employee?email=X (find employees mentioned in the prompt — may need to create them)",
            "POST /project with: name, projectManager={id}, customer={id}, isInternal=false, startDate=today",
            "POST /project/projectActivity for each activity needed (link existing activities to project)",
            "POST /timesheet/entry for each employee's hours (employee={id}, project={id}, activity={id}, hours, date)",
            "For supplier costs: GET /supplier?organizationNumber=X, then POST /ledger/voucher to book the supplier cost",
            "POST /order with customer, project, orderLines for the invoice amount",
            "PUT /order/{id}/:invoice to create the customer invoice",
        ],
        "key_lessons": [
            "This is a FULL project billing cycle — not just project creation",
            "POST /project REQUIRES projectManager and startDate (auto-validated)",
            "POST /project does NOT accept 'budget' field — it will be blocked by the validator",
            "For timesheet entries: use POST /timesheet/entry with employee, project, activity, date, hours",
            "Supplier costs are booked as vouchers (POST /ledger/voucher), not through the project API",
            "The customer invoice should cover all project costs — create via POST /order + PUT /order/:invoice",
            "Find existing activities with GET /activity?isProjectActivity=true before creating project activities",
            "If employees don't exist, create them first (POST /employee + department + userType='STANDARD')",
        ],
    },
    "task_30": {
        "description": "Simplified year-end closing: depreciation of multiple assets + prepaid expense reversal + tax provision",
        "golden_path": [
            "Parse the prompt: extract asset names, costs, useful lives, accounts for each asset",
            "GET /ledger/account?number=1200,1209,1210,1230,1250,1700,2920,6010,8700 (batch ALL accounts mentioned)",
            "GET /ledger/voucherType (find correct voucher type)",
            "For EACH ASSET: POST /ledger/voucher?sendToLedger=true — depreciation entry",
            "  Debit: depreciation expense account (e.g. 6010), amount = cost / useful_life_years",
            "  Credit: accumulated depreciation account (e.g. 1209), same amount",
            "For PREPAID EXPENSES: POST /ledger/voucher?sendToLedger=true",
            "  Debit: appropriate expense account, credit: 1700 (prepaid), full amount",
            "For TAX PROVISION: First calculate taxable profit from GET /ledger (or use the amounts given)",
            "  POST /ledger/voucher: Debit 8700 (tax expense), Credit 2920 (tax payable), amount = 22% × taxable profit",
        ],
        "key_lessons": [
            "EACH depreciation is a SEPARATE voucher (the prompt says this explicitly)",
            "Annual depreciation = asset cost / useful life in years (linear method)",
            "Common accounts: 6010 (depreciation expense), 1209 (accumulated depreciation for all asset types)",
            "Asset accounts: 1200 (machinery), 1210 (IT equipment), 1230 (vehicles), 1250 (software)",
            "Tax rate is 22% of taxable profit (standard Norwegian corporate tax rate)",
            "To calculate taxable profit: you may need GET /ledger with dateFrom/dateTo to get total revenue - expenses",
            "Or the prompt may give you the total prepaid amount and asset details — calculate from those",
            "Postings row must start from 1 (auto-fixed), amountGross = amountGrossCurrency (auto-fixed)",
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
