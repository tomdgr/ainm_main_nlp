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
                return (
                    "\n\n## Task-Specific Playbook (follow closely)\n\n"
                    + ctx.deps.playbook_text
                )
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
                value = result.get("body", {}).get("value")
                if (
                    isinstance(value, dict)
                    and len(json.dumps(value, default=str)) > 500
                ):
                    essential_keys = (
                        "id",
                        "version",
                        "name",
                        "number",
                        "displayName",
                        "invoiceNumber",
                        "amount",
                        "amountExcludingVat",
                        "amountOutstanding",
                        "startDate",
                        "endDate",
                        "supplierNumber",
                        "customerNumber",
                        "organizationNumber",
                        "email",
                        "invoiceEmail",
                        "firstName",
                        "lastName",
                    )
                    trimmed = {k: value[k] for k in essential_keys if k in value}
                    result["body"]["value"] = trimmed

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
            postings_json: str,
            group_by: str = "account",
        ) -> str:
            """Aggregate ledger postings by account — returns per-account sums sorted by absolute amount.

            Use this instead of manually summing postings in your reasoning. Pass the raw JSON
            response body from GET /ledger/postingByDate or similar endpoints.

            Args:
                ctx: The run context with dependencies.
                postings_json: JSON string — either the full API response body (with "values" wrapper)
                    or a raw JSON array of posting objects.
                group_by: Field to group by. Currently only "account" is supported.

            Returns:
                JSON array of {account_id, account_number, account_name, total_amount, count}
                sorted by absolute total_amount descending.
            """
            ctx.deps.run_logger.log_tool_call(
                "aggregate_postings", {"group_by": group_by}
            )
            try:
                data = json.loads(postings_json)
                postings = data.get("values", data) if isinstance(data, dict) else data

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
                    f"=== TASK PLAYBOOK ===\n{playbook_text}\n=== END PLAYBOOK ===\n\n"
                    f"Follow this playbook as your guide. Use search_api_spec and get_endpoint_detail "
                    f"to verify endpoints or find alternatives if the playbook approach doesn't work.\n\n"
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

            duration = time.monotonic() - start_time
            usage = result.usage()

            # Log thinking blocks from the executor run
            for msg in result.all_messages():
                if isinstance(msg, ModelResponse):
                    for part in msg.parts:
                        if isinstance(part, ThinkingPart):
                            run_logger.log_thinking(part.content)

            # Log the model's final text output
            if result.output:
                run_logger.log_model_response(str(result.output))

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
        """Background task: retry leaderboard detection then save logs."""
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
        except Exception as e:
            logger.error(f"Background detection failed: {e}")
        finally:
            await run_logger.save()
