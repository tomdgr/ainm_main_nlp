import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Resolve spec path: check /app/data/ (Docker) then local docs/
_SPEC_PATHS = [
    Path("/app/data/apispec_openapi.json"),
    Path(__file__).resolve().parents[2] / "docs" / "task_api_docs" / "apispec_openapi.json",
]


class OpenAPISpecSearcher:
    """Loads the Tripletex OpenAPI spec and provides keyword search."""

    def __init__(self):
        self._spec: dict = {}
        self._paths: dict = {}
        self._schemas: dict = {}
        self._index: list[dict] = []  # Pre-built search index

    def load(self, spec_path: str | Path | None = None):
        """Load the OpenAPI spec from disk and build a search index."""
        if spec_path is None:
            for p in _SPEC_PATHS:
                if p.exists():
                    spec_path = p
                    break
        if spec_path is None:
            logger.warning("OpenAPI spec not found at any default path")
            return

        logger.info(f"Loading OpenAPI spec from {spec_path}")
        with open(spec_path) as f:
            self._spec = json.load(f)

        self._paths = self._spec.get("paths", {})
        self._schemas = (
            self._spec.get("components", {}).get("schemas", {})
        )
        self._build_index()
        logger.info(f"Indexed {len(self._index)} endpoints from OpenAPI spec")

    def get_raw_spec(self) -> dict:
        """Return the raw OpenAPI spec dict for use by validators."""
        return self._spec

    @staticmethod
    def _is_beta(details: dict) -> bool:
        """Check if an endpoint is marked as [BETA] in its summary."""
        summary = details.get("summary", "")
        return summary.upper().startswith("[BETA]")

    _BETA_WHITELIST: set[str] = set()  # No longer used for filtering

    def _build_index(self):
        """Build a flat search index of all endpoints (including BETA — they may work)."""
        beta_count = 0
        for path, methods in self._paths.items():
            for method, details in methods.items():
                if method in ("get", "post", "put", "delete", "patch"):
                    if self._is_beta(details):
                        beta_count += 1
                        # Still index BETA endpoints — some work on the competition proxy
                    self._index.append({
                        "path": path,
                        "method": method.upper(),
                        "summary": details.get("summary", ""),
                        "tags": details.get("tags", []),
                        "operation_id": details.get("operationId", ""),
                        "details": details,
                    })
        if beta_count:
            logger.info(f"Skipped {beta_count} [BETA] endpoints (always return 403)")

    # Synonyms: expand query keywords to find more API matches
    _SYNONYMS: dict[str, set[str]] = {
        "create": {"post", "add", "register", "new", "opprett"},
        "register": {"create", "add", "book", "post"},
        "update": {"put", "modify", "change", "edit"},
        "delete": {"remove", "slett"},
        "reverse": {"annul", "void", "undo", "reverser"},
        "payment": {"betaling", "zahlung", "pago", "paiement"},
        "balance": {"saldo", "balanse", "saldobalanse"},
        "invoice": {"faktura", "rechnung", "factura", "fatura"},
        "employee": {"ansatt", "tilsett", "arbeitnehmer"},
        "customer": {"kunde", "client", "cliente"},
        "supplier": {"leverandør", "lieferant", "fournisseur", "proveedor"},
        "department": {"avdeling", "abteilung", "département"},
        "project": {"prosjekt", "projekt", "proyecto", "projet"},
        "voucher": {"bilag", "beleg", "écriture", "asiento"},
    }

    # Method keywords that indicate HTTP method preference
    _METHOD_KEYWORDS: dict[str, str] = {
        "create": "POST", "add": "POST", "register": "POST", "new": "POST",
        "post": "POST", "opprett": "POST",
        "update": "PUT", "modify": "PUT", "edit": "PUT", "put": "PUT",
        "delete": "DELETE", "remove": "DELETE",
        "get": "GET", "find": "GET", "search": "GET", "list": "GET", "fetch": "GET",
    }

    def search_endpoints(self, query: str, max_results: int = 12) -> str:
        """Search endpoints by keyword with weighted scoring. Returns formatted text of matches."""
        if not self._index:
            return "OpenAPI spec not loaded."

        query_lower = query.lower()
        raw_keywords = query_lower.split()

        # Detect method preference from query keywords
        preferred_method = None
        for kw in raw_keywords:
            if kw in self._METHOD_KEYWORDS:
                preferred_method = self._METHOD_KEYWORDS[kw]
                break

        # Expand keywords with synonyms
        expanded = set(raw_keywords)
        for kw in raw_keywords:
            if kw in self._SYNONYMS:
                expanded.update(self._SYNONYMS[kw])

        scored = []
        for entry in self._index:
            score = self._score_entry(entry, expanded, preferred_method)
            if score > 0:
                scored.append((score, entry))

        # Sort by score desc, then by path length asc (shorter = more relevant)
        scored.sort(key=lambda x: (-x[0], len(x[1]['path'])))
        results = scored[:max_results]

        if not results:
            return f"No endpoints found matching '{query}'."

        lines = [f"Found {len(results)} endpoints matching '{query}':\n"]
        for _, entry in results:
            lines.append(f"  {entry['method']} {entry['path']}")
            if entry["summary"]:
                lines.append(f"    Summary: {entry['summary']}")

            # Show parameters
            params = entry["details"].get("parameters", [])
            if params:
                param_names = [
                    f"{p.get('name')}({'required' if p.get('required') else 'optional'})"
                    for p in params[:10]
                ]
                lines.append(f"    Params: {', '.join(param_names)}")

            # Show request body schema ref
            req_body = entry["details"].get("requestBody", {})
            if req_body:
                content = req_body.get("content", {})
                json_schema = (
                    content.get("application/json; charset=utf-8", {}).get("schema", {})
                    or content.get("application/json", {}).get("schema", {})
                )
                ref = json_schema.get("$ref", "")
                if ref:
                    schema_name = ref.split("/")[-1]
                    lines.append(f"    Body schema: {schema_name}")

            lines.append("")

        return "\n".join(lines)

    def _score_entry(self, entry: dict, keywords: set[str], preferred_method: str | None) -> float:
        """Score an endpoint against expanded keywords with field weighting."""
        path = entry["path"].lower()
        summary = entry["summary"].lower()
        op_id = entry["operation_id"].lower()
        tags = " ".join(entry["tags"]).lower()
        method = entry["method"]

        # Split path into segments for exact matching
        raw_segments = path.strip("/").split("/")
        path_segments = set(raw_segments)
        # Also include action segments like :invoice, :payment, :reverse
        path_segments.update(seg.lstrip(":") for seg in raw_segments if seg.startswith(":"))

        score = 0.0
        for kw in keywords:
            # Exact path segment match (strongest signal)
            if kw in path_segments:
                score += 5.0
            # Substring in path
            elif kw in path:
                score += 3.0
            # In summary
            if kw in summary:
                score += 2.0
            # In operation_id or tags
            if kw in op_id or kw in tags:
                score += 1.0

        # Method preference bonus
        if preferred_method and method == preferred_method:
            score += 3.0

        return score

    def get_endpoint_details(self, path: str, method: str) -> str:
        """Get full details for a specific endpoint including body schema fields."""
        method_lower = method.lower()
        path_data = self._paths.get(path, {})
        details = path_data.get(method_lower)

        if not details:
            return f"Endpoint {method.upper()} {path} not found."

        # Note: BETA endpoints are no longer blocked — some work on the competition proxy

        lines = [f"{method.upper()} {path}", f"Summary: {details.get('summary', 'N/A')}", ""]

        # Parameters
        params = details.get("parameters", [])
        if params:
            lines.append("Parameters:")
            for p in params:
                req = "required" if p.get("required") else "optional"
                p_type = p.get("schema", {}).get("type", "unknown")
                lines.append(f"  - {p['name']} ({p_type}, {req}): {p.get('description', '')}")
            lines.append("")

        # Request body — try both content type variants
        req_body = details.get("requestBody", {})
        if req_body:
            content = req_body.get("content", {})
            json_schema = (
                content.get("application/json; charset=utf-8", {}).get("schema", {})
                or content.get("application/json", {}).get("schema", {})
            )
            if json_schema:
                lines.append("Request body:")
                self._format_schema(json_schema, lines, indent=2)
                lines.append("")

        # Response — try both content type variants
        responses = details.get("responses", {})
        for code, resp in responses.items():
            if code.startswith("2"):
                resp_content = resp.get("content", {})
                resp_schema = (
                    resp_content.get("application/json; charset=utf-8", {}).get("schema", {})
                    or resp_content.get("application/json", {}).get("schema", {})
                )
                if resp_schema:
                    lines.append(f"Response ({code}):")
                    self._format_schema(resp_schema, lines, indent=2)
                break

        return "\n".join(lines)

    def get_schema(self, schema_name: str) -> str:
        """Get a schema definition by name."""
        schema = self._schemas.get(schema_name)
        if not schema:
            return f"Schema '{schema_name}' not found."

        lines = [f"Schema: {schema_name}", ""]
        self._format_schema_object(schema, lines, indent=0)
        return "\n".join(lines)

    def _format_schema(self, schema: dict, lines: list, indent: int = 0):
        """Format a schema reference or inline schema."""
        prefix = " " * indent
        ref = schema.get("$ref")
        if ref:
            schema_name = ref.split("/")[-1]
            resolved = self._schemas.get(schema_name, {})
            lines.append(f"{prefix}Schema: {schema_name}")
            self._format_schema_object(resolved, lines, indent)
        else:
            self._format_schema_object(schema, lines, indent)

    def _format_schema_object(self, schema: dict, lines: list, indent: int = 0):
        """Format schema properties with enum values and expanded refs."""
        prefix = " " * indent
        required_fields = set(schema.get("required", []))
        properties = schema.get("properties", {})

        for prop_name, prop_def in properties.items():
            req_marker = " (REQUIRED)" if prop_name in required_fields else ""
            prop_type = prop_def.get("type", "")
            read_only = prop_def.get("readOnly", False)

            if read_only:
                continue  # Skip read-only fields for brevity

            # Handle refs in properties — expand one level to show key fields
            ref = prop_def.get("$ref")
            if ref:
                ref_name = ref.split("/")[-1]
                ref_schema = self._schemas.get(ref_name, {})
                ref_props = ref_schema.get("properties", {})
                # Show writable scalar fields of the referenced schema
                ref_fields = [
                    k for k, v in ref_props.items()
                    if not v.get("readOnly", False) and v.get("type") in ("string", "integer", "number", "boolean", None)
                ][:8]
                if ref_fields:
                    prop_type = f"object {{{'|'.join(ref_fields)}}}"
                else:
                    prop_type = f"ref:{ref_name}"

            # Handle arrays with $ref items — show writable fields of the item schema
            if prop_type == "array":
                items = prop_def.get("items", {})
                items_ref = items.get("$ref")
                if items_ref:
                    items_name = items_ref.split("/")[-1]
                    items_schema = self._schemas.get(items_name, {})
                    items_props = items_schema.get("properties", {})
                    # Show ALL writable fields — critical for nested schemas like Posting
                    # where important fields (freeAccountingDimension1) are far down
                    writable = [
                        k for k, v in items_props.items()
                        if not v.get("readOnly", False)
                    ]
                    if writable:
                        prop_type = f"array of {items_name} {{{'|'.join(writable)}}}"
                    else:
                        prop_type = f"array of {items_name}"

            # Show enum values inline
            enum_values = prop_def.get("enum")
            if enum_values:
                enum_str = " | ".join(str(v) for v in enum_values)
                prop_type = f"{prop_type} ({enum_str})"

            desc = prop_def.get("description", "")
            desc_short = desc[:80] + "..." if len(desc) > 80 else desc

            lines.append(f"{prefix}  - {prop_name}: {prop_type}{req_marker} {desc_short}")
