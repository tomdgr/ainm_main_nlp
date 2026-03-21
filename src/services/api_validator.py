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

    def validate(self, method: str, path: str, json_body: dict | None) -> list[str]:
        """Validate an API call against the spec. Returns list of warnings (empty = valid)."""
        warnings: list[str] = []

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

        # 3. Check BETA
        summary = endpoint.get("summary", "")
        if summary.upper().startswith("[BETA]"):
            warnings.append(
                f"Endpoint {method.upper()} {spec_path} is [BETA] and will return 403 Forbidden. "
                f"Find a non-beta alternative."
            )
            return warnings

        # 4. Validate json_body against schema (for POST/PUT)
        if json_body and method_lower in ("post", "put"):
            schema_ref = self._get_request_body_ref(endpoint)
            if schema_ref:
                schema_name = schema_ref.split("/")[-1]
                self._validate_body(json_body, schema_name, warnings)

        return warnings

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
            cleaned[key] = value

        if stripped:
            logger.debug(f"Stripped read-only fields from {schema_name}: {stripped}")

        return cleaned
