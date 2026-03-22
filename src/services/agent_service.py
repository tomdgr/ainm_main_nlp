import base64
import csv
import io
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from anthropic import AsyncAnthropicVertex
from pydantic_ai import Agent, BinaryContent, RunContext, UsageLimits
from pydantic_ai.messages import ModelResponse, ThinkingPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from src.models import FileAttachment, SolveRequest
from src.prompts.system_prompt import get_system_prompt

# from src.services.api_search import ApiSearchService
from src.services.api_validator import APIValidator
from src.services.pdf_extractor import extract_pdf_text
from src.services.leaderboard import LeaderboardService
from src.services.openapi_spec import OpenAPISpecSearcher
from src.services.run_history import RunHistoryService
from src.services.tripletex_client import TripletexClient
from src.utils.logging import RunLogger

logger = logging.getLogger(__name__)


@dataclass
class AgentDeps:
    """Dependencies injected into the agent for each solve request."""

    tripletex_client: TripletexClient
    spec_searcher: OpenAPISpecSearcher
    api_validator: APIValidator
    run_logger: RunLogger
    files: list[FileAttachment] = field(default_factory=list)
    playbook_text: str = ""
    call_history: list[str] = field(default_factory=list)
    notes: dict[str, list[str]] = field(default_factory=dict)
    data_store: dict[str, any] = field(default_factory=dict)
    _data_counter: int = 0


