"""Pre-validates API calls against the OpenAPI spec before making HTTP requests.

Catches common errors (unknown fields, BETA endpoints, wrong enum values, read-only
fields) BEFORE hitting the real API. This saves API calls and avoids 4xx error penalties.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


class APIValidator:
    """Validates API calls against OpenAPI spec before making HTTP requests."""

    def __init__(self, spec: dict):
        self._paths: dict = spec.get("paths", {})
        self._schemas: dict = spec.get("components", {}).get("schemas", {})
        self._path_patterns: list[tuple[re.Pattern, str]] = []
        self._build_path_index()
        logger.info(
            f"APIValidator initialized with {len(self._paths)} paths, "
            f"{len(self._schemas)} schemas, {len(self._path_patterns)} patterns"
        )

    def _build_path_index(self):
        """Build regex patterns for matching parameterized paths like /employee/{id}."""
        for path in self._paths:
            if "{" in path:
                # Convert /employee/{id} → ^/employee/[^/]+$
                # Also handle :action suffixes like /order/{id}/:invoice
                pattern = re.sub(r"\{[^}]+\}", "[^/]+", path)
                self._path_patterns.append((re.compile(f"^{pattern}$"), path))

    def _resolve_path(self, actual_path: str) -> str | None:
        """Match an actual path (e.g. /employee/123) to a spec template (e.g. /employee/{id})."""
        # Direct match first (most common for POST to base paths)
        if actual_path in self._paths:
            return actual_path

        # Try regex patterns for parameterized paths
        for regex, spec_path in self._path_patterns:
            if regex.match(actual_path):
                return spec_path

        return None

    def validate(self, method: str, path: str, json_body: dict | None,
                 params: dict | None = None) -> list[str]:
        """Validate an API call against the spec. Returns list of warnings (empty = valid)."""
        warnings: list[str] = []

        # 0. Hard-learned rules (from competition run failures)
        self._check_hard_rules(method, path, json_body, params, warnings)
        if warnings:
            return warnings  # Hard rules are definitive — don't continue

        # 1. Resolve path
        spec_path = self._resolve_path(path)
        if spec_path is None:
            # Can't validate — path not in spec (might be valid, just unknown)
            return []

        path_data = self._paths[spec_path]
        method_lower = method.lower()

        # 2. Check method exists
        endpoint = path_data.get(method_lower)
        if not endpoint:
            available = [m.upper() for m in path_data if m in ("get", "post", "put", "delete", "patch")]
            warnings.append(
                f"Method {method.upper()} not available for {spec_path}. "
                f"Available: {', '.join(available)}"
            )
            return warnings

        # 3. Check BETA — warn but DON'T block (some BETA endpoints work on the competition proxy)
        summary = endpoint.get("summary", "")
        if summary.upper().startswith("[BETA]"):
            # Log as info, not as a blocking warning — let the agent try
            logger.info(f"Note: {method.upper()} {spec_path} is marked [BETA] — may return 403")
            # Don't add to warnings — allow the call through

        # 4. Validate json_body against schema (for POST/PUT)
        if json_body and method_lower in ("post", "put"):
            schema_ref = self._get_request_body_ref(endpoint)
            if schema_ref:
                schema_name = schema_ref.split("/")[-1]
                self._validate_body(json_body, schema_name, warnings)

        return warnings

    def _check_hard_rules(self, method: str, path: str, json_body: dict | None,
                          params: dict | None, warnings: list[str]):
        """Enforce rules learned from competition failures. These prevent known 4xx errors."""
        method_upper = method.upper()

        # Rule 1: /list endpoints require a raw JSON array, not {"values": [...]}
        if path.endswith("/list") and method_upper == "POST" and isinstance(json_body, dict):
            if "values" in json_body:
                warnings.append(
                    f"POST {path} requires a RAW JSON ARRAY as the request body, "
                    f"not an object with 'values'. Send [{{}}, ...] instead of "
                    f"{{\"values\": [...]}}. Fix: pass the array directly."
                )

        # Rule 2: Orders must have deliveryDate
        if json_body and method_upper == "POST":
            # Inline orders in POST /invoice
            if path == "/invoice" and isinstance(json_body.get("orders"), list):
                for i, order in enumerate(json_body["orders"]):
                    if isinstance(order, dict) and not order.get("deliveryDate"):
                        warnings.append(
                            f"orders[{i}].deliveryDate is missing. Orders REQUIRE "
                            f"deliveryDate — use today's date. Without it: 422 error."
                        )
            # Direct POST /order
            if path == "/order" and not json_body.get("deliveryDate"):
                warnings.append(
                    "deliveryDate is missing on order. Orders REQUIRE deliveryDate "
                    "— use today's date. Without it: 422 error."
                )

        # Rule 3: Voucher postings row=0 — auto-fixed in fix_postings_rows(), not blocked here

        # Rule 4: amountGross/amountGrossCurrency mismatch — auto-fixed, not blocked
        # Both are handled in the auto-fix pipeline (fix_postings_rows + _fix_amount_gross_currency)
        # called from agent_service before the HTTP call
        if json_body and method_upper in ("POST", "PUT"):
            self._fix_amount_gross_currency(json_body)

        # Rule 5: GET /ledger/postingByDate does NOT support fields parameter
        if method_upper == "GET" and "/ledger/postingByDate" in path and params:
            if "fields" in params:
                warnings.append(
                    "GET /ledger/postingByDate does NOT support the 'fields' parameter — "
                    "it always returns 422. Remove 'fields' from params."
                )

        # Rule 6: POST /project requires projectManager
        if method_upper == "POST" and path == "/project" and json_body:
            if not json_body.get("projectManager"):
                warnings.append(
                    "POST /project REQUIRES 'projectManager' field with employee {id}. "
                    "Get employee ID from GET /token/session/>whoAmI first."
                )
            if not json_body.get("startDate"):
                warnings.append(
                    "POST /project REQUIRES 'startDate'. Use today's date."
                )

        # Rule 7: PUT /invoice/{id}/:payment requires query params, NOT json body
        if method_upper == "PUT" and "/:payment" in path and json_body:
            # Common mistake: sending paidAmount/paymentDate/paymentTypeId as body
            body_keys = set(json_body.keys()) if isinstance(json_body, dict) else set()
            payment_params = body_keys & {"paidAmount", "paymentDate", "paymentTypeId"}
            if payment_params:
                warnings.append(
                    f"PUT /:payment requires paidAmount, paymentDate, paymentTypeId as "
                    f"QUERY PARAMETERS (params=), NOT in json_body. Found {payment_params} "
                    f"in body. Fix: move them to params dict."
                )

        # Rule 6: SupplierInvoiceDTO does NOT have amountOutstanding field
        if method_upper == "GET" and params:
            fields_val = params.get("fields", "")
            if isinstance(fields_val, str) and "amountOutstanding" in fields_val:
                if "/supplierInvoice" in path:
                    warnings.append(
                        "Invalid field 'amountOutstanding' on SupplierInvoiceDTO. "
                        "Use 'amount' instead. For outstanding balance, check the "
                        "voucher postings or use amount - amountPaid."
                    )

        # Rule 7: fields= parameter validation for travel expense endpoints
        if method_upper == "GET" and params:
            fields_val = params.get("fields", "")
            if isinstance(fields_val, str) and fields_val and fields_val != "*":
                fields_set = {f.strip() for f in fields_val.split(",")}
                # TravelExpenseRateDTO: has id, rate — NOT name, type, description
                if "/travelExpense/rate" in path and not path.endswith("Category"):
                    bad = fields_set & {"name", "type", "description"}
                    if bad:
                        warnings.append(
                            f"Invalid fields on TravelExpenseRateDTO: {bad}. "
                            f"Valid fields: id, rate. Use fields=id,rate"
                        )
                # TravelCostCategoryDTO: has id, description — NOT name
                if "/travelExpense/costCategory" in path:
                    bad = fields_set & {"name"}
                    if bad:
                        warnings.append(
                            f"Invalid field 'name' on TravelCostCategoryDTO. "
                            f"Use 'description' instead: fields=id,description"
                        )
                # TravelPaymentTypeDTO: has id, description — NOT name
                if "/travelExpense/paymentType" in path:
                    bad = fields_set & {"name"}
                    if bad:
                        warnings.append(
                            f"Invalid field 'name' on TravelPaymentTypeDTO. "
                            f"Use 'description' instead: fields=id,description"
                        )

    def _fix_amount_gross_currency(self, body: dict):
        """Auto-fix amountGross/amountGrossCurrency mismatch in postings.

        For NOK-only companies, these must always be equal. If one is set and not
        the other, copy it. If both are set but differ, trust amountGross.
        Recurses into 'postings' arrays.
        """
        postings = body.get("postings")
        if isinstance(postings, list):
            for posting in postings:
                if not isinstance(posting, dict):
                    continue
                gross = posting.get("amountGross")
                currency = posting.get("amountGrossCurrency")
                if gross is not None and currency is None:
                    posting["amountGrossCurrency"] = gross
                elif gross is None and currency is not None:
                    posting["amountGross"] = currency
                elif gross is not None and currency is not None and gross != currency:
                    posting["amountGrossCurrency"] = gross
                    logger.info(
                        f"Auto-fixed amountGrossCurrency {currency} → {gross} "
                        f"to match amountGross"
                    )

    def fix_postings_rows(self, method: str, path: str, json_body: dict | None) -> dict | None:
        """Auto-fix row=0 in voucher postings. Row 0 is system-reserved; renumber from 1."""
        if not json_body or method.upper() not in ("POST", "PUT"):
            return json_body
        if "/ledger/voucher" not in path:
            return json_body

        postings = json_body.get("postings")
        if not isinstance(postings, list):
            return json_body

        fixed = False
        for i, posting in enumerate(postings):
            if isinstance(posting, dict) and posting.get("row") == 0:
                posting["row"] = i + 1
                fixed = True
        if fixed:
            logger.info("Auto-fixed row=0 in voucher postings (renumbered from 1)")
        return json_body

    def strip_readonly_fields(self, method: str, path: str, json_body: dict | None) -> dict | None:
        """Remove read-only fields from the request body. Returns cleaned body."""
        if not json_body or method.lower() not in ("post", "put"):
            return json_body

        spec_path = self._resolve_path(path)
        if not spec_path:
            return json_body

        endpoint = self._paths.get(spec_path, {}).get(method.lower())
        if not endpoint:
            return json_body

        schema_ref = self._get_request_body_ref(endpoint)
        if not schema_ref:
            return json_body

        schema_name = schema_ref.split("/")[-1]
        return self._strip_readonly(json_body, schema_name)

    def _get_request_body_ref(self, endpoint: dict) -> str | None:
        """Extract the $ref from an endpoint's request body schema."""
        req_body = endpoint.get("requestBody", {})
        content = req_body.get("content", {})

        # Try both content type variants
        for ct in ("application/json; charset=utf-8", "application/json"):
            schema = content.get(ct, {}).get("schema", {})
            ref = schema.get("$ref")
            if ref:
                return ref

        return None

    def _validate_body(self, body: dict, schema_name: str, warnings: list[str], prefix: str = ""):
        """Validate a request body dict against a named schema."""
        schema = self._schemas.get(schema_name, {})
        properties = schema.get("properties", {})

        if not properties:
            return  # Schema not found or has no properties — skip validation

        for key, value in body.items():
            full_key = f"{prefix}{key}"

            if key not in properties:
                # Show a subset of valid fields to help the agent
                valid_writable = [
                    k for k, v in properties.items()
                    if not v.get("readOnly", False)
                ][:20]
                warnings.append(
                    f"Unknown field '{full_key}' on {schema_name}. "
                    f"Writable fields: {valid_writable}"
                )
                continue

            prop_def = properties[key]

            # Enum check
            if "enum" in prop_def and value is not None:
                if value not in prop_def["enum"]:
                    warnings.append(
                        f"Field '{full_key}': value '{value}' not in "
                        f"allowed values {prop_def['enum']}"
                    )

            # Type check (basic — don't be too strict)
            expected_type = prop_def.get("type")
            if expected_type and value is not None:
                if expected_type == "integer" and isinstance(value, str):
                    warnings.append(
                        f"Field '{full_key}': expected integer, got string '{value}'"
                    )
                elif expected_type == "boolean" and not isinstance(value, bool):
                    warnings.append(
                        f"Field '{full_key}': expected boolean, got {type(value).__name__}"
                    )

            # Recurse into nested objects (one level) if value is a dict and prop is a $ref
            if isinstance(value, dict) and "$ref" in prop_def:
                nested_schema = prop_def["$ref"].split("/")[-1]
                self._validate_body(value, nested_schema, warnings, prefix=f"{full_key}.")

            # Recurse into arrays of objects — validate each item against the items schema
            if isinstance(value, list) and prop_def.get("type") == "array":
                items_ref = prop_def.get("items", {}).get("$ref")
                if items_ref:
                    items_schema_name = items_ref.split("/")[-1]
                    for i, item in enumerate(value):
                        if isinstance(item, dict):
                            self._validate_body(
                                item, items_schema_name, warnings,
                                prefix=f"{full_key}[{i}]."
                            )

    def _strip_readonly(self, body: dict, schema_name: str) -> dict:
        """Strip read-only fields from the body dict."""
        schema = self._schemas.get(schema_name, {})
        properties = schema.get("properties", {})

        if not properties:
            return body

        cleaned = {}
        stripped = []
        for key, value in body.items():
            prop_def = properties.get(key, {})
            if prop_def.get("readOnly", False):
                stripped.append(key)
                continue
            # Recurse into arrays of objects
            if isinstance(value, list) and prop_def.get("type") == "array":
                items_ref = prop_def.get("items", {}).get("$ref")
                if items_ref:
                    items_schema_name = items_ref.split("/")[-1]
                    value = [
                        self._strip_readonly(item, items_schema_name)
                        if isinstance(item, dict) else item
                        for item in value
                    ]
            # Recurse into nested objects
            elif isinstance(value, dict) and "$ref" in prop_def:
                nested_schema = prop_def["$ref"].split("/")[-1]
                value = self._strip_readonly(value, nested_schema)
            cleaned[key] = value

        if stripped:
            logger.debug(f"Stripped read-only fields from {schema_name}: {stripped}")

        return cleaned
