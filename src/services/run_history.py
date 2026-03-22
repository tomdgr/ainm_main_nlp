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
        ({"fixed price", "fastpris", "festpreis", "prix fixe", "prix forfaitaire",
          "precio fijo", "preço fixo", "preco fixo"}, 4.0),
        ({"milestone", "delbetaling", "teilzahlung", "acompte", "paiement d'etape",
          "pago parcial", "pagamento parcial", "etappenzahlung"}, 3.0),
        ({"75%", "75 %", "50%", "50 %", "25%", "25 %"}, 2.0),
        ({"facturez", "invoice the customer", "fakturere kunden", "invoice for",
          "rechnung stellen", "faturar ao cliente"}, 1.5),
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
    "task_21": [  # Employee from offer letter PDF (same as task_19 but different prompt variant)
        ({"tilbudsbrev", "tilbodsbrev", "offer letter", "carta de oferta", "lettre d'offre",
          "angebotsschreiben", "lettera di offerta"}, 4.0),
        ({"vedlagt", "attached", "ci-joint", "adjunto", "anexo", "beigefügt", "allegato"}, 2.0),
        ({"funcionario", "funcionária", "empregado", "employee", "ansatt", "tilsett",
          "mitarbeiter", "employé", "empleado"}, 2.0),
        ({"integracao", "integração", "onboarding", "integration", "intégration"}, 2.0),
        ({"departamento", "department", "avdeling", "abteilung", "département"}, 1.0),
    ],
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
    "task_19": [  # Employee from PDF (employment contract or offer letter)
        ({"arbeidskontrakt", "employment contract", "contrat de travail", "contrato de trabajo",
          "contrato de trabalho", "arbeitsvertrag", "carta de oferta", "offer letter",
          "lettre d'offre", "carta de oferta", "tilbudsbrev", "angebotsschreiben"}, 4.0),
        ({"vedlagt pdf", "attached pdf", "pdf ci-joint", "pdf adjunto", "pdf anexo", "beigefügte pdf"}, 3.0),
        ({"funcionario", "funcionária", "empregado", "employee", "ansatt", "tilsett",
          "mitarbeiter", "employé", "empleado"}, 2.0),
        ({"integracao", "integração", "onboarding", "integration", "intégration"}, 2.0),
        ({"personnummer", "national identity", "numéro d'identité", "número de identidad",
          "número de identidade", "personalnummer"}, 1.5),
        ({"stillingskode", "occupation code", "code profession", "código de ocupación"}, 1.0),
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
        ({"valutadifferanse", "currency difference", "différence de change", "diferencia de cambio",
          "währungsdifferenz", "diferenca de cambio", "exchange rate difference",
          "kursdifferanse", "valutakursdifferanse"}, 3.0),
        ({"kurs", "exchange rate", "taux de change", "tipo de cambio", "wechselkurs",
          "taxa de cambio", "vekslingskurs"}, 3.0),
        ({"eur", "usd", "gbp", "sek", "dkk"}, 2.0),
        ({"nok/eur", "nok/usd", "nok/gbp", "nok/sek", "nok/dkk",
          "eur/nok", "usd/nok"}, 2.0),
        ({"8060", "8160"}, 1.5),  # Agio/disagio accounts
        ({"valutatap", "valutagevinst", "currency loss", "currency gain"}, 2.0),
    ],
    "task_29": [  # Full project lifecycle (budget + hours + supplier costs + customer invoice)
        # "ciclo de vida" matches Spanish, "projektzyklus" German, "prosjektsyklus" Norwegian
        ({"projektzyklus", "project lifecycle", "project cycle", "ciclo de vida",
          "ciclo do projeto", "cycle de vie", "prosjektsyklus", "prosjektlivssyklus",
          "ciclo di vita", "ciclo de vida completo"}, 5.0),
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
        ({"årlig", "annuel", "anual", "annual", "jährlich", "årsavslutning", "arsoppgjer",
          "jahresabschluss", "year-end", "clôture annuelle", "cierre anual"}, 3.0),
        ({"avskrivning", "avskrivingar", "avskriving", "depreciation", "amortissement",
          "depreciación", "depreciação", "abschreibung"}, 4.0),
        ({"immobilisations", "anlagen", "activos fijos", "ativos fixos", "fixed assets",
          "anleggsmidler", "eigedelar"}, 3.0),
        ({"programvare", "kontormaskiner", "kjøretøy", "it-utstyr", "inventar"}, 3.0),
        ({"skatteberegning", "tax provision", "provision d'impôt", "provisión fiscal", "steuerrückstellung"}, 2.5),
        ({"forskuddsbetalt", "forskotsbetalt", "prepaid", "constatées d'avance", "gastos anticipados", "vorausbezahlt"}, 2.0),
        ({"6010", "1209"}, 1.5),  # Depreciation expense / accumulated depreciation accounts
        ({"8700", "2920"}, 1.5),  # Tax expense / tax payable accounts
    ],
}


