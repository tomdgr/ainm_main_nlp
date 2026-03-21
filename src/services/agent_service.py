import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from anthropic import AsyncAnthropicVertex
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.messages import ModelResponse, ThinkingPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from src.models import FileAttachment, SolveRequest
from src.prompts.system_prompt import get_system_prompt

# from src.services.api_search import ApiSearchService
from src.services.api_validator import APIValidator
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
        self.model = self.sonnet_model  # Sonnet for speed (< 300s timeout)
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
            json_body: dict | None = None,
        ) -> str:
            """Make an API call to the Tripletex REST API.

            Use this to create, read, update, or delete accounting entities.

            Args:
                ctx: The run context with dependencies.
                method: HTTP method (GET, POST, PUT, DELETE).
                path: API path (e.g., "/employee", "/invoice/123/:payment").
                params: Query parameters as key-value pairs.
                json_body: Request body for POST/PUT as a dictionary.

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
                if isinstance(value, dict) and len(json.dumps(value, default=str)) > 500:
                    essential_keys = (
                        "id", "version", "name", "number", "displayName",
                        "invoiceNumber", "amount", "amountExcludingVat",
                        "amountOutstanding", "startDate", "endDate",
                        "supplierNumber", "customerNumber", "organizationNumber",
                        "email", "invoiceEmail", "firstName", "lastName",
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

    # ------------------------------------------------------------------
    # User message building
    # ------------------------------------------------------------------

    def _build_user_message(self, request: SolveRequest) -> str | list:
        """Build the user message, including file attachments as multimodal content."""
        if not request.files:
            return request.prompt

        parts: list = [request.prompt]

        for f in request.files:
            file_bytes = base64.b64decode(f.content_base64)

            if f.mime_type.startswith("image/"):
                parts.append(f"\n\n[Attached image: {f.filename}]")
                parts.append(f"<image>{f.content_base64}</image>")
            elif f.mime_type == "application/pdf":
                parts.append(f"\n\n[Attached PDF: {f.filename}]")
                parts.append(f"<pdf>{f.content_base64}</pdf>")
            else:
                try:
                    text_content = file_bytes.decode("utf-8")
                    parts.append(f"\n\n[Attached file: {f.filename}]\n{text_content}")
                except UnicodeDecodeError:
                    parts.append(
                        f"\n\n[Attached binary file: {f.filename} ({len(file_bytes)} bytes)]"
                    )

        return "\n".join(parts) if isinstance(parts[0], str) else parts

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
                task_type, task_confidence = self.run_history.classify_prompt(request.prompt)
                lessons = self.run_history.get_lessons(request.prompt)
                if lessons:
                    playbook_text = lessons
                    run_logger.log("LESSONS", f"Injected playbook for {task_type} (confidence={task_confidence:.2f})")

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
                run_logger.log("PHASE", f"Executing with playbook ({task_type}, conf={task_confidence:.2f})")
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