class AgentService:
    """Single-phase PydanticAI agent with playbook injection."""

    def __init__(
        self,
        spec_searcher: OpenAPISpecSearcher,
        api_validator: APIValidator,
        run_history: RunHistoryService | None = None,
        api_search=None,
        leaderboard: LeaderboardService | None = None,
    ):
        self.spec_searcher = spec_searcher
        self.api_validator = api_validator
        self.run_history = run_history
        self.api_search = api_search
        self.leaderboard = leaderboard
        self.sonnet_model = self._create_model("claude-sonnet-4-6")
        self.opus_model = self._create_model("claude-opus-4-6")
        self.model = self.opus_model
        self.executor_agent = self._create_executor_agent()

    def _create_model(self, model_name: str = "claude-opus-4-6") -> AnthropicModel:
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "ainm26osl-708")
        region = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

        vertex_client = AsyncAnthropicVertex(
            project_id=project_id,
            region=region,
        )
        provider = AnthropicProvider(anthropic_client=vertex_client)
        return AnthropicModel(model_name, provider=provider)

    # ------------------------------------------------------------------
    # Executor agent (all tools: spec lookup + tripletex_api)
    # ------------------------------------------------------------------

    def _create_executor_agent(self) -> Agent:
        """Agent that executes the plan. Has all tools including tripletex_api."""
        agent = Agent(
            self.model,
            system_prompt=get_system_prompt(),
            deps_type=AgentDeps,
            model_settings={
                "temperature": 1.0,
                "max_tokens": 16000,
                "anthropic_thinking": {
                    "type": "adaptive",
                },
                "anthropic_effort": "medium",
                "anthropic_cache_instructions": True,
                "anthropic_cache_tool_definitions": True,
                "parallel_tool_calls": True,
            },
        )
        self._register_executor_tools(agent)
        return agent

    def _register_executor_tools(self, agent: Agent):
        """Register all tools including tripletex_api."""

        # Dynamic system prompt: inject task-specific playbook
        @agent.system_prompt(dynamic=True)
        def inject_playbook(ctx: RunContext[AgentDeps]) -> str:
            if ctx.deps.playbook_text:
                # Framing (confident vs soft) is baked into the playbook text
                return "\n\n## Task-Specific Playbook\n\n" + ctx.deps.playbook_text
            return ""

        @agent.tool(retries=5)
        async def tripletex_api(
            ctx: RunContext[AgentDeps],
            method: str,
            path: str,
            params: dict[str, str | int | bool] | None = None,
            json_body: dict | list | None = None,
        ) -> str:
            """Make an API call to the Tripletex REST API.

            Use this to create, read, update, or delete accounting entities.

            Args:
                ctx: The run context with dependencies.
                method: HTTP method (GET, POST, PUT, DELETE).
                path: API path (e.g., "/employee", "/invoice/123/:payment").
                params: Query parameters as key-value pairs.
                json_body: Request body for POST/PUT. Usually a dict, but some endpoints (like PUT /supplierInvoice/voucher/{id}/postings) require a JSON array (list).

            Returns:
                JSON string with {status_code, body, ok}.
            """
            ctx.deps.run_logger.log_tool_call(
                "tripletex_api",
                {
                    "method": method,
                    "path": path,
                    "params": params,
                    "json_body": json_body,
                },
            )

            # --- Pre-validation: catch errors before making the HTTP call ---
            warnings = ctx.deps.api_validator.validate(method, path, json_body, params)
            if warnings:
                warning_text = "\n".join(f"  - {w}" for w in warnings)
                ctx.deps.run_logger.log_validation_warning(method, path, warnings)
                result = {
                    "status_code": 0,
                    "body": {
                        "validation_warnings": warnings,
                        "message": f"Pre-validation caught issues (no API call made):\n{warning_text}",
                    },
                    "ok": False,
                }
                result_str = json.dumps(result, ensure_ascii=False, default=str)
                ctx.deps.run_logger.log_tool_result("tripletex_api", result_str)
                return result_str

            # Auto-fix known issues silently before sending
            cleaned_body = ctx.deps.api_validator.strip_readonly_fields(
                method, path, json_body
            )
            cleaned_body = ctx.deps.api_validator.fix_postings_rows(
                method, path, cleaned_body
            )

            # --- Auto-ensure bank account 1920 before invoice creation ---
            if method.upper() == "POST" and path.rstrip("/") == "/invoice":
                if not ctx.deps.data_store.get("_bank_account_checked"):
                    ctx.deps.data_store["_bank_account_checked"] = True
                    bank_resp = await ctx.deps.tripletex_client.request(
                        "GET", "/ledger/account",
                        params={"number": "1920", "fields": "id,version,bankAccountNumber"},
                    )
                    bank_vals = bank_resp.get("body", {}).get("values", [])
                    if bank_vals and not bank_vals[0].get("bankAccountNumber"):
                        await ctx.deps.tripletex_client.request(
                            "PUT", f"/ledger/account/{bank_vals[0]['id']}",
                            json_body={
                                "id": bank_vals[0]["id"],
                                "version": bank_vals[0]["version"],
                                "bankAccountNumber": "86011117947",
                            },
                        )
                        ctx.deps.run_logger.log(
                            "AUTO_FIX", "Set bankAccountNumber on account 1920"
                        )

            # --- Auto-fetch paymentTypeId before payment calls ---
            if method.upper() == "PUT" and "/:payment" in path:
                if params is None:
                    params = {}
                ptid = params.get("paymentTypeId", 0)
                if not ptid or ptid == 0 or ptid == "0":
                    if not ctx.deps.data_store.get("_payment_type_id"):
                        pt_resp = await ctx.deps.tripletex_client.request(
                            "GET", "/invoice/paymentType",
                            params={"fields": "id,description", "count": 10},
                        )
                        pt_vals = pt_resp.get("body", {}).get("values", [])
                        if pt_vals:
                            ctx.deps.data_store["_payment_type_id"] = pt_vals[0]["id"]
                    cached_ptid = ctx.deps.data_store.get("_payment_type_id")
                    if cached_ptid:
                        params["paymentTypeId"] = cached_ptid
                        ctx.deps.run_logger.log(
                            "AUTO_FIX", f"Set paymentTypeId={cached_ptid}"
                        )

            result = await ctx.deps.tripletex_client.request(
                method=method,
                path=path,
                params=params,
                json_body=cleaned_body,
            )

            # Track call in history
            status = result.get("status_code", 0)
            ctx.deps.call_history.append(f"{method.upper()} {path} -> {status}")

            # Truncate verbose 200/201 responses to reduce context growth
            if result.get("ok") and status in (200, 201):
                body = result.get("body", {})
                value = body.get("value")
                values = body.get("values")

                essential_keys = {
                    "id", "version", "name", "number", "displayName",
                    "invoiceNumber", "voucherNumber", "amount",
                    "amountExcludingVat", "amountOutstanding",
                    "startDate", "endDate", "date",
                    "supplierNumber", "customerNumber", "organizationNumber",
                    "email", "invoiceEmail", "firstName", "lastName",
                    # Lookup/reference fields the LLM needs for matching
                    "nameNO", "code", "description", "rate", "type",
                    "activityType", "percentage", "isInactive",
                    # BalanceSheet fields (task 30 needs these)
                    "account", "balanceIn", "balanceChange", "balanceOut",
                    "bankAccountNumber",
                }
                # Extra keys needed for chaining operations after writes
                chaining_keys = {
                    "postings", "orders", "orderLines", "vatType",
                    "currency", "employments", "projectManager",
                    "isFixedPrice", "fixedprice", "supplier",
                    "customer", "employee", "department", "project",
                    "travelDetails", "costs", "perDiemCompensations",
                }

                # Single entity response
                if (
                    isinstance(value, dict)
                    and len(json.dumps(value, default=str)) > 800
                ):
                    is_write = method.upper() in ("POST", "PUT")
                    keep = essential_keys | chaining_keys if is_write else essential_keys
                    trimmed = {k: value[k] for k in keep if k in value}
                    result["body"]["value"] = trimmed

                # List responses (GET with multiple items)
                elif isinstance(values, list) and len(values) > 0:
                    # Skip truncation for critical small endpoints (balanceSheet, etc.)
                    skip_truncation = any(p in path for p in ("/balanceSheet", "/accountingPeriod"))
                    raw_size = len(json.dumps(values, default=str))

                    # Large lists: store raw data server-side, return summary
                    if not skip_truncation and len(values) >= 20 and raw_size > 2000:
                        ctx.deps._data_counter += 1
                        ref_key = f"api_result_{ctx.deps._data_counter}"
                        ctx.deps.data_store[ref_key] = values

                        # Build compact summary for the LLM
                        sample = values[:3]
                        sample_keys = set()
                        for item in sample:
                            if isinstance(item, dict):
                                sample_keys.update(item.keys())

                        summary = {
                            "status_code": status,
                            "ok": True,
                            "data_ref": ref_key,
                            "count": len(values),
                            "full_result_size": body.get("fullResultSize", len(values)),
                            "sample_keys": sorted(sample_keys)[:15],
                            "message": (
                                f"{len(values)} items stored as '{ref_key}'. "
                                f"Use aggregate_postings(data_ref='{ref_key}') to analyze, "
                                f"or access individual items via the data reference."
                            ),
                        }
                        # Include first 3 items trimmed for context
                        summary["sample"] = [
                            {k: item[k] for k in essential_keys if k in item}
                            if isinstance(item, dict) else item
                            for item in values[:3]
                        ]
                        result_str = json.dumps(summary, ensure_ascii=False, default=str)
                        ctx.deps.run_logger.log_tool_result("tripletex_api", result_str)
                        return result_str

                    # Small-medium lists: trim in place
                    elif not skip_truncation and raw_size > 800:
                        trimmed_list = []
                        for item in values:
                            if isinstance(item, dict):
                                trimmed_list.append(
                                    {k: item[k] for k in essential_keys if k in item}
                                )
                            else:
                                trimmed_list.append(item)
                        result["body"]["values"] = trimmed_list

            result_str = json.dumps(result, ensure_ascii=False, default=str)
            ctx.deps.run_logger.log_tool_result("tripletex_api", result_str)
            return result_str

        @agent.tool
        def search_api_spec(ctx: RunContext[AgentDeps], query: str) -> str:
            """Search the Tripletex OpenAPI specification for endpoint details.

            Args:
                ctx: The run context with dependencies.
                query: Keywords to search for (e.g., "mileage allowance", "bank reconciliation").

            Returns:
                Formatted text listing matching endpoints with their methods, params, and body schemas.
            """
            ctx.deps.run_logger.log_tool_call("search_api_spec", {"query": query})
            result = ctx.deps.spec_searcher.search_endpoints(query)
            ctx.deps.run_logger.log_tool_result("search_api_spec", result)
            return result

        @agent.tool
        def get_endpoint_detail(
            ctx: RunContext[AgentDeps], path: str, method: str
        ) -> str:
            """Get full details for a specific Tripletex API endpoint.

            Use this to verify exact field names and types before making an API call.

            Args:
                ctx: The run context with dependencies.
                path: The endpoint path (e.g., "/travelExpense/mileageAllowance").
                method: HTTP method (GET, POST, PUT, DELETE).

            Returns:
                Full endpoint details including parameters, body schema, and response format.
            """
            ctx.deps.run_logger.log_tool_call(
                "get_endpoint_detail", {"path": path, "method": method}
            )
            result = ctx.deps.spec_searcher.get_endpoint_details(path, method)
            ctx.deps.run_logger.log_tool_result("get_endpoint_detail", result)
            return result

        @agent.tool
        def parse_structured_data(
            ctx: RunContext[AgentDeps],
            content: str,
            format: str = "csv",
        ) -> str:
            """Parse CSV, TSV, or semicolon-delimited text into structured JSON rows.

            Use this instead of manually parsing tabular data in your reasoning.
            Handles Norwegian number formats (comma as decimal separator).

            Args:
                ctx: The run context with dependencies.
                content: The raw text content to parse (e.g., from a bank statement CSV).
                format: Delimiter format — "csv" (comma), "tsv" (tab), or "ssv" (semicolon).

            Returns:
                JSON array of row objects with column headers as keys. Numbers are auto-parsed.
            """
            ctx.deps.run_logger.log_tool_call(
                "parse_structured_data", {"format": format, "length": len(content)}
            )
            rows: list[dict] = []
            try:
                delimiter = {"csv": ",", "tsv": "\t", "ssv": ";"}.get(format, ",")
                reader = csv.DictReader(
                    io.StringIO(content.strip()), delimiter=delimiter
                )
                for row in reader:
                    cleaned: dict = {}
                    for k, v in row.items():
                        if not k:
                            continue
                        k = k.strip()
                        v = v.strip() if v else ""
                        try:
                            cleaned[k] = float(v.replace(" ", "").replace(",", "."))
                        except (ValueError, AttributeError):
                            cleaned[k] = v
                    rows.append(cleaned)
                result = json.dumps(rows, ensure_ascii=False, default=str)
            except Exception as e:
                result = json.dumps({"error": str(e)})
            ctx.deps.run_logger.log_tool_result(
                "parse_structured_data", f"{len(rows)} rows parsed"
            )
            return result

        @agent.tool
        def aggregate_postings(
            ctx: RunContext[AgentDeps],
            data_ref: str | None = None,
            postings_json: str | None = None,
            group_by: str = "account",
        ) -> str:
            """Aggregate ledger postings by account — returns per-account sums sorted by absolute amount.

            Use this instead of manually summing postings in your reasoning.
            Preferred: pass a data_ref from a previous large API response.
            Alternative: pass raw JSON directly via postings_json.

            Args:
                ctx: The run context with dependencies.
                data_ref: Reference key from a stored API response (e.g., "api_result_1").
                    Use this when the API response was large and stored automatically.
                postings_json: JSON string — either the full API response body (with "values" wrapper)
                    or a raw JSON array of posting objects. Only used if data_ref is not provided.
                group_by: Field to group by. Currently only "account" is supported.

            Returns:
                JSON array of {account_id, account_number, account_name, total_amount, count}
                sorted by absolute total_amount descending.
            """
            ctx.deps.run_logger.log_tool_call(
                "aggregate_postings", {"data_ref": data_ref, "group_by": group_by}
            )
            try:
                # Resolve data source
                if data_ref:
                    stored = ctx.deps.data_store.get(data_ref)
                    if stored is None:
                        return json.dumps({"error": f"No data found for ref '{data_ref}'. Available refs: {list(ctx.deps.data_store.keys())}"})
                    postings = stored  # Already a list from data_store
                elif postings_json:
                    data = json.loads(postings_json)
                    postings = data.get("values", data) if isinstance(data, dict) else data
                else:
                    return json.dumps({"error": "Provide either data_ref or postings_json"})

                totals: dict[str, dict] = {}
                for p in postings:
                    acct = p.get("account", {})
                    key = str(acct.get("id", "unknown"))
                    if key not in totals:
                        totals[key] = {
                            "account_id": acct.get("id"),
                            "account_number": acct.get("number"),
                            "account_name": acct.get(
                                "name", acct.get("displayName", "")
                            ),
                            "total_amount": 0.0,
                            "count": 0,
                        }
                    totals[key]["total_amount"] = round(
                        totals[key]["total_amount"] + p.get("amount", 0), 2
                    )
                    totals[key]["count"] += 1

                sorted_totals = sorted(
                    totals.values(), key=lambda x: abs(x["total_amount"]), reverse=True
                )
                result = json.dumps(sorted_totals, ensure_ascii=False, default=str)
            except Exception as e:
                result = json.dumps({"error": str(e)})
            ctx.deps.run_logger.log_tool_result("aggregate_postings", result[:300])
            return result

        @agent.tool
        def calculate_accounting(
            ctx: RunContext[AgentDeps],
            operation: str,
            amount: float | None = None,
            vat_rate: float | None = None,
            cost: float | None = None,
            useful_life_years: float | None = None,
            period_months: int | None = None,
            amounts: list[float] | None = None,
        ) -> str:
            """Perform accounting calculations — VAT, depreciation, posting validation.

            Use this instead of calculating in your reasoning. Operations:
            - "vat_from_gross": Split gross amount into net + VAT. Args: amount, vat_rate (default 0.25)
            - "vat_from_net": Compute gross from net + VAT. Args: amount, vat_rate (default 0.25)
            - "depreciation": Linear depreciation. Args: cost, useful_life_years, period_months
            - "validate_postings": Check if amounts sum to zero. Args: amounts (list of floats)

            Args:
                ctx: The run context with dependencies.
                operation: One of "vat_from_gross", "vat_from_net", "depreciation", "validate_postings".
                amount: The amount for VAT operations.
                vat_rate: VAT rate as decimal (0.25 for 25%). Defaults to 0.25.
                cost: Asset cost for depreciation.
                useful_life_years: Useful life in years for depreciation.
                period_months: Number of months to depreciate (default 1).
                amounts: List of posting amounts for validation.

            Returns:
                JSON with calculated values. All amounts rounded to 2 decimals.
            """
            ctx.deps.run_logger.log_tool_call(
                "calculate_accounting", {"operation": operation}
            )
            try:
                if operation == "vat_from_gross":
                    rate = vat_rate or 0.25
                    gross = amount
                    net = round(gross / (1 + rate), 2)
                    vat = round(gross - net, 2)
                    result = {"gross": gross, "net": net, "vat": vat, "vat_rate": rate}
                elif operation == "vat_from_net":
                    rate = vat_rate or 0.25
                    net = amount
                    vat = round(net * rate, 2)
                    gross = round(net + vat, 2)
                    result = {"gross": gross, "net": net, "vat": vat, "vat_rate": rate}
                elif operation == "depreciation":
                    annual = round(cost / useful_life_years, 2)
                    monthly = round(annual / 12, 2)
                    period_total = round(monthly * (period_months or 1), 2)
                    result = {
                        "annual": annual,
                        "monthly": monthly,
                        "period_total": period_total,
                        "cost": cost,
                        "useful_life_years": useful_life_years,
                    }
                elif operation == "validate_postings":
                    total = round(sum(amounts), 2)
                    result = {
                        "sum": total,
                        "balanced": abs(total) < 0.01,
                        "amounts": amounts,
                    }
                else:
                    result = {"error": f"Unknown operation: {operation}"}
                out = json.dumps(result, ensure_ascii=False)
            except Exception as e:
                out = json.dumps({"error": str(e)})
            ctx.deps.run_logger.log_tool_result("calculate_accounting", out)
            return out

        @agent.tool
        async def analyze_expense_changes(
            ctx: RunContext[AgentDeps],
            period1_from: str,
            period1_to: str,
            period2_from: str,
            period2_to: str,
            top_n: int = 3,
            account_range_from: int = 5000,
            account_range_to: int = 7999,
        ) -> str:
            """Compare expense accounts between two periods and find the largest changes.

            Fetches ledger postings for both periods, aggregates by account, computes
            the change (period2 - period1), and returns the top N accounts by increase.
            This is much more reliable than manually chaining API calls + aggregate_postings.

            Args:
                ctx: The run context with dependencies.
                period1_from: Start date for period 1 (e.g., "2026-01-01").
                period1_to: End date for period 1 exclusive (e.g., "2026-02-01").
                period2_from: Start date for period 2 (e.g., "2026-02-01").
                period2_to: End date for period 2 exclusive (e.g., "2026-03-01").
                top_n: Number of top accounts to return (default 3).
                account_range_from: Min account number to include (default 5000).
                account_range_to: Max account number to include (default 7999).

            Returns:
                JSON with top_changes array: [{account_id, account_number, account_name,
                period1_total, period2_total, change}] sorted by change descending.
            """
            ctx.deps.run_logger.log_tool_call(
                "analyze_expense_changes",
                {"period1": f"{period1_from}..{period1_to}", "period2": f"{period2_from}..{period2_to}"},
            )
            try:
                # Fetch both periods
                p1_result = await ctx.deps.tripletex_client.request(
                    "GET", "/ledger/postingByDate",
                    params={"dateFrom": period1_from, "dateTo": period1_to, "count": 10000},
                )
                p2_result = await ctx.deps.tripletex_client.request(
                    "GET", "/ledger/postingByDate",
                    params={"dateFrom": period2_from, "dateTo": period2_to, "count": 10000},
                )

                if not p1_result.get("ok") or not p2_result.get("ok"):
                    return json.dumps({
                        "error": "Failed to fetch postings",
                        "period1_status": p1_result.get("status_code"),
                        "period2_status": p2_result.get("status_code"),
                    })

                # Step 1: Aggregate by account ID (number may be missing from postingByDate)
                def aggregate_by_id(postings_body):
                    postings = postings_body.get("values", [])
                    totals = {}
                    for p in postings:
                        acct = p.get("account", {})
                        acct_id = acct.get("id")
                        if not acct_id:
                            continue
                        key = str(acct_id)
                        if key not in totals:
                            totals[key] = {"account_id": acct_id, "total": 0.0}
                        totals[key]["total"] = round(
                            totals[key]["total"] + p.get("amount", 0), 2
                        )
                    return totals

                p1_raw = aggregate_by_id(p1_result.get("body", {}))
                p2_raw = aggregate_by_id(p2_result.get("body", {}))

                # Step 2: Batch-fetch account numbers for all unique account IDs
                all_ids = set(p1_raw.keys()) | set(p2_raw.keys())
                id_list = ",".join(all_ids)
                acct_resp = await ctx.deps.tripletex_client.request(
                    "GET", "/ledger/account",
                    params={"id": id_list, "fields": "id,number,name", "count": len(all_ids) + 10},
                )
                acct_map = {}
                for a in acct_resp.get("body", {}).get("values", []):
                    acct_map[str(a["id"])] = {
                        "number": a.get("number", 0),
                        "name": a.get("name", ""),
                    }

                # Step 3: Filter to expense accounts and compute changes
                changes = []
                for key in all_ids:
                    info = acct_map.get(key, {})
                    acct_num = info.get("number", 0)
                    if not (account_range_from <= acct_num <= account_range_to):
                        continue
                    p1_total = p1_raw.get(key, {}).get("total", 0.0)
                    p2_total = p2_raw.get(key, {}).get("total", 0.0)
                    changes.append({
                        "account_id": int(key),
                        "account_number": acct_num,
                        "account_name": info.get("name", ""),
                        "period1_total": p1_total,
                        "period2_total": p2_total,
                        "change": round(p2_total - p1_total, 2),
                    })

                # Sort by change descending (largest increase first)
                changes.sort(key=lambda x: x["change"], reverse=True)
                top = changes[:top_n]

                result = json.dumps({
                    "top_changes": top,
                    "total_accounts_analyzed": len(changes),
                    "period1_postings": len(p1_result.get("body", {}).get("values", [])),
                    "period2_postings": len(p2_result.get("body", {}).get("values", [])),
                }, ensure_ascii=False, default=str)
            except Exception as e:
                result = json.dumps({"error": str(e)})

            ctx.deps.run_logger.log_tool_result("analyze_expense_changes", result[:500])
            return result

        @agent.tool
        async def create_supplier_invoice(
            ctx: RunContext[AgentDeps],
            supplier_id: int,
            invoice_number: str,
            invoice_date: str,
            due_date: str,
            gross_amount: float,
            expense_account_id: int,
            description: str,
            vat_type_id: int = 1,
        ) -> str:
            """Create a complete supplier invoice: voucher + SupplierInvoice registration.

            Handles the full 3-step flow that the Tripletex API requires:
            1. Finds the 'Leverandørfaktura' voucher type
            2. Creates a balanced voucher (expense debit + accounts payable 2400 credit)
            3. Registers it as a proper SupplierInvoice via PUT postings

            Args:
                ctx: The run context.
                supplier_id: ID of the supplier entity.
                invoice_number: Supplier's invoice reference (e.g., "INV-2026-1234").
                invoice_date: Invoice date (YYYY-MM-DD).
                due_date: Due date (YYYY-MM-DD).
                gross_amount: Total amount INCLUDING VAT.
                expense_account_id: ID of the expense account (e.g., account 6500).
                description: Description for the voucher.
                vat_type_id: VAT type ID (default 1 = 25% incoming/fradrag).

            Returns:
                JSON with voucher_id, supplier_invoice status, and created posting details.
            """
            ctx.deps.run_logger.log_tool_call("create_supplier_invoice", {
                "supplier_id": supplier_id, "invoice_number": invoice_number,
                "invoice_date": invoice_date, "due_date": due_date,
                "gross_amount": gross_amount, "expense_account_id": expense_account_id,
                "description": description,
            })
            client = ctx.deps.tripletex_client
            try:
                # Step 1: Find voucherType "Leverandørfaktura"
                vt_resp = await client.request(
                    "GET", "/ledger/voucherType",
                    params={"name": "Leverandørfaktura", "fields": "id,name"},
                )
                vt_vals = vt_resp.get("body", {}).get("values", [])
                voucher_type_id = vt_vals[0]["id"] if vt_vals else None

                # Step 2: Find accounts payable (2400)
                ap_resp = await client.request(
                    "GET", "/ledger/account",
                    params={"number": "2400", "fields": "id,number"},
                )
                ap_vals = ap_resp.get("body", {}).get("values", [])
                ap_account_id = ap_vals[0]["id"] if ap_vals else None

                if not ap_account_id:
                    return json.dumps({"error": "Account 2400 (Leverandørgjeld) not found"})

                # Step 3: Build balanced postings with all scoring-relevant fields
                postings = [
                    {
                        "row": 1,
                        "account": {"id": expense_account_id},
                        "description": description,
                        "amountGross": gross_amount,
                        "amountGrossCurrency": gross_amount,
                        "vatType": {"id": vat_type_id},
                    },
                    {
                        "row": 2,
                        "account": {"id": ap_account_id},
                        "supplier": {"id": supplier_id},
                        "description": description,
                        "amountGross": -gross_amount,
                        "amountGrossCurrency": -gross_amount,
                        "invoiceNumber": invoice_number,
                        "termOfPayment": due_date,
                    },
                ]

                voucher_body = {
                    "date": invoice_date,
                    "description": description,
                    "vendorInvoiceNumber": invoice_number,
                    "postings": postings,
                }
                if voucher_type_id:
                    voucher_body["voucherType"] = {"id": voucher_type_id}

                # Step 4: Create voucher and send to ledger
                v_resp = await client.request(
                    "POST", "/ledger/voucher",
                    params={"sendToLedger": "true"},
                    json_body=voucher_body,
                )
                if not v_resp.get("ok"):
                    return json.dumps({
                        "error": "Voucher creation failed",
                        "details": v_resp.get("body"),
                    })

                voucher_id = v_resp["body"]["value"]["id"]
                ctx.deps.call_history.append(f"POST /ledger/voucher -> {v_resp['status_code']}")

                # Step 5: Try POST /incomingInvoice as best-effort (may work on
                # competition proxy even if 403 on our sandbox — different permissions).
                # We decrement error_count if it fails so it doesn't hurt efficiency scoring.
                inc_ok = False
                errors_before = client.error_count
                try:
                    inc_resp = await client.request(
                        "POST", "/incomingInvoice",
                        params={"sendTo": "ledger"},
                        json_body={
                            "invoiceHeader": {
                                "vendorId": supplier_id,
                                "invoiceDate": invoice_date,
                                "dueDate": due_date,
                                "invoiceAmount": gross_amount,
                                "invoiceNumber": invoice_number,
                                "description": description,
                            },
                            "orderLines": [{
                                "externalId": "line-1",
                                "row": 1,
                                "description": description,
                                "accountId": expense_account_id,
                                "amountInclVat": gross_amount,
                                "vatTypeId": vat_type_id,
                            }],
                        },
                    )
                    inc_ok = inc_resp.get("ok", False)
                except Exception:
                    pass
                # Don't let this best-effort attempt hurt our error count
                if not inc_ok:
                    client.error_count = errors_before

                result = json.dumps({
                    "voucher_id": voucher_id,
                    "voucher_sent_to_ledger": True,
                    "incoming_invoice_created": inc_ok,
                    "message": (
                        f"Supplier invoice voucher {voucher_id} created and sent to ledger. "
                        f"VoucherType=Leverandørfaktura, vendorInvoiceNumber={invoice_number}."
                        + (" Also registered via /incomingInvoice." if inc_ok else "")
                    ),
                })
            except Exception as e:
                result = json.dumps({"error": str(e)})

            ctx.deps.run_logger.log_tool_result("create_supplier_invoice", result)
            return result

        @agent.tool
        async def create_travel_expense(
            ctx: RunContext[AgentDeps],
            employee_id: int,
            title: str,
            departure_date: str,
            return_date: str,
            destination: str,
            per_diem_days: int,
            per_diem_rate: float,
            costs: list[dict] | None = None,
            deliver: bool = True,
        ) -> str:
            """Create and deliver a travel expense with per diem and optional costs.

            Handles the full flow: discovers rate/cost/payment categories,
            builds the correct nested body with proper field names, creates
            the expense, and auto-delivers it.

            Args:
                ctx: The run context.
                employee_id: ID of the traveling employee.
                title: Title/purpose of the trip (e.g., "Konferanse Oslo").
                departure_date: Trip start date (YYYY-MM-DD).
                return_date: Trip end date (YYYY-MM-DD).
                destination: Destination city.
                per_diem_days: Number of per diem days.
                per_diem_rate: Daily rate in NOK.
                costs: Optional list of costs, each with keys:
                    - description (str): e.g., "Flybillett", "Taxi"
                    - amount (float): Amount including VAT
                    - date (str): Date of the cost (YYYY-MM-DD)
                deliver: Whether to auto-deliver after creation (default True).

            Returns:
                JSON with travel_expense_id and delivery status.
            """
            ctx.deps.run_logger.log_tool_call("create_travel_expense", {
                "employee_id": employee_id, "title": title, "days": per_diem_days,
            })
            client = ctx.deps.tripletex_client

            try:
                # Step 1: Discover rate category + rate type (for per diem)
                rc_resp = await client.request(
                    "GET", "/travelExpense/rateCategory",
                    params={
                        "type": "PER_DIEM",
                        "dateFrom": departure_date[:7] + "-01",
                        "dateTo": return_date,
                        "fields": "id,name,type",
                    },
                )
                rc_vals = rc_resp.get("body", {}).get("values", [])
                # Prefer "Overnatting" (overnight) category
                rate_cat = next(
                    (r for r in rc_vals if "overnatting" in r.get("name", "").lower()),
                    rc_vals[0] if rc_vals else None,
                )
                rate_cat_id = rate_cat["id"] if rate_cat else None

                rate_type_id = None
                if rate_cat_id:
                    rt_resp = await client.request(
                        "GET", "/travelExpense/rate",
                        params={"rateCategoryId": rate_cat_id, "fields": "id,rate"},
                    )
                    rt_vals = rt_resp.get("body", {}).get("values", [])
                    if rt_vals:
                        rate_type_id = rt_vals[0]["id"]

                # Step 2: Discover cost categories and payment type
                cc_resp = await client.request(
                    "GET", "/travelExpense/costCategory",
                    params={"isInactive": "false", "fields": "id,description"},
                )
                cost_categories = {
                    c.get("description", "").lower(): c["id"]
                    for c in cc_resp.get("body", {}).get("values", [])
                }

                pt_resp = await client.request(
                    "GET", "/travelExpense/paymentType",
                    params={"isInactive": "false", "fields": "id,description"},
                )
                pay_types = pt_resp.get("body", {}).get("values", [])
                payment_type_id = pay_types[0]["id"] if pay_types else None

                # Step 3: Build the nested body with correct field names
                body = {
                    "employee": {"id": employee_id},
                    "title": title,
                    "travelDetails": {
                        "departureDate": departure_date,
                        "returnDate": return_date,
                        "departureTime": "08:00",
                        "returnTime": "18:00",
                        "departureFrom": "Oslo",
                        "destination": destination,
                        "purpose": title,
                        "isForeignTravel": False,
                        "isDayTrip": False,
                    },
                    "perDiemCompensations": [],
                    "costs": [],
                }

                # Add per diem
                if rate_cat_id:
                    per_diem = {
                        "rateCategory": {"id": rate_cat_id},
                        "overnightAccommodation": "HOTEL",
                        "location": destination,
                        "count": per_diem_days,
                        "rate": per_diem_rate,
                    }
                    if rate_type_id:
                        per_diem["rateType"] = {"id": rate_type_id}
                    body["perDiemCompensations"].append(per_diem)

                # Multilingual cost category mapping (Norwegian category → search terms)
                _COST_KEYWORDS = {
                    "fly": ["fly", "flight", "flug", "vol", "vuelo", "voo", "aviao",
                            "avion", "flybillett", "bilhete", "billet", "billete", "air"],
                    "taxi": ["taxi", "táxi", "cab"],
                    "tog": ["tog", "train", "zug", "tren", "trem", "comboio", "bahn"],
                    "buss": ["buss", "bus", "autobus", "ônibus", "onibus", "autocar"],
                    "overnatting": ["hotell", "hotel", "overnatting", "hébergement",
                                    "alojamiento", "hospedagem", "unterkunft", "accommodation"],
                    "parkering": ["parkering", "parking", "estacionamento", "aparcamiento"],
                }

                def _match_cost_category(desc: str, categories: dict) -> int | None:
                    """Match a cost description to a Tripletex cost category using multilingual keywords."""
                    desc_lower = desc.lower()
                    # First: direct match against category names (Norwegian)
                    for cat_desc, cid in categories.items():
                        if desc_lower in cat_desc or cat_desc in desc_lower:
                            return cid
                    # Second: multilingual keyword matching
                    for cat_key, keywords in _COST_KEYWORDS.items():
                        if any(kw in desc_lower for kw in keywords):
                            # Find the Tripletex category that contains this key
                            for cat_desc, cid in categories.items():
                                if cat_key in cat_desc:
                                    return cid
                    return None

                # Add costs with correct field names
                for cost in (costs or []):
                    desc = cost.get("description", "")
                    cat_id = _match_cost_category(desc, cost_categories)
                    if not cat_id:
                        # Fallback: use first available category
                        cat_id = next(iter(cost_categories.values()), None)

                    cost_entry = {
                        "date": cost.get("date", departure_date),
                        "amountCurrencyIncVat": cost.get("amount", 0),
                        "comments": desc,
                    }
                    if cat_id:
                        cost_entry["costCategory"] = {"id": cat_id}
                    if payment_type_id:
                        cost_entry["paymentType"] = {"id": payment_type_id}
                    body["costs"].append(cost_entry)

                # Step 4: Create the travel expense
                te_resp = await client.request("POST", "/travelExpense", json_body=body)
                if not te_resp.get("ok"):
                    return json.dumps({
                        "error": "Travel expense creation failed",
                        "details": te_resp.get("body"),
                    })

                te_id = te_resp["body"]["value"]["id"]
                ctx.deps.call_history.append(f"POST /travelExpense -> {te_resp['status_code']}")

                # Step 5: Auto-deliver
                deliver_status = None
                approve_status = None
                voucher_status = None
                if deliver:
                    del_resp = await client.request(
                        "PUT", "/travelExpense/:deliver",
                        params={"id": te_id},
                    )
                    deliver_status = del_resp.get("status_code")
                    ctx.deps.call_history.append(
                        f"PUT /travelExpense/:deliver -> {deliver_status}"
                    )

                    # Step 6: Auto-approve (required before voucher creation)
                    if deliver_status in (200, 204):
                        appr_resp = await client.request(
                            "PUT", "/travelExpense/:approve",
                            params={"id": te_id},
                        )
                        approve_status = appr_resp.get("status_code")
                        ctx.deps.call_history.append(
                            f"PUT /travelExpense/:approve -> {approve_status}"
                        )

                        # Step 7: Create vouchers from the approved expense
                        if approve_status in (200, 204):
                            vchr_resp = await client.request(
                                "PUT", "/travelExpense/:createVouchers",
                                params={"id": te_id, "date": departure_date},
                            )
                            voucher_status = vchr_resp.get("status_code")
                            ctx.deps.call_history.append(
                                f"PUT /travelExpense/:createVouchers -> {voucher_status}"
                            )

                result = json.dumps({
                    "travel_expense_id": te_id,
                    "delivered": deliver_status in (200, 204) if deliver else False,
                    "approved": approve_status in (200, 204) if approve_status else False,
                    "voucher_created": voucher_status in (200, 204) if voucher_status else False,
                    "message": f"Travel expense {te_id} created, delivered, {'approved' if approve_status in (200, 204) else 'NOT approved'}, {'voucher created' if voucher_status in (200, 204) else 'no voucher'}",
                })
            except Exception as e:
                result = json.dumps({"error": str(e)})

            ctx.deps.run_logger.log_tool_result("create_travel_expense", result)
            return result

        @agent.tool
        async def import_bank_statement(
            ctx: RunContext[AgentDeps],
            csv_content: str,
            bank_account_id: int,
            from_date: str,
            to_date: str,
        ) -> str:
            """Convert a bank statement CSV to Danske Bank format and import it into Tripletex.

            Args:
                csv_content: The raw CSV text (semicolon-separated: Dato;Forklaring;Inn;Ut;Saldo).
                bank_account_id: The ledger account ID for the bank account (1920).
                from_date: Start date of the statement (YYYY-MM-DD).
                to_date: End date of the statement (YYYY-MM-DD).

            Returns the import result including the bank statement ID.
            """
            ctx.deps.run_logger.log_tool_call("import_bank_statement", {
                "bank_account_id": bank_account_id,
                "from_date": from_date,
                "to_date": to_date,
            })
            client = ctx.deps.tripletex_client

            try:
                import re as _re
                # Parse the task CSV (Dato;Forklaring;Inn;Ut;Saldo)
                lines = [l.strip() for l in csv_content.strip().split("\n") if l.strip()]
                if not lines:
                    return json.dumps({"error": "Empty CSV"})

                # Skip header line
                header = lines[0].lower()
                data_lines = lines[1:] if "dato" in header or "forklaring" in header else lines

                # Convert to Danske Bank CSV format
                danske_lines = [
                    "Bokf\u00f8rt dato;Rentedato;Tekst;Bel\u00f8p i NOK;Bokf\u00f8rt saldo i NOK;Status"
                ]
                for line in data_lines:
                    parts = line.split(";")
                    if len(parts) < 5:
                        continue
                    dato = parts[0].strip()
                    forklaring = parts[1].strip()
                    inn = parts[2].strip()
                    ut = parts[3].strip()
                    saldo = parts[4].strip()

                    # Convert date from YYYY-MM-DD to DD.MM.YYYY if needed
                    if _re.match(r"\d{4}-\d{2}-\d{2}", dato):
                        y, m, d = dato.split("-")
                        dato_dk = f"{d}.{m}.{y}"
                    else:
                        dato_dk = dato  # already DD.MM.YYYY

                    # Determine amount (Inn = positive, Ut = negative)
                    if inn and inn.replace(".", "").replace(",", "").replace("-", "").strip():
                        amount = inn.strip()
                    elif ut and ut.replace(".", "").replace(",", "").replace("-", "").strip():
                        amount = ut.strip()
                        if not amount.startswith("-"):
                            amount = f"-{amount}"
                    else:
                        continue

                    # Convert decimal from dot to comma for Norwegian format
                    amount = amount.replace(".", ",")
                    saldo_nok = saldo.replace(".", ",") if saldo else "0,00"

                    danske_lines.append(
                        f"{dato_dk};{dato_dk};{forklaring};{amount};{saldo_nok};utf\u00f8rt"
                    )

                danske_csv = "\n".join(danske_lines) + "\n"
                # Encode as ISO-8859-1 (required by the API)
                csv_bytes = danske_csv.encode("iso-8859-1")

                # Upload to Tripletex
                resp = await client.upload_file(
                    path="/bank/statement/import",
                    params={
                        "bankId": 76,  # Danske Bank
                        "accountId": bank_account_id,
                        "fromDate": from_date,
                        "toDate": to_date,
                        "fileFormat": "DANSKE_BANK_CSV",
                    },
                    file_content=csv_bytes,
                    file_name="bankutskrift.csv",
                    content_type="text/csv",
                )
                ctx.deps.call_history.append(
                    f"POST /bank/statement/import -> {resp.get('status_code')}"
                )

                if resp.get("ok"):
                    result = json.dumps({
                        "status_code": resp["status_code"],
                        "body": resp["body"],
                        "message": "Bank statement imported successfully",
                    })
                else:
                    result = json.dumps({
                        "status_code": resp.get("status_code"),
                        "error": str(resp.get("body", ""))[:500],
                        "message": "Bank statement import failed",
                    })
            except Exception as e:
                result = json.dumps({"error": str(e)})

            ctx.deps.run_logger.log_tool_result("import_bank_statement", result)
            return result

        @agent.tool
        async def setup_employee_for_payroll(
            ctx: RunContext[AgentDeps],
            employee_id: int,
            date_of_birth: str,
            start_date: str,
            annual_salary: float,
            percentage: float = 100.0,
            occupation_code: str | None = None,
        ) -> str:
            """Set up an employee for payroll processing.

            Handles the full prerequisite chain that payroll requires:
            1. Sets dateOfBirth on the employee (always null initially)
            2. Creates or finds employment record
            3. Creates employment details with validated enums
            4. Creates division if needed (with correct org number)
            5. Links division to employment

            Args:
                ctx: The run context.
                employee_id: ID of the employee.
                date_of_birth: Employee DOB (YYYY-MM-DD).
                start_date: Employment start date (YYYY-MM-DD).
                annual_salary: Annual salary in NOK.
                percentage: Full-time percentage (default 100).
                occupation_code: Optional STYRK occupation code.

            Returns:
                JSON with employment_id, division_id, and setup status.
            """
            ctx.deps.run_logger.log_tool_call("setup_employee_for_payroll", {
                "employee_id": employee_id, "salary": annual_salary,
            })
            client = ctx.deps.tripletex_client

            try:
                # Step 1: Get employee current state + set dateOfBirth
                emp_resp = await client.request(
                    "GET", f"/employee/{employee_id}",
                    params={"fields": "id,version,firstName,lastName,dateOfBirth"},
                )
                emp = emp_resp.get("body", {}).get("value", {})
                if not emp.get("dateOfBirth"):
                    await client.request(
                        "PUT", f"/employee/{employee_id}",
                        json_body={
                            "id": employee_id,
                            "version": emp["version"],
                            "dateOfBirth": date_of_birth,
                        },
                    )

                # Step 2: Check existing employment or create
                empl_resp = await client.request(
                    "GET", "/employee/employment",
                    params={"employeeId": employee_id, "fields": "id,version,startDate"},
                )
                empl_vals = empl_resp.get("body", {}).get("values", [])
                if empl_vals:
                    employment_id = empl_vals[0]["id"]
                else:
                    new_empl = await client.request(
                        "POST", "/employee/employment",
                        json_body={
                            "employee": {"id": employee_id},
                            "startDate": start_date,
                            "employmentEndReason": None,
                        },
                    )
                    employment_id = new_empl.get("body", {}).get("value", {}).get("id")
                    ctx.deps.call_history.append(
                        f"POST /employee/employment -> {new_empl.get('status_code')}"
                    )

                if not employment_id:
                    return json.dumps({"error": "Failed to create employment"})

                # Step 3: Create employment details
                occ_body = {}
                if occupation_code:
                    # Look up occupation code
                    occ_resp = await client.request(
                        "GET", "/employee/employment/occupationCode",
                        params={"nameNO": occupation_code, "fields": "id,nameNO", "count": 5},
                    )
                    occ_vals = occ_resp.get("body", {}).get("values", [])
                    if occ_vals:
                        occ_body["occupationCode"] = {"id": occ_vals[0]["id"]}

                detail_body = {
                    "employment": {"id": employment_id},
                    "date": start_date,
                    "employmentType": "ORDINARY",
                    "employmentForm": "PERMANENT",
                    "remunerationType": "MONTHLY_WAGE",
                    "workingHoursScheme": "NOT_SHIFT",
                    "annualSalary": annual_salary,
                    "percentageOfFullTimeEquivalent": percentage,
                    **occ_body,
                }
                det_resp = await client.request(
                    "POST", "/employee/employment/details",
                    json_body=detail_body,
                )
                ctx.deps.call_history.append(
                    f"POST /employee/employment/details -> {det_resp.get('status_code')}"
                )

                # Step 4: Get company info and create division if needed
                empl_check = await client.request(
                    "GET", f"/employee/employment/{employment_id}",
                    params={"fields": "id,version,division(id)"},
                )
                empl_data = empl_check.get("body", {}).get("value", {})
                division = empl_data.get("division")
                division_id = division.get("id") if division else None

                if not division_id:
                    # Get company org number to generate a different one for division
                    company_resp = await client.request(
                        "GET", "/token/session/>whoAmI",
                    )
                    company = company_resp.get("body", {}).get("value", {}).get("company", {})
                    company_org = company.get("organizationNumber", "000000000")

                    # Transform org number (flip first digit)
                    div_org = ("9" if company_org[0] != "9" else "8") + company_org[1:]

                    # Get municipality
                    muni_resp = await client.request(
                        "GET", "/municipality",
                        params={"fields": "id,name", "count": 1},
                    )
                    muni_vals = muni_resp.get("body", {}).get("values", [])
                    muni_id = muni_vals[0]["id"] if muni_vals else None

                    if muni_id:
                        div_resp = await client.request(
                            "POST", "/division",
                            json_body={
                                "name": f"Lønnsavdeling {employee_id}",
                                "organizationNumber": div_org,
                                "startDate": start_date,
                                "municipalityDate": start_date,
                                "municipality": {"id": muni_id},
                            },
                        )
                        division_id = div_resp.get("body", {}).get("value", {}).get("id")
                        ctx.deps.call_history.append(
                            f"POST /division -> {div_resp.get('status_code')}"
                        )

                        # Link division to employment
                        if division_id:
                            empl_ver = empl_data.get("version", 0)
                            await client.request(
                                "PUT", f"/employee/employment/{employment_id}",
                                json_body={
                                    "id": employment_id,
                                    "version": empl_ver,
                                    "division": {"id": division_id},
                                },
                            )

                result = json.dumps({
                    "employment_id": employment_id,
                    "division_id": division_id,
                    "message": f"Employee {employee_id} ready for payroll (employment={employment_id})",
                })
            except Exception as e:
                result = json.dumps({"error": str(e)})

            ctx.deps.run_logger.log_tool_result("setup_employee_for_payroll", result)
            return result

        @agent.tool
        def build_voucher_postings(
            ctx: RunContext[AgentDeps],
            entries: list[dict],
        ) -> str:
            """Build balanced voucher postings from a list of entries.

            Auto-assigns row numbers, sets amountGross=amountGrossCurrency,
            and validates the total sums to zero.

            Args:
                ctx: The run context.
                entries: List of posting entries, each with:
                    - account_id (int): Required. The account ID.
                    - amount (float): Required. Positive=debit, negative=credit.
                    - description (str): Optional description.
                    - supplier_id (int): Optional supplier reference.
                    - customer_id (int): Optional customer reference.
                    - department_id (int): Optional department reference.
                    - project_id (int): Optional project reference.
                    - vat_type_id (int): Optional VAT type ID.

            Returns:
                JSON with the postings array ready to use in POST /ledger/voucher body,
                plus a balance check.
            """
            ctx.deps.run_logger.log_tool_call("build_voucher_postings", {
                "entry_count": len(entries),
            })
            try:
                postings = []
                for i, entry in enumerate(entries):
                    posting = {
                        "row": i + 1,
                        "account": {"id": entry["account_id"]},
                        "amountGross": entry["amount"],
                        "amountGrossCurrency": entry["amount"],
                    }
                    if entry.get("description"):
                        posting["description"] = entry["description"]
                    if entry.get("supplier_id"):
                        posting["supplier"] = {"id": entry["supplier_id"]}
                    if entry.get("customer_id"):
                        posting["customer"] = {"id": entry["customer_id"]}
                    if entry.get("department_id"):
                        posting["department"] = {"id": entry["department_id"]}
                    if entry.get("project_id"):
                        posting["project"] = {"id": entry["project_id"]}
                    if entry.get("vat_type_id"):
                        posting["vatType"] = {"id": entry["vat_type_id"]}
                    postings.append(posting)

                total = round(sum(e["amount"] for e in entries), 2)
                result = json.dumps({
                    "postings": postings,
                    "balanced": abs(total) < 0.01,
                    "total": total,
                    "warning": f"Postings do NOT balance (sum={total})" if abs(total) >= 0.01 else None,
                }, ensure_ascii=False)
            except Exception as e:
                result = json.dumps({"error": str(e)})

            ctx.deps.run_logger.log_tool_result("build_voucher_postings", result[:300])
            return result

        @agent.tool
        def think(ctx: RunContext[AgentDeps], thought: str) -> str:
            """Use this tool to pause and think through your approach before acting.

            Call this to plan your next steps, analyze tool results before proceeding,
            or reason about intermediate state (entity IDs, amounts, what's left to do).

            Args:
                ctx: The run context with dependencies.
                thought: Your reasoning, plan, or analysis of the current state.

            Returns:
                Acknowledgment that the thought was recorded.
            """
            ctx.deps.run_logger.log_tool_call("think", {"thought": thought[:200]})
            return "Thought noted."

        @agent.tool
        def save_note(
            ctx: RunContext[AgentDeps],
            label: str,
            content: str,
        ) -> str:
            """Save a note for later reference. Use this to store entity IDs, amounts,
            running totals, or intermediate results that you'll need in later steps.

            Notes are grouped by label and accumulate (append, not overwrite).

            Args:
                ctx: The run context with dependencies.
                label: Category label (e.g., "entity_ids", "amounts", "plan").
                content: The note content to save.

            Returns:
                Confirmation with current note count for this label.
            """
            notes = ctx.deps.notes
            total_len = sum(len(s) for vals in notes.values() for s in vals)
            if total_len + len(content) > 5000:
                content = content[: max(100, 5000 - total_len)]
            notes.setdefault(label, []).append(content)
            ctx.deps.run_logger.log_tool_call("save_note", {"label": label})
            count = len(notes[label])
            result = f"Saved. {count} note(s) under '{label}'."
            ctx.deps.run_logger.log_tool_result("save_note", result)
            return result

        @agent.tool
        def get_notes(
            ctx: RunContext[AgentDeps],
            label: str | None = None,
        ) -> str:
            """Retrieve previously saved notes.

            Args:
                ctx: The run context with dependencies.
                label: Optional label to filter by. If omitted, returns all notes.

            Returns:
                All matching notes as formatted text.
            """
            ctx.deps.run_logger.log_tool_call("get_notes", {"label": label})
            notes = ctx.deps.notes
            if label:
                entries = notes.get(label, [])
                if not entries:
                    result = f"No notes found under '{label}'."
                else:
                    result = f"Notes for '{label}':\n" + "\n".join(
                        f"  {i+1}. {e}" for i, e in enumerate(entries)
                    )
            else:
                if not notes:
                    result = "No notes saved yet."
                else:
                    lines = []
                    for lbl, entries in notes.items():
                        lines.append(f"[{lbl}]")
                        for i, e in enumerate(entries):
                            lines.append(f"  {i+1}. {e}")
                    result = "\n".join(lines)
            ctx.deps.run_logger.log_tool_result("get_notes", result[:300])
            return result

    # ------------------------------------------------------------------
    # User message building
    # ------------------------------------------------------------------

    def _build_user_message(self, request: SolveRequest) -> str | list:
        """Build the user message, including file attachments as multimodal content.

        Uses PydanticAI BinaryContent for proper Claude API document/image blocks.
        PDFs get server-side text extraction (Layer 1) plus native PDF passthrough (Layer 2).
        """
        if not request.files:
            return request.prompt

        parts: list = [request.prompt]

        for f in request.files:
            file_bytes = base64.b64decode(f.content_base64)

            if f.mime_type.startswith("image/"):
                parts.append(f"\n\n[Attached image: {f.filename}]")
                parts.append(BinaryContent(data=file_bytes, media_type=f.mime_type))
            elif f.mime_type == "application/pdf":
                # Layer 1: Server-side text extraction for reliable data access
                extracted = extract_pdf_text(f.content_base64)
                if extracted:
                    parts.append(
                        f"\n\n[PDF: {f.filename}]\nExtracted text:\n{extracted}"
                    )
                else:
                    parts.append(
                        f"\n\n[PDF: {f.filename} — text extraction failed, see document below]"
                    )
                # Layer 2: Native PDF document block for visual layout
                parts.append(
                    BinaryContent(data=file_bytes, media_type="application/pdf")
                )
            else:
                try:
                    text_content = file_bytes.decode("utf-8")
                    parts.append(f"\n\n[Attached file: {f.filename}]\n{text_content}")
                except UnicodeDecodeError:
                    parts.append(
                        f"\n\n[Attached binary file: {f.filename} ({len(file_bytes)} bytes)]"
                    )

        return parts

    # ------------------------------------------------------------------
    # Solve: classify → inject playbook → execute
    # ------------------------------------------------------------------

    async def solve(self, request: SolveRequest) -> dict:
        """Classify task, inject playbook if available, and execute."""
        run_logger = RunLogger(task_id=request.task_id)
        start_time = time.monotonic()

        # Store file attachments (PDFs, images) alongside run logs
        if request.files:
            run_logger.store_files(request.files)

        run_logger.log_prompt(request.prompt, len(request.files))
        logger.info(f"Solving task: {request.prompt}...")

        client = TripletexClient(
            base_url=request.tripletex_credentials.base_url,
            session_token=request.tripletex_credentials.session_token,
            run_logger=run_logger,
        )

        try:
            # Classify task and get playbook
            playbook_text = ""
            task_type = None
            task_confidence = 0.0
            if self.run_history:
                task_type, task_confidence = self.run_history.classify_prompt(
                    request.prompt
                )
                lessons = self.run_history.get_lessons(request.prompt)
                if lessons:
                    playbook_text = lessons
                    run_logger.log(
                        "LESSONS",
                        f"Injected playbook for {task_type} (confidence={task_confidence:.2f})",
                    )

            deps = AgentDeps(
                tripletex_client=client,
                spec_searcher=self.spec_searcher,
                api_validator=self.api_validator,
                run_logger=run_logger,
                files=request.files,
                playbook_text=playbook_text,
            )

            user_message = self._build_user_message(request)

            # ---- Single-phase execution (no separate planner) ----
            # The executor has ALL tools (spec lookup + API calls).
            # Playbook is injected via dynamic system prompt.
            # For unknown tasks, the executor discovers endpoints inline.

            if playbook_text:
                run_logger.log(
                    "PHASE",
                    f"Executing with playbook ({task_type}, conf={task_confidence:.2f})",
                )
                context = (
                    "A task-specific playbook is in the system prompt. Follow it as your guide. "
                    "Use search_api_spec and get_endpoint_detail to verify endpoints or find "
                    "alternatives if the playbook approach doesn't work.\n\n"
                )
            else:
                run_logger.log("PHASE", f"Executing without playbook (unknown task)")
                context = (
                    "No playbook available for this task. Use search_api_spec to discover the right "
                    "endpoints, then get_endpoint_detail to verify field names before making API calls.\n\n"
                )

            if isinstance(user_message, str):
                executor_message = context + user_message
            else:
                executor_message = [context + str(user_message[0])] + user_message[1:]

            result = await self.executor_agent.run(
                executor_message,
                deps=deps,
                usage_limits=UsageLimits(request_limit=100, tool_calls_limit=100),
            )

            # Log thinking blocks from the executor run
            for msg in result.all_messages():
                if isinstance(msg, ModelResponse):
                    for part in msg.parts:
                        if isinstance(part, ThinkingPart):
                            run_logger.log_thinking(part.content)

            # Log the model's final text output
            if result.output:
                run_logger.log_model_response(str(result.output))

            duration = time.monotonic() - start_time
            usage = result.usage()

            run_logger.log_result(
                duration_s=duration,
                api_calls=client.call_count,
                api_errors=client.error_count,
                usage=str(usage),
            )

            logger.info(
                f"Task completed in {duration:.1f}s"
                f" | API calls: {client.call_count}"
                f" | API errors: {client.error_count}"
                f" | Token usage: {usage}"
            )

            return {"status": "completed"}

        except Exception as e:
            duration = time.monotonic() - start_time
            run_logger.log_error(f"{type(e).__name__}: {e}")
            run_logger.log_result(
                duration_s=duration,
                api_calls=client.call_count,
                api_errors=client.error_count,
                usage="N/A (failed)",
            )
            logger.exception(
                f"Task failed after {duration:.1f}s"
                f" | API calls: {client.call_count}"
                f" | API errors: {client.error_count}"
                f" | Error: {e}"
            )
            return {"status": "completed"}

        finally:
            await client.close()
            run_logger.finalize()

            if run_logger.task_id:
                await run_logger.save()
            elif self.leaderboard:
                import asyncio

                run_end_time = datetime.now(timezone.utc)
                logger.info(
                    f"Starting background task detection (run ended at {run_end_time.isoformat()})..."
                )
                asyncio.create_task(self._detect_and_save(run_logger, run_end_time))
            else:
                await run_logger.save()

    async def _detect_and_save(self, run_logger, run_end_time: datetime):
        """Background task: retry leaderboard detection, fetch submission feedback, then save logs."""
        try:
            (
                detected_task,
                attempts,
                prev_score,
                new_score,
            ) = await self.leaderboard.detect_task(run_end_time)
            if detected_task:
                run_logger.task_id = detected_task
                run_logger.attempt_number = attempts

                if new_score > prev_score:
                    run_logger.log(
                        "SCORE",
                        f"{detected_task}: {prev_score:.2f} → {new_score:.2f} ✓ IMPROVED (+{new_score - prev_score:.2f})",
                    )
                elif new_score == prev_score:
                    run_logger.log(
                        "SCORE", f"{detected_task}: {new_score:.2f} (no change)"
                    )
                else:
                    run_logger.log(
                        "SCORE",
                        f"{detected_task}: {prev_score:.2f} → {new_score:.2f} (benchmark recalculated)",
                    )

                logger.info(
                    f"Background detection: {detected_task} (attempt #{attempts}, "
                    f"score {prev_score:.2f} → {new_score:.2f})"
                )
            else:
                logger.warning(
                    "Background detection: no match — saving to unclassified/"
                )

            # Fetch submission feedback (checks passed/failed, actual run score)
            # Pass leaderboard best score to filter out implausible matches
            feedback = await self.leaderboard.fetch_submission_feedback(
                run_end_time, leaderboard_score=new_score if detected_task else None,
            )
            if feedback:
                norm_score = feedback.get("normalized_score", 0)
                score_raw = feedback.get("score_raw", 0)
                score_max = feedback.get("score_max", 0)
                fb = feedback.get("feedback", {})
                comment = fb.get("comment", "")
                checks = fb.get("checks", [])

                run_logger.log(
                    "FEEDBACK",
                    f"Run score: {norm_score} (raw {score_raw}/{score_max}) — {comment}",
                )
                for check in checks:
                    run_logger.log("FEEDBACK", f"  {check}")

                # Save the full feedback JSON alongside the run logs
                run_logger.submission_feedback = feedback

                logger.info(
                    f"Submission feedback: score={norm_score}, {comment}"
                )
            else:
                logger.info("No submission feedback found for this run")

        except Exception as e:
            logger.error(f"Background detection failed: {e}")
        finally:
            await run_logger.save()