# ---------------------------------------------------------------------------
# Playbook overrides — surgical control for specific tasks
# ---------------------------------------------------------------------------
# - "disabled": never inject this playbook regardless of confidence
# - "experimental": inject with softer framing even at high confidence
PLAYBOOK_OVERRIDES: dict[str, str] = {
    # "task_11": "disabled",  # Re-enabled — new create_supplier_invoice tool handles it
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
            "GET calls are free (don't affect efficiency score). Verification is optional for simple creates but recommended when chaining (e.g., verify employment was linked correctly).",
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
            "POST /invoice?sendToCustomer=true — create invoice with INLINE order (1 write call instead of 2):\n"
            "  Body: {invoiceDate:'TODAY', invoiceDueDate:'TODAY+14days', customer:{id:CUST_ID},\n"
            "    orders:[{customer:{id:CUST_ID}, deliveryDate:'TODAY', orderDate:'TODAY',\n"
            "      orderLines:[{description:'SERVICE_DESC', count:1,\n"
            "        unitPriceExcludingVatCurrency:AMOUNT, vatType:{id:3}}]}]}",
        ],
        "key_lessons": [
            "EFFICIENCY: Use POST /invoice with inline orders — this creates order + invoice in 1 write call. NEVER use POST /order + PUT /order/:invoice (2 writes = lower score).",
            "Do NOT create a product (POST /product) — put the service description directly on orderLine.description. Creating a product wastes a write call.",
            "Do NOT manually check/set bank account 1920 — it is auto-configured before POST /invoice. Manual PUT wastes a write call.",
            "POST /invoice REQUIRES invoiceDueDate — set to invoiceDate + 14 days.",
            "Orders in the inline array REQUIRE deliveryDate — use today's date.",
            "'Excluding VAT' / 'sin IVA' / 'hors TVA' means the stated price is without VAT, but 25% VAT still applies (vatType id=3). Do NOT use vatType 0.",
            "Optimal: 1 GET (customer) + 1 POST (invoice) = 2 calls total, 1 write.",
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
            "GET /product?productNumber=NUM1&productNumber=NUM2&productNumber=NUM3 (find ALL products in ONE call — use REPEATED productNumber params)",
            "POST /invoice with inline order — 1 write call:\n"
            "  Body: {invoiceDate:'TODAY', invoiceDueDate:'TODAY+14days',\n"
            "    orders:[{customer:{id:CUST_ID}, deliveryDate:'TODAY', orderDate:'TODAY',\n"
            "      orderLines:[{product:{id:P1_ID}, count:1, unitPriceExcludingVatCurrency:PRICE1, vatType:{id:3}},\n"
            "        {product:{id:P2_ID}, count:1, unitPriceExcludingVatCurrency:PRICE2, vatType:{id:31}},\n"
            "        {product:{id:P3_ID}, count:1, unitPriceExcludingVatCurrency:PRICE3, vatType:{id:6}}]}]}",
        ],
        "key_lessons": [
            "EFFICIENCY: Use POST /invoice with inline orders — 1 write call instead of POST /order + PUT /order/:invoice (2 writes). Bank account 1920 is auto-checked before POST /invoice.",
            "VAT type IDs: id=3 for 25% (Utgående avgift, høy sats), id=31 for 15% (Utgående avgift, middels sats/næringsmiddel/food), id=6 for 0% exempt (Ingen utgående avgift, utenfor mva-loven/avgiftsfri). Do NOT use id=5 for exempt — use id=6.",
            "BATCH product lookup: GET /product?productNumber=NUM1&productNumber=NUM2&productNumber=NUM3 fetches all 3 products in ONE call. Do NOT use comma-separated syntax (productNumber=1,2,3) — that returns 0 results. Do NOT make 3 separate GET /product calls.",
            "Products already exist — search by productNumber. Do NOT create new products.",
            "GET calls are free — you may verify orderlines if needed, but the POST response already contains all necessary IDs.",
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
            "GET calls are free — verification is optional since the 201 response confirms success, but use GETs if you need to chain entity IDs.",
        ],
    },
    "task_11": {
        "description": "Register supplier invoice (WITHOUT PDF — details given in text)",
        "golden_path": [
            "GET /supplier?organizationNumber=X (find supplier)",
            "GET /ledger/account?number=XXXX,2400 (find expense account + AP account IDs)",
            "",
            "CRITICAL — Search for a pre-existing unposted voucher first:",
            "GET /ledger/voucher?dateFrom=2020-01-01&dateTo=2030-12-31&count=50&sorting=-tempNumber",
            "Look for vouchers with EMPTY postings array (0 postings) — the competition may pre-create an unposted voucher.",
            "",
            "If unposted voucher found (0 postings):",
            "  PUT /supplierInvoice/voucher/{id}/postings with body: [{posting: {account:{id:EXPENSE_ACCT_ID}, supplier:{id:SUPPLIER_ID}, amountGross:GROSS_AMOUNT, amountGrossCurrency:GROSS_AMOUNT, vatType:{id:1}, row:1, date:INVOICE_DATE, description:DESCRIPTION}}]",
            "  This creates both the debit postings AND the SupplierInvoice entity.",
            "",
            "If NO unposted voucher found:",
            "  Use create_supplier_invoice tool as fallback (creates Leverandørfaktura voucher + attempts /incomingInvoice best-effort).",
        ],
        "key_lessons": [
            "CRITICAL: ALWAYS search for pre-existing unposted vouchers first (GET /ledger/voucher, filter for empty postings). The competition sandbox may have a pre-created voucher in draft/inbox state. PUT /supplierInvoice/voucher/{id}/postings ONLY works on vouchers WITHOUT postings.",
            "PUT body format: [{posting: {account:{id}, supplier:{id}, amountGross, amountGrossCurrency, vatType:{id:1}, row:1, date, description}}] — note the OrderLinePosting wrapper.",
            "The gross_amount is TTC (VAT included). vatType:{id:1} = 25% incoming VAT deduction.",
            "POST /incomingInvoice returns 403 (no permission). The create_supplier_invoice tool tries it as best-effort.",
            "Due date: if not specified, use invoice date + 30 days.",
        ],
    },
    "task_12": {
        "description": "Run payroll for employee with base salary and bonus",
        "golden_path": [
            "STEP 1 (parallel) — GET /employee?email=X&fields=id,firstName,lastName,dateOfBirth AND GET /salary/type?isInactive=false&name=Fastlønn&fields=id,name AND GET /salary/type?isInactive=false&name=Bonus&fields=id,name AND GET /salary/type?isInactive=false&name=Trekk i lønn for ferie&fields=id,name AND GET /employee/employment?employeeId=X&fields=id,startDate,division",
            "STEP 2 — Use setup_employee_for_payroll tool with: employee_id, date_of_birth (use '1990-01-15' if not known), start_date, annual_salary (base salary × 12). This handles the ENTIRE prerequisite chain (dateOfBirth, employment, details, division) in one call.",
            "STEP 3 — POST /salary/transaction?generateTaxDeduction=true with 3 specifications: Fastlønn + Bonus + Trekk i lønn for ferie - fastlønn. EXACT body: {date:'YYYY-MM-DD', year:YYYY, month:M, payslips:[{employee:{id:EMP_ID}, date:'YYYY-MM-DD', year:YYYY, month:M, specifications:[{salaryType:{id:FASTLONN_ID}, rate:BASE_AMT, count:1, amount:BASE_AMT}, {salaryType:{id:BONUS_ID}, rate:BONUS_AMT, count:1, amount:BONUS_AMT}, {salaryType:{id:TREKK_FERIE_ID}, rate:BASE_AMT, count:-1, amount:-BASE_AMT}]}]}",
        ],
        "key_lessons": [
            "PREFERRED: Use setup_employee_for_payroll tool for steps 2-7 — it handles dateOfBirth, employment, details, division creation and linking automatically.",
            "GET /salary/type: use fields=id,name ONLY. 'code' does NOT exist. Find name='Fastlønn' for base salary, name='Bonus' for bonus, and name='Trekk i lønn for ferie - fastlønn' for the holiday deduction.",
            "CRITICAL: You MUST include 3 specifications: (1) Fastlønn with rate=BASE, count=1, amount=BASE, (2) Bonus with rate=BONUS, count=1, amount=BONUS, (3) 'Trekk i lønn for ferie - fastlønn' with rate=BASE, count=-1, amount=-BASE. The holiday deduction is the employee's monthly contribution to the holiday pay fund. Without it, the payslip is incomplete.",
            "ALWAYS include amount=rate*count on each salary specification. Salary type IDs vary per sandbox — discover via GET /salary/type.",
            "If setup_employee_for_payroll fails, fall back to manual steps (PUT dateOfBirth → POST employment → POST details → POST division → PUT link division).",
        ],
    },
    "task_13": {
        "description": "Register travel expense with per diem and costs, then deliver, approve, and create vouchers",
        "golden_path": [
            "GET /employee?email=X&fields=id,firstName,lastName,email (find the employee)",
            "Use the create_travel_expense tool with: employee_id, title, departure_date, return_date, destination, per_diem_days, per_diem_rate, costs (list of {description, amount, date}). The tool handles the FULL lifecycle: create → deliver → approve → create vouchers.",
        ],
        "key_lessons": [
            "PREFERRED: Use create_travel_expense tool. You MUST pass ALL required parameters. Example call:\n"
            "create_travel_expense(\n"
            "  employee_id=12345,\n"
            "  title='Kundebesøk Bergen',\n"
            "  departure_date='2026-03-18',\n"
            "  return_date='2026-03-19',\n"
            "  destination='Bergen',\n"
            "  per_diem_days=2,\n"
            "  per_diem_rate=800,\n"
            "  costs=[{description: 'Flybillett', amount: 5200, date: '2026-03-18'}, {description: 'Taxi', amount: 350, date: '2026-03-19'}]\n"
            ")",
            "Date calculation: For N-day trip, set departure_date = today minus N days, return_date = today minus 1 day. The trip should be in the past.",
            "CRITICAL: Do NOT skip parameters. The tool requires: employee_id, title, departure_date, return_date, destination, per_diem_days, per_diem_rate. Without these the expense will be incomplete and score 0.",
            "The tool automatically handles the full travel expense lifecycle: POST (create) → PUT /:deliver → PUT /:approve → PUT /:createVouchers. All 4 steps are needed for full score.",
            "If create_travel_expense fails, fall back to manual construction. Key field name rules: 'title' on root (NOT in travelDetails), 'costCategory' (NOT 'category'), 'comments' (NOT 'description'), 'amountCurrencyIncVat' (NOT 'amount'), 'count' for days (NOT 'countDays'). After manual POST, you MUST also call PUT /:deliver, PUT /:approve, and PUT /:createVouchers?date=DEPARTURE_DATE.",
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
        "description": "Set fixed price on existing project and invoice partial amount (e.g., 50% milestone)",
        "golden_path": [
            "GET /customer?organizationNumber=X (find customer)",
            "GET /employee?email=X (find project manager)",
            "CRITICAL: GET /project?name=PROJECT_NAME — search for the EXISTING project first! The competition pre-creates it.",
            "IF project found: PUT /project/{id} with {id, version, isFixedPrice:true, fixedprice:TOTAL_AMOUNT, customer:{id}, projectManager:{id}}",
            "IF project NOT found: POST /project with {name, customer:{id}, projectManager:{id}, startDate:today, isFixedPrice:true, fixedprice:TOTAL_AMOUNT}",
            "POST /invoice with inline order: {invoiceDate:today, invoiceDueDate:today+30, customer:{id}, orders:[{customer:{id}, project:{id}, orderDate:today, deliveryDate:today, orderLines:[{description:'Milestone X%', count:1, unitPriceExcludingVatCurrency:PARTIAL_AMOUNT, vatType:{id:3}}]}]}",
        ],
        "key_lessons": [
            "CRITICAL: The project usually already EXISTS in the competition sandbox — ALWAYS search by name first with GET /project?name=X. If found, UPDATE it with PUT /project/{id} to set isFixedPrice and fixedprice. Creating a duplicate project will fail the check.",
            "Calculate partial amount: e.g., 75% of fixed price = fixedPrice * 0.75, 25% = fixedPrice * 0.25",
            "The project's fixedprice field stores the total contract value",
            "The bank account auto-fix handles account 1920 automatically when creating the invoice — do NOT manually PUT the bank account (wastes a write call and reduces efficiency score)",
            "Use POST /invoice with inline orders for efficiency (1 write instead of POST /order + PUT /:invoice = 2 writes)",
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
            "Do NOT set bankAccountNumber on account 1920 — it is NOT needed for voucher posting. Only invoice creation needs it, and this task has no invoice. Every unnecessary write call reduces the efficiency score.",
            "EXACT voucher body (4 writes total, 0 errors expected): {date: 'YYYY-MM-DD', description: '...', postings: [{date: 'YYYY-MM-DD', description: '...', account: {id: EXPENSE_ACCT_ID}, amountGross: AMOUNT, amountGrossCurrency: AMOUNT, row: 1, freeAccountingDimension1: {id: DIMENSION_VALUE_ID}}, {date: 'YYYY-MM-DD', description: '...', account: {id: BANK_ACCT_ID}, amountGross: -AMOUNT, amountGrossCurrency: -AMOUNT, row: 2}]}",
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
            "Compare the aggregated results: for each cost account (5000-7999), compute Feb total - Jan total. 'Kostnadskonto' includes ALL cost accounts 5000-7999: salary (5000-5999), operating expenses (6000-6999), and other costs (7000-7999).",
            "GET /ledger/account?id=X,Y,Z (get account names for the top 3 account IDs by largest increase)",
            "For EACH of the 3 accounts, do these steps SEQUENTIALLY (one at a time, not parallel):",
            "  (a) POST /activity with {name: ACCOUNT_NAME, activityType: 'GENERAL_ACTIVITY'}",
            "  (b) POST /project with {name: ACCOUNT_NAME, description: 'Kostnadsanalyse: Økning AMOUNT kr fra januar til februar 2026', projectManager:{id}, isInternal:true, startDate:today}",
            "  (c) POST /project/projectActivity with {project:{id:PROJECT_ID}, activity:{id:NEW_ACTIVITY_ID}}",
            "  THEN proceed to the next account.",
        ],
        "key_lessons": [
            "CRITICAL: The analyze_expense_changes tool defaults to 5000-7999 which is CORRECT. Do NOT override with 6000-7999. Salary account 5000 IS a kostnadskonto. Excluding it gives wrong top 3 and loses a check.",
            "CRITICAL: Activity names must MATCH the project name exactly — use just the account name (e.g., 'Bilgodtgjørelse oppgavepliktig'), NOT 'Kostnadsanalyse - X'.",
            "CRITICAL: Include a description on each project with the analysis result: the increase amount and month comparison. E.g., 'Kostnadsanalyse: Økning 7 000 kr fra januar til februar 2026'.",
            "CRITICAL: Use activityType='GENERAL_ACTIVITY' (not PROJECT_GENERAL_ACTIVITY). Then EXPLICITLY link each activity to its project via POST /project/projectActivity. PROJECT_GENERAL_ACTIVITY auto-links to ALL projects causing 409 conflicts.",
            "CRITICAL: GET /ledger/postingByDate does NOT support the fields parameter — always returns 422 if you include it. Omit fields entirely.",
            "CRITICAL: POST /project REQUIRES projectManager field — always do GET /token/session/>whoAmI FIRST to get employee ID.",
            "POST /project also REQUIRES startDate — use today's date.",
            "Create activities, projects, and link them SEQUENTIALLY (not parallel) — parallel creation causes 409 'Duplicate entry' race conditions.",
            "dateFrom is inclusive, dateTo is EXCLUSIVE (so dateTo=2026-02-01 covers all of January).",
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
        "description": "Register supplier invoice from attached PDF (leverandørfaktura)",
        "golden_path": [
            "READ the attached PDF — extract ALL fields: supplier name, org number, address, invoice number (INV-XXXX-NNNN), invoice date, due date, gross amount (incl VAT), expense account (Konto: XXXX), bank account number",
            "GET /supplier?organizationNumber=X (check if supplier already exists)",
            "If not found: POST /supplier with {name, organizationNumber, postalAddress: {addressLine1, postalCode, city, country: {id: 161}}, bankAccountPresentation: [{bban: 'BANK_ACCOUNT_NUMBER'}]}",
            "GET /ledger/account?number=XXXX,2400 (find expense account + AP account IDs)",
            "",
            "CRITICAL — Search for a pre-existing unposted voucher that may be ready for processing:",
            "GET /ledger/voucher?dateFrom=2020-01-01&dateTo=2030-12-31&count=50&sorting=-tempNumber (look for vouchers with tempNumber > 0, which are unposted/draft)",
            "If you find a voucher with 0 postings (empty postings array): this is likely the competition's pre-created voucher. Use PUT /supplierInvoice/voucher/{id}/postings on it:",
            "  PUT /supplierInvoice/voucher/{id}/postings with body: [{posting: {account:{id:EXPENSE_ACCT_ID}, supplier:{id:SUPPLIER_ID}, amountGross:GROSS_AMOUNT, amountGrossCurrency:GROSS_AMOUNT, vatType:{id:1}, row:1, date:INVOICE_DATE, description:DESCRIPTION}}]",
            "  This creates both the debit postings AND the SupplierInvoice entity (the endpoint returns ResponseWrapperSupplierInvoice).",
            "",
            "If no unposted voucher found: use create_supplier_invoice tool as fallback (creates a Leverandørfaktura voucher).",
        ],
        "key_lessons": [
            "CRITICAL: The competition sandbox may have a pre-created unposted voucher (from document import). ALWAYS search for it first with GET /ledger/voucher and look for vouchers with empty postings array. PUT /supplierInvoice/voucher/{id}/postings only works on vouchers WITHOUT existing postings.",
            "PUT /supplierInvoice/voucher/{id}/postings body format is [{posting: {account, supplier, amountGross, amountGrossCurrency, vatType, row, date, description}}] — note the 'posting' wrapper object (OrderLinePosting schema).",
            "POST /supplier body uses bankAccountPresentation: [{bban: '12345678901'}] — NOT bankAccountNumber.",
            "Include the supplier's address: parse street (addressLine1), postal code, and city from the PDF. country: {id: 161} is Norway.",
            "POST /incomingInvoice returns 403 (no permission). Do not waste calls on it.",
            "vatType:{id:1} is 'Fradrag inngående avgift, høy sats' (25% incoming VAT deduction).",
            "Fallback: create_supplier_invoice tool creates a correct Leverandørfaktura voucher but may not create the SupplierInvoice entity.",
        ],
    },
    "task_21": {
        "description": "Employee onboarding from offer letter (tilbudsbrev) PDF — no personnummer, no email, no bank account, no STYRK code in the PDF",
        "golden_path": [
            "READ the offer letter PDF carefully — extract: firstName, lastName, dateOfBirth (DD.MM.YYYY→YYYY-MM-DD), position title (stillingen), department name (avdeling), startDate (tiltredelse), employmentForm (ansettelsesform), percentageOfFullTimeEquivalent (stillingsprosent), annualSalary (årslønn), hoursPerDay (arbeidstid, e.g. 7.5 or 6.0)",
            "NOTE: Offer letters do NOT contain: personnummer, email, bankAccountNumber, or STYRK code. Do NOT search for these.",
            "GET /department?name=X (find or create). If creating: POST /department with name AND departmentNumber='1'.",
            "Occupation code: Search by POSITION TITLE from the PDF: GET /employee/employment/occupationCode?nameNO=POSITION_TITLE. Pick the first match. Limit to 2 API calls. Common titles: Logistikksjef→1235, Regnskapssjef→1213, Seniorutvikler→2512, HR-rådgiver→2512, Prosjektleder→1213.",
            "POST /employee with: firstName, lastName, dateOfBirth, department={id}. The validator auto-generates email (firstName.lastName@company.no) and sets userType=STANDARD.",
            "POST /employee/employment with: employee={id}, startDate, taxDeductionCode='loennFraHovedarbeidsgiver', employmentDetails=[{date, employmentType='ORDINARY', employmentForm='PERMANENT', remunerationType='MONTHLY_WAGE', workingHoursScheme='NOT_SHIFT', occupationCode={id}, percentageOfFullTimeEquivalent, annualSalary}]",
            "MANDATORY FINAL STEP — POST /employee/standardTime with: employee={id}, fromDate=startDate, hoursPerDay=HOURS_FROM_PDF. Use the EXACT value from the PDF (e.g., 7.5 or 6.0). Do NOT multiply by percentage. Do NOT skip this step!",
        ],
        "key_lessons": [
            "CRITICAL: The offer letter has NO personnummer, NO email, NO bank account, NO STYRK code. Do NOT waste API calls looking for these. The validator auto-generates email and userType.",
            "CRITICAL: You MUST call POST /employee/standardTime as the LAST step with the hoursPerDay value from the PDF. This is a scored check worth ~1.5 points.",
            "CRITICAL: departmentNumber MUST be set when creating a department. Use '1' if not specified in PDF. Missing departmentNumber loses scoring points.",
            "Occupation code: Search by position TITLE (not code number). Example: for 'Logistikksjef', search ?nameNO=Logistikksjef. The API matches case-insensitively.",
            "The position title is in the sentence 'stillingen som X i avdeling Y' (NO), 'le poste de X' (FR), 'die Stelle als X' (DE), 'the position of X' (EN), 'o cargo de X' (PT).",
            "Include employmentDetails INLINE in POST /employee/employment to save API calls.",
        ],
    },
    "task_23": {
        "description": "Reconcile bank statement (CSV) against open invoices and supplier payments, then create bank reconciliation",
        "golden_path": [
            "STEP 0 — Parse CSV and do parallel lookups:",
            "  parse_structured_data(format='ssv') on the CSV file to get structured rows",
            "  GET /invoice?invoiceDateFrom=2020-01-01&invoiceDateTo=2030-12-31&fields=id,invoiceNumber,amount,amountOutstanding,customer(*)",
            "  GET /invoice/paymentType?fields=id,description (find 'Betalt til bank' ID)",
            "  GET /ledger/account?number=1920&fields=id,number",
            "  GET /ledger/accountingPeriod?count=12&fields=id,start,end",
            "",
            "STEP 1 — Import the bank statement into Tripletex (CRITICAL — enables reconciliation matching):",
            "  Use the import_bank_statement tool with the RAW CSV text, bank_account_id (1920 account ID), from_date, to_date.",
            "  The tool converts to Danske Bank format and uploads it. This creates bank statement transactions.",
            "",
            "STEP 2 — Register customer payments (incoming CSV rows with 'Faktura NNNN' reference):",
            "  For each: PUT /invoice/{id}/:payment?paymentDate=DATE&paidAmount=AMOUNT&paymentTypeId=BANK_TYPE_ID",
            "  For partial payments: use the CSV amount (less than full invoice amount)",
            "",
            "STEP 3 — Register supplier payments and misc entries as vouchers:",
            "  For supplier payments: GET /supplier?name=X, then POST /ledger/voucher (debit 2400 with supplier ref, credit 1920)",
            "  For fees (Bankgebyr): POST /ledger/voucher (debit 7770, credit 1920)",
            "  For interest income (Inn column): POST /ledger/voucher (debit 1920, credit 8050)",
            "  For interest expense (Ut column): POST /ledger/voucher (debit 8150, credit 1920)",
            "",
            "STEP 4 — Create bank reconciliation and match (CRITICAL — worth 80% of points):",
            "  Find the accounting period covering the CSV dates from the periods fetched in step 0",
            "  POST /bank/reconciliation with: {account:{id:BANK_1920_ID}, accountingPeriod:{id:PERIOD_ID}, type:'MANUAL'}",
            "  PUT /bank/reconciliation/match/:suggest?bankReconciliationId=RECON_ID (auto-match imported transactions to ledger postings)",
            "  If :suggest returns 0 matches, try POST /bank/reconciliation/match to create manual matches between bank transactions and postings.",
        ],
        "key_lessons": [
            "CRITICAL: You MUST (1) import the bank statement via import_bank_statement tool, AND (2) create a bank reconciliation via POST /bank/reconciliation, AND (3) match entries. Without these, the reconciliation check fails.",
            "CRITICAL: import_bank_statement MUST be called BEFORE creating the reconciliation. It converts the CSV to Danske Bank format and uploads it, creating bank transactions that can be matched.",
            "GET /ledger/accountingPeriod returns periods with id, start, end. Pick the period that covers the CSV dates.",
            "After creating reconciliation, call PUT /bank/reconciliation/match/:suggest?bankReconciliationId=ID to auto-match transactions.",
            "CRITICAL: paymentTypeId=0 causes HTTP 500. ALWAYS use the ID from GET /invoice/paymentType where description='Betalt til bank'.",
            "Voucher postings on account 2400 MUST include supplier:{id:X}. Without it → 422.",
            "PUT /invoice/{id}/:payment requires QUERY params — paymentDate, paidAmount, paymentTypeId. NOT JSON body.",
            "CSV columns: Dato, Forklaring, Inn (incoming), Ut (outgoing), Saldo. Match invoices by 'Faktura NNNN' reference.",
            "Interest/fees direction matters: check whether the amount is in the Inn or Ut column. Renteinntekter in Inn=income(credit 8050), in Ut=expense(debit 8150). Bankgebyr in Ut=expense(debit 7770).",
        ],
    },
    "task_19": {
        "description": "Create employee from PDF employment contract (with national ID, occupation code, salary details)",
        "golden_path": [
            "READ the attached PDF carefully — extract EVERY field: firstName, lastName, nationalIdentityNumber (11 digits, strip spaces/dots), dateOfBirth (convert DD.MM.YYYY to YYYY-MM-DD), email, bankAccountNumber (Bankkonto), department name, occupationCode (STYRK code, usually 4 digits), annualSalary, percentageOfFullTimeEquivalent, startDate, standard working hours per day",
            "GET /department?name=X (find or create the department). If creating, ALWAYS set departmentNumber (use '1' or a short code like '2', '3')",
            "Occupation code lookup: GET /employee/employment/occupationCode?code=XXXX (4-digit STYRK code). If 0 results, search by position title: GET /employee/employment/occupationCode?nameNO=POSITION_TITLE. Pick the best match. Limit to 2 API calls max.",
            "POST /employee with: firstName, lastName, nationalIdentityNumber, dateOfBirth, email (if in PDF, otherwise the validator generates one), bankAccountNumber (from PDF Bankkonto field), department={id}. The validator auto-sets userType and email.",
            "POST /employee/employment with: employee={id}, startDate, taxDeductionCode='loennFraHovedarbeidsgiver', employmentDetails=[{date, employmentType='ORDINARY', employmentForm='PERMANENT', remunerationType='MONTHLY_WAGE', workingHoursScheme='NOT_SHIFT', occupationCode={id}, percentageOfFullTimeEquivalent, annualSalary}]",
            "MANDATORY FINAL STEP — POST /employee/standardTime with: employee={id}, fromDate=startDate, hoursPerDay=HOURS_FROM_PDF. Use the EXACT value from the PDF (typically 7.5). Do NOT skip this step — it is a scored check!",
        ],
        "key_lessons": [
            "CRITICAL: Extract and set EVERY field from the PDF. Every field you skip costs scoring points. Never drop a field due to a validation error — fix the VALUE instead.",
            "CRITICAL: bankAccountNumber (Bankkonto in the PDF) MUST be included in POST /employee body. This field is checked by the competition and costs points if missing. Extract the number exactly as shown in the PDF.",
            "CRITICAL: You MUST call POST /employee/standardTime as the LAST step. Without it you lose a scored check. Use hoursPerDay from the PDF (e.g., 7.5). Do NOT multiply by percentage — use the raw value from the contract.",
            "nationalIdentityNumber: Must be exactly 11 digits (DDMMYYXXXCC). Strip spaces, dots, dashes before sending. The validator checks format and warns if invalid.",
            "occupationCode: STYRK-08 (4-digit) ≠ STYRK-98 (7-digit). Some codes like 3323, 3512, 4110 have NO 7-digit equivalent with that prefix. Strategy: (1) Try ?code=XXXX first. (2) If 0 results, immediately search by position title: ?nameNO=POSITION_TITLE_FROM_PDF. (3) Pick the first result. Do NOT waste more than 2 API calls on this — pick the best available match. Common mappings: 3323→search 'INNKJØPSMEDARBEIDER' or 'SALGSMEDARBEIDER', 4110→search 'KONTORMEDARBEIDER', 3512→search 'IKT' or 'DATATEKNIKER'.",
            "Include employmentDetails INLINE in POST /employee/employment to save an API call",
            "If department doesn't exist, create it with POST /department. ALWAYS include departmentNumber (e.g. '1') — empty departmentNumber loses scoring points",
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
            "STEP 1 — Create project with budget, then add ALL employees as participants:",
            "  POST /project with: {name, projectManager:{id}, customer:{id}, startDate:'YYYY-MM-DD' (today), isInternal:false, isFixedPrice:true, fixedprice:BUDGET_AMOUNT}",
            "  NOTE: The budget amount goes in 'fixedprice' field (NOT 'budget' — that field does NOT exist!)",
            "  THEN: POST /project/participant for EACH additional employee (consultant, etc.):",
            "  POST /project/participant with: {project:{id:PROJECT_ID}, employee:{id:EMP2_ID}}",
            "  The project manager is automatically a participant. Add ALL other mentioned employees as participants.",
            "",
            "STEP 1b — Set project hourly rate (MUST happen BEFORE any timesheet entries!):",
            "  NEVER use POST /project/hourlyRates — it ALWAYS returns 409 Duplicate.",
            "  (a) GET /project/hourlyRates?projectId=PROJECT_ID → returns the auto-created entry with {id, version}",
            "  (b) PUT /project/hourlyRates/{id} with: {id:ENTRY_ID, version:VERSION, project:{id:PROJECT_ID}, startDate:'TODAY', hourlyRateModel:'TYPE_FIXED_HOURLY_RATE', fixedRate:BUDGET/TOTAL_HOURS}",
            "  Calculate hourly rate = budget / (hours_employee_1 + hours_employee_2).",
            "  THIS STEP IS MANDATORY. Without it, all timesheet entries will have hourlyRate=0 and 3 checks will fail.",
            "",
            "STEP 2 — Find activities for the project, then log timesheet hours (AFTER setting hourly rate!):",
            "  GET /activity/>forTimeSheet?projectId=PROJECT_ID&employeeId=EMPLOYEE_ID&date=TODAY",
            "  This returns project-specific activities. Use 'Fakturerbart arbeid' (billable work) activity ID.",
            "  POST /timesheet/entry {employee:{id:EMP1_ID}, project:{id:PROJECT_ID}, activity:{id:ACTIVITY_ID}, date:TODAY, hours:HOURS_1}",
            "  WAIT for the first entry to succeed, THEN send the second:",
            "  POST /timesheet/entry {employee:{id:EMP2_ID}, project:{id:PROJECT_ID}, activity:{id:ACTIVITY_ID}, date:TOMORROW, hours:HOURS_2}",
            "  CRITICAL: Send timesheet entries ONE AT A TIME (sequentially, not in parallel) AND use DIFFERENT dates!",
            "  Using the same date causes 409 Duplicate Entry errors even for different employees (API race condition).",
            "  All dates must be >= project startDate — cannot register hours before the project starts.",
            "",
            "STEP 3 — Register supplier cost as BOTH a project order line AND a ledger voucher:",
            "  First find expense account and AP account:",
            "  GET /ledger/account?number=4300,2400&fields=id,number,name",
            "",
            "  3a) POST /project/orderline to track cost in the project:",
            "    {project:{id:PROJECT_ID}, description:'Supplier cost SUPPLIER_NAME', unitCostCurrency:COST_AMOUNT, count:1, date:'TODAY', vendor:{id:SUPPLIER_ID}}",
            "",
            "  3b) POST /ledger/voucher?sendToLedger=true for the accounting entry:",
            "    {date:'TODAY', description:'Supplier cost SUPPLIER_NAME - PROJECT_NAME', postings:[",
            "      {row:1, account:{id:EXPENSE_ACCT_ID}, supplier:{id:SUPPLIER_ID}, project:{id:PROJECT_ID}, amountGross:COST_AMOUNT, amountGrossCurrency:COST_AMOUNT, description:'Supplier cost SUPPLIER_NAME'},",
            "      {row:2, account:{id:AP_2400_ID}, supplier:{id:SUPPLIER_ID}, amountGross:-COST_AMOUNT, amountGrossCurrency:-COST_AMOUNT, description:'Accounts payable SUPPLIER_NAME'}",
            "    ]}",
            "  IMPORTANT: Do NOT set vatType on the supplier cost posting — the stated cost amount is the FULL expense amount.",
            "",
            "STEP 4 — Create customer invoice for the project (bank account 1920 is auto-configured):",
            "  POST /invoice?sendToCustomer=false with body: {invoiceDate:'TODAY', invoiceDueDate:'TODAY+30days', customer:{id:CUST_ID}, orders:[{customer:{id:CUST_ID}, project:{id:PROJECT_ID}, orderDate:'TODAY', deliveryDate:'TODAY', orderLines:[{description:'Project services', count:1, unitPriceExcludingVatCurrency:BUDGET_AMOUNT, vatType:{id:3}}]}]}",
            "  CRITICAL: invoiceDueDate is REQUIRED — set it to ~30 days after invoiceDate. Omitting it causes 422.",
            "  The invoice amount should equal the project budget/fixedprice amount.",
        ],
        "key_lessons": [
            "CRITICAL ORDERING: Steps MUST execute in this exact order: create project → add participants → SET HOURLY RATE (GET+PUT) → THEN log timesheet hours → supplier cost → invoice. Timesheet entries created BEFORE the hourly rate is set will have hourlyRate=0 permanently.",
            "NEVER use POST /project/hourlyRates — it ALWAYS returns 409. You MUST: (1) GET /project/hourlyRates?projectId=X to find the auto-created entry, (2) PUT /project/hourlyRates/{id} with fixedRate=BUDGET/TOTAL_HOURS. Skipping this loses 3 competition checks.",
            "POST /project does NOT accept 'budget' field — use 'fixedprice' + 'isFixedPrice:true' instead",
            "CRITICAL: After creating the project, add ALL non-PM employees as project participants via POST /project/participant {project:{id}, employee:{id}}. The PM is auto-added but consultants/other employees are NOT.",
            "For timesheet: first call GET /activity/>forTimeSheet?projectId=X to find valid activities for the project",
            "Do NOT use GET /activity?isGeneral=true — those general activities may NOT work for the specific project",
            "CRITICAL: Log hours for EVERY employee mentioned in the prompt — each gets their own timesheet entry with the exact hours specified",
            "Use DIFFERENT dates for each employee's timesheet entry to avoid 409 Duplicate Entry conflicts",
            "Timesheet dates must be >= project startDate — cannot register hours before the project starts",
            "Supplier cost: use POST /ledger/voucher with supplier+project linked in the expense posting (debit expense/credit AP 2400)",
            "CRITICAL: Do NOT use vatType on the supplier cost posting. The stated cost IS the full expense amount. Using vatType:{id:1} makes account 4300 show only 80% of the stated cost (the rest goes to input VAT), which is wrong.",
            "IMPORTANT: For supplier cost expense account, prefer account 4300 (Innkjøp). If 4300 doesn't exist, look for any account in 4000-4999 range or use 6900",
            "IMPORTANT: Include project:{id} reference in the expense posting to link the cost to the project",
            "POST /invoice REQUIRES invoiceDueDate — set to invoiceDate + 30 days",
            "vatType:{id:3} = 'Utgående avgift, høy sats' (25% output VAT) — standard for Norwegian invoices",
            "The invoice order line amount should be the project budget (fixedprice) amount, EXCLUDING VAT",
            "After completing all steps, verify the project exists with customer linked, timesheet hours are correct per employee, and the invoice was created",
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
            "Step 6a: BEFORE posting prepaid reversal — check if 1700 still has a balance:",
            "  GET /balanceSheet?dateFrom=2025-01-01&dateTo=2026-01-01&accountNumberFrom=1700&accountNumberTo=1701",
            "  Look at the balanceOut for account 1700. If balanceOut is 0 or near-zero, the prepaid has ALREADY been fully periodized by the system — do NOT post a reversal (skip to step 7). Only reverse the REMAINING balance (balanceOut value).",
            "  CRITICAL: The competition sandbox auto-periodizes prepaid expenses monthly throughout 2025. Posting a FULL reversal when the balance is already 0 will DOUBLE-COUNT the expense and make the tax calculation wrong too.",
            "Step 6b: If account 1700 has a remaining balance > 0:",
            "  GET /ledger/posting?accountNumberFrom=1700&accountNumberTo=1700&dateFrom=2025-01-01&dateTo=2025-12-31&count=100 — find the paired expense account (e.g., 6300). If no postings found, default to 6300.",
            "  POST /ledger/voucher?sendToLedger=true for prepaid reversal using the REMAINING BALANCE (not the original amount from the prompt). Body: {date: '2025-12-31', description: 'Oppløsning forskuddsbetalte kostnader 2025', postings: [{account: {id: PAIRED_EXPENSE_ACCT_ID}, amountGross: REMAINING_BALANCE, amountGrossCurrency: REMAINING_BALANCE, row: 1}, {account: {id: ACCT_1700_ID}, amountGross: -REMAINING_BALANCE, amountGrossCurrency: -REMAINING_BALANCE, row: 2}]}",
            "Step 7: GET /balanceSheet?dateFrom=2025-01-01&dateTo=2026-01-01&accountNumberFrom=3000&accountNumberTo=8700 — returns per-account balances for ALL P&L accounts including the new depreciation and prepaid postings. Do NOT use the 'fields' parameter — it causes 400 errors (BalanceSheetAccountDTO only supports: account, balanceIn, balanceChange, balanceOut, startDate, endDate). Response: {values: [{account: {id, number, name}, balanceIn, balanceChange, balanceOut}, ...]}.",
            "Step 8: Calculate taxable profit from balanceSheet response: Sum all 'balanceChange' values for accounts 3000-8699 (EXCLUDE 8700 if it appears — that's the tax expense account you haven't populated yet). Revenue accounts (3xxx) show NEGATIVE balanceChange (credit), expense accounts (4xxx-8xxx) show POSITIVE balanceChange (debit). Taxable profit = -1 * SUM(all balanceChange values). Tax = taxable_profit * 0.22. Round to 2 decimals. IMPORTANT: Use balanceChange (NOT balanceOut) since we want only 2025 P&L activity, not cumulative balance.",
            "Step 9: POST /ledger/voucher?sendToLedger=true for tax provision. Body: {date: '2025-12-31', description: 'Skatteavsetning 2025 (22%)', postings: [{account: {id: ACCT_8700_ID}, amountGross: TAX_AMOUNT, amountGrossCurrency: TAX_AMOUNT, row: 1}, {account: {id: ACCT_2920_ID}, amountGross: -TAX_AMOUNT, amountGrossCurrency: -TAX_AMOUNT, row: 2}]}",
        ],
        "key_lessons": [
            "CRITICAL: EACH depreciation is a SEPARATE voucher (the prompt says this explicitly). You typically need depreciation vouchers + 1 prepaid + 1 tax = total vouchers.",
            "CRITICAL: Use calculate_accounting(operation='depreciation', cost=X, useful_life_years=Y) for EACH asset instead of manual math. It handles rounding correctly.",
            "CRITICAL: For the tax calculation, you MUST use GET /balanceSheet (NOT /ledger/postingByDate or /ledger/posting). The balanceSheet endpoint is the ONLY reliable way to get P&L totals. Do NOT fall back to aggregating individual postings — this gives wrong results on fresh accounts.",
            "CRITICAL: The GET /balanceSheet call MUST happen AFTER all depreciation and prepaid vouchers are posted (steps 5-6). This ensures the balanceChange values include your new postings. Query: GET /balanceSheet?dateFrom=2025-01-01&dateTo=2026-01-01&accountNumberFrom=3000&accountNumberTo=8700 (no 'fields' param!)",
            "CRITICAL: From the balanceSheet response, sum ALL 'balanceChange' values (exclude account 8700 if present). Revenue accounts (3xxx) have negative balanceChange (credit), expense accounts have positive balanceChange (debit). Taxable profit = -1 * sum. Tax = profit * 0.22.",
            "CRITICAL: Accounts 1209 (accumulated depreciation) and 8700 (tax expense) are NOT in the default chart of accounts. You MUST check if they exist and create them if missing. Also check 2920 — it usually exists but may have a different name.",
            "CRITICAL: BEFORE reversing prepaid expenses, CHECK the balance of account 1700 via balanceSheet. The competition sandbox auto-periodizes prepaid amounts monthly — by Dec 31 the balance is often ZERO. If balance is 0, SKIP the reversal entirely. If balance > 0, only reverse the REMAINING balance, NOT the full amount from the prompt.",
            "Do NOT hardcode 6300 for prepaid reversal. Look up the paired expense account from existing postings on 1700. Only default to 6300 if no postings found.",
            "Postings: row starts at 1 (never 0). amountGross = amountGrossCurrency. Positive = debit, negative = credit.",
            "Tax provision: Norwegian corporate tax rate is 22%. The GET /balanceSheet call AFTER steps 5-6 will include the depreciation and prepaid postings you just created.",
            "IMPORTANT: The balanceSheet dateTo is EXCLUSIVE. Use dateTo=2026-01-01 to include all of 2025.",
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
        if not task_type or confidence < 0.4:
            logger.debug(f"No confident task match (best={task_type}, conf={confidence:.2f})")
            return None

        # Check overrides
        override = PLAYBOOK_OVERRIDES.get(task_type)
        if override == "disabled":
            logger.info(f"Playbook for {task_type} is disabled via PLAYBOOK_OVERRIDES")
            return None

        playbook = self._playbooks.get(task_type)
        if not playbook:
            return None

        # Soft framing when confidence is low or playbook is experimental
        soft_framing = override == "experimental" or confidence < 0.6

        logger.info(
            f"Classified as {task_type} (confidence={confidence:.2f}, "
            f"framing={'soft' if soft_framing else 'confident'}), injecting playbook"
        )
        return self._format_playbook(playbook, soft=soft_framing)

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

    def _format_playbook(self, playbook: TaskPlaybook, soft: bool = False) -> str:
        """Format a playbook as injection text for the system prompt."""
        if soft:
            header = f"=== SUGGESTED APPROACH ({playbook.description}) ==="
            footer = (
                "This is a SUGGESTED approach — verify endpoints with search_api_spec "
                "and get_endpoint_detail before following. Adapt as needed."
            )
        else:
            header = f"=== CONTEXT FROM PREVIOUS RUNS ({playbook.description}) ==="
            footer = "Follow this flow closely, adapting to the specific prompt below."

        lines = [header, "", "RECOMMENDED API FLOW:"]
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
        lines.append(footer)
        lines.append("===")
        return "\n".join(lines)
