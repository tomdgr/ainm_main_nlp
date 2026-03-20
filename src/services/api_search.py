"""Hybrid OpenAPI search: BM25 + semantic embeddings with RRF merging."""

import json
import logging
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# Resolve spec path: Docker then local
_SPEC_PATHS = [
    Path("/app/data/apispec_openapi.json"),
    Path(__file__).resolve().parents[2] / "docs" / "task_api_docs" / "apispec_openapi.json",
]

_HTTP_METHODS = {"get", "post", "put", "delete", "patch"}


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer: lowercase, split on non-alphanumeric, filter short tokens."""
    return [t for t in re.split(r"[^a-zA-Z0-9æøåÆØÅäöüÄÖÜ]+", text.lower()) if len(t) > 1]


def _get_base_path(path: str) -> str:
    """Extract base path for grouping. e.g., /employee/employment/{id} → /employee/employment"""
    segments = [s for s in path.split("/") if s and not s.startswith("{")]
    return "/" + "/".join(segments) if segments else path


class ApiSearchService:
    """Hybrid search over OpenAPI endpoints using BM25 + semantic embeddings."""

    def __init__(self):
        self._entries: list[dict] = []
        self._documents: list[str] = []  # Text corpus for BM25
        self._bm25: BM25Okapi | None = None
        self._embeddings: np.ndarray | None = None
        self._model: SentenceTransformer | None = None
        self._path_groups: dict[str, list[dict]] = defaultdict(list)
        self._schemas: dict = {}

    def load(self, spec_path: str | Path | None = None):
        """Load the OpenAPI spec and build search indices."""
        if spec_path is None:
            for p in _SPEC_PATHS:
                if p.exists():
                    spec_path = p
                    break
        if spec_path is None:
            logger.warning("OpenAPI spec not found")
            return

        logger.info(f"Loading OpenAPI spec from {spec_path}")
        with open(spec_path) as f:
            spec = json.load(f)

        paths = spec.get("paths", {})
        self._schemas = spec.get("components", {}).get("schemas", {})

        # Build entries and text corpus
        for path, methods in paths.items():
            for method, details in methods.items():
                if method not in _HTTP_METHODS:
                    continue

                summary = details.get("summary", "")
                tags = " ".join(details.get("tags", []))
                operation_id = details.get("operationId", "")

                # Extract params
                params = details.get("parameters", [])
                param_names = [p.get("name", "") for p in params]

                # Extract body schema name
                body_schema = ""
                req_body = details.get("requestBody", {})
                if req_body:
                    content = req_body.get("content", {})
                    json_schema = content.get("application/json", {}).get("schema", {})
                    ref = json_schema.get("$ref", "")
                    if ref:
                        body_schema = ref.split("/")[-1]

                entry = {
                    "path": path,
                    "method": method.upper(),
                    "summary": summary,
                    "tags": tags,
                    "operation_id": operation_id,
                    "params": param_names,
                    "body_schema": body_schema,
                    "base_path": _get_base_path(path),
                }
                self._entries.append(entry)

                # Build document text for this endpoint
                # Include path segments as separate tokens for matching
                path_words = " ".join(s for s in path.split("/") if s and not s.startswith("{"))
                doc = f"{method} {path_words} {summary} {tags} {operation_id} {' '.join(param_names)}"
                self._documents.append(doc)

                # Group by base path
                self._path_groups[entry["base_path"]].append(entry)

        # Build BM25 index
        tokenized = [_tokenize(doc) for doc in self._documents]
        self._bm25 = BM25Okapi(tokenized)

        # Build semantic embeddings
        logger.info("Loading embedding model (all-MiniLM-L6-v2)...")
        self._model = SentenceTransformer("all-MiniLM-L6-v2")
        self._embeddings = self._model.encode(self._documents, show_progress_bar=False)

        logger.info(f"Indexed {len(self._entries)} endpoints with BM25 + {self._embeddings.shape[1]}-dim embeddings")

    def search(self, query: str, max_groups: int = 6) -> str:
        """Hybrid search: BM25 + semantic + RRF + path boost + grouping."""
        if not self._entries or self._bm25 is None or self._embeddings is None:
            return "Search index not loaded."

        n = len(self._entries)
        query_tokens = _tokenize(query)

        # --- BM25 sparse search ---
        bm25_scores = self._bm25.get_scores(query_tokens)
        bm25_ranking = np.argsort(-bm25_scores)[:30]

        # --- Semantic dense search ---
        query_embedding = self._model.encode([query])[0]
        cosine_sims = np.dot(self._embeddings, query_embedding) / (
            np.linalg.norm(self._embeddings, axis=1) * np.linalg.norm(query_embedding) + 1e-8
        )
        semantic_ranking = np.argsort(-cosine_sims)[:30]

        # --- RRF merge ---
        k = 60  # RRF constant
        rrf_scores = np.zeros(n)
        for rank, idx in enumerate(bm25_ranking):
            rrf_scores[idx] += 1.0 / (k + rank)
        for rank, idx in enumerate(semantic_ranking):
            rrf_scores[idx] += 1.0 / (k + rank)

        # --- Path segment boost ---
        for i, entry in enumerate(self._entries):
            path_segments = set(
                s.lower() for s in entry["path"].split("/") if s and not s.startswith("{")
            )
            for token in query_tokens:
                if token in path_segments:
                    rrf_scores[i] *= 3.0
                    break  # One boost per endpoint is enough

        # --- Group by base path, rank groups by best endpoint score ---
        group_scores: dict[str, float] = {}
        for i, entry in enumerate(self._entries):
            bp = entry["base_path"]
            group_scores[bp] = max(group_scores.get(bp, 0), rrf_scores[i])

        # Sort groups by score, take top N
        sorted_groups = sorted(group_scores.items(), key=lambda x: -x[1])[:max_groups]

        # --- Format output ---
        lines = []
        for base_path, score in sorted_groups:
            if score <= 0:
                continue
            lines.append(f"{base_path}:")
            for entry in self._path_groups[base_path]:
                method = entry["method"]
                path = entry["path"]
                summary = entry["summary"]

                # Build detail string
                detail_parts = [f"  {method} {path}"]
                if summary:
                    detail_parts[0] += f" — {summary}"
                if entry["body_schema"]:
                    detail_parts.append(f"    Schema: {entry['body_schema']}")
                if entry["params"]:
                    param_str = ", ".join(entry["params"][:8])
                    if len(entry["params"]) > 8:
                        param_str += ", ..."
                    detail_parts.append(f"    Params: {param_str}")

                lines.append("\n".join(detail_parts))
            lines.append("")

        if not lines:
            return f"No endpoints found matching '{query}'."

        return "\n".join(lines)

    def get_schema(self, schema_name: str) -> str:
        """Get a schema definition by name (delegates to existing functionality)."""
        schema = self._schemas.get(schema_name)
        if not schema:
            return f"Schema '{schema_name}' not found."

        lines = [f"Schema: {schema_name}", ""]
        required_fields = set(schema.get("required", []))
        properties = schema.get("properties", {})

        for prop_name, prop_def in properties.items():
            if prop_def.get("readOnly", False):
                continue
            req_marker = " (REQUIRED)" if prop_name in required_fields else ""
            prop_type = prop_def.get("type", "")
            ref = prop_def.get("$ref")
            if ref:
                prop_type = f"ref:{ref.split('/')[-1]}"
            desc = prop_def.get("description", "")
            desc_short = desc[:80] + "..." if len(desc) > 80 else desc
            lines.append(f"  - {prop_name}: {prop_type}{req_marker} {desc_short}")

        return "\n".join(lines)
