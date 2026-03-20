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

    def _build_index(self):
        """Build a flat search index of all endpoints."""
        for path, methods in self._paths.items():
            for method, details in methods.items():
                if method in ("get", "post", "put", "delete", "patch"):
                    self._index.append({
                        "path": path,
                        "method": method.upper(),
                        "summary": details.get("summary", ""),
                        "tags": details.get("tags", []),
                        "operation_id": details.get("operationId", ""),
                        "details": details,
                    })

    def search_endpoints(self, query: str, max_results: int = 8) -> str:
        """Search endpoints by keyword. Returns formatted text of matches."""
        if not self._index:
            return "OpenAPI spec not loaded."

        query_lower = query.lower()
        keywords = query_lower.split()

        scored = []
        for entry in self._index:
            searchable = f"{entry['path']} {entry['summary']} {' '.join(entry['tags'])} {entry['operation_id']}".lower()
            score = sum(1 for kw in keywords if kw in searchable)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
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
                json_schema = content.get("application/json", {}).get("schema", {})
                ref = json_schema.get("$ref", "")
                if ref:
                    schema_name = ref.split("/")[-1]
                    lines.append(f"    Body schema: {schema_name}")

            lines.append("")

        return "\n".join(lines)

    def get_endpoint_details(self, path: str, method: str) -> str:
        """Get full details for a specific endpoint including body schema fields."""
        method_lower = method.lower()
        path_data = self._paths.get(path, {})
        details = path_data.get(method_lower)

        if not details:
            return f"Endpoint {method.upper()} {path} not found."

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

        # Request body
        req_body = details.get("requestBody", {})
        if req_body:
            content = req_body.get("content", {})
            json_schema = content.get("application/json", {}).get("schema", {})
            lines.append("Request body:")
            self._format_schema(json_schema, lines, indent=2)
            lines.append("")

        # Response
        responses = details.get("responses", {})
        for code, resp in responses.items():
            if code.startswith("2"):
                resp_content = resp.get("content", {})
                resp_schema = resp_content.get("application/json", {}).get("schema", {})
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
        """Format schema properties."""
        prefix = " " * indent
        required_fields = set(schema.get("required", []))
        properties = schema.get("properties", {})

        for prop_name, prop_def in properties.items():
            req_marker = " (REQUIRED)" if prop_name in required_fields else ""
            prop_type = prop_def.get("type", "")
            read_only = prop_def.get("readOnly", False)

            if read_only:
                continue  # Skip read-only fields for brevity

            # Handle refs in properties
            ref = prop_def.get("$ref")
            if ref:
                ref_name = ref.split("/")[-1]
                prop_type = f"ref:{ref_name}"

            desc = prop_def.get("description", "")
            desc_short = desc[:80] + "..." if len(desc) > 80 else desc

            lines.append(f"{prefix}  - {prop_name}: {prop_type}{req_marker} {desc_short}")
