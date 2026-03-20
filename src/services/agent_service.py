import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field

from anthropic import AsyncAnthropicVertex
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.messages import ModelResponse, ThinkingPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from src.models import FileAttachment, SolveRequest
from src.prompts.system_prompt import get_system_prompt
from src.services.api_search import ApiSearchService
from src.services.openapi_spec import OpenAPISpecSearcher
from src.services.tripletex_client import TripletexClient
from src.utils.logging import RunLogger

logger = logging.getLogger(__name__)


@dataclass
class AgentDeps:
    """Dependencies injected into the agent for each solve request."""

    tripletex_client: TripletexClient
    spec_searcher: OpenAPISpecSearcher
    api_search: ApiSearchService
    run_logger: RunLogger
    files: list[FileAttachment] = field(default_factory=list)


class AgentService:
    """PydanticAI agent that interprets accounting prompts and executes Tripletex API calls."""

    def __init__(
        self, spec_searcher: OpenAPISpecSearcher, api_search: ApiSearchService
    ):
        self.spec_searcher = spec_searcher
        self.api_search = api_search
        self.model = self._create_model()
        self.agent = self._create_agent()

    def _create_model(self) -> AnthropicModel:
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "ainm26osl-708")
        region = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

        vertex_client = AsyncAnthropicVertex(
            project_id=project_id,
            region=region,
        )
        provider = AnthropicProvider(anthropic_client=vertex_client)
        return AnthropicModel("claude-opus-4-6", provider=provider)

    def _create_agent(self) -> Agent:
        agent = Agent(
            self.model,
            system_prompt=get_system_prompt(),
            deps_type=AgentDeps,
            model_settings={
                "temperature": 1.0,  # Required when thinking is enabled
                "max_tokens": 16000,
                "anthropic_thinking": {
                    "type": "enabled",
                    "budget_tokens": 8000,
                },
                "anthropic_cache_instructions": True,
                "anthropic_cache_tool_definitions": True,
                "parallel_tool_calls": True,
            },
        )
        self._register_tools(agent)
        return agent

    def _register_tools(self, agent: Agent):

        @agent.tool
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

            result = await ctx.deps.tripletex_client.request(
                method=method,
                path=path,
                params=params,
                json_body=json_body,
            )
            result_str = json.dumps(result, ensure_ascii=False, default=str)
            ctx.deps.run_logger.log_tool_result("tripletex_api", result_str)
            return result_str

        @agent.tool
        def search_api_spec(
            ctx: RunContext[AgentDeps],
            query: str,
        ) -> str:
            """Search the Tripletex OpenAPI specification for endpoint details.

            Use this ONLY when you need to find an endpoint not covered in your system prompt reference.

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

        """
            NOTE: A new search tool. Currently testing search_api under but for now the search_api_spec seems to work as good
        """
        # @agent.tool
        # def search_api(
        #     ctx: RunContext[AgentDeps],
        #     query: str,
        # ) -> str:
        #     """Search the Tripletex API for relevant endpoints.

        #     Returns endpoints grouped by path with all available HTTP methods, parameters, and body schema names.
        #     Uses both keyword and semantic search for best results.

        #     Args:
        #         ctx: The run context with dependencies.
        #         query: Search query — entity type, action, or concept (e.g., "supplier", "invoice payment", "employee employment").

        #     Returns:
        #         Grouped endpoint listing with methods, summaries, params, and schema names.
        #     """
        #     ctx.deps.run_logger.log_tool_call("search_api", {"query": query})
        #     result = ctx.deps.api_search.search(query)
        #     ctx.deps.run_logger.log_tool_result("search_api", result)
        #     return result

        @agent.tool
        def get_endpoint_detail(
            ctx: RunContext[AgentDeps],
            path: str,
            method: str,
        ) -> str:
            """Get full details for a specific Tripletex API endpoint.

            Use this to verify exact field names and types before making an API call
            to an unfamiliar endpoint.

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

    async def solve(self, request: SolveRequest) -> dict:
        """Execute an accounting task described in the prompt."""
        run_logger = RunLogger(task_id=request.task_id)
        start_time = time.monotonic()

        run_logger.log_prompt(request.prompt, len(request.files))
        logger.info(f"Solving task: {request.prompt}...")

        client = TripletexClient(
            base_url=request.tripletex_credentials.base_url,
            session_token=request.tripletex_credentials.session_token,
            run_logger=run_logger,
        )

        try:
            deps = AgentDeps(
                tripletex_client=client,
                spec_searcher=self.spec_searcher,
                api_search=self.api_search,
                run_logger=run_logger,
                files=request.files,
            )

            user_message = self._build_user_message(request)

            result = await self.agent.run(
                user_message,
                deps=deps,
                usage_limits=UsageLimits(request_limit=100, tool_calls_limit=50),
            )

            duration = time.monotonic() - start_time
            usage = result.usage()

            # Log thinking blocks from the agent run
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
            await run_logger.save()
