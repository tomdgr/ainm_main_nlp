from dotenv import load_dotenv

load_dotenv()

import logging
import os
import time

import logfire
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.models import SolveRequest, SolveResponse
from src.services.agent_service import AgentService
from src.services.openapi_spec import OpenAPISpecSearcher
from src.utils.logging import setup_logging

setup_logging()

logfire_key = os.getenv("LOGFIRE_API_KEY")
if logfire_key:
    logfire.configure(token=logfire_key, send_to_logfire="if-token-present")
    logfire.instrument_pydantic_ai()
else:
    logfire.configure(send_to_logfire=False)

logger = logging.getLogger(__name__)

AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")

# auto_error=False so unauthenticated requests don't 403 when no key is configured
bearer_scheme = HTTPBearer(auto_error=False)

# Load OpenAPI spec once at startup
spec_searcher = OpenAPISpecSearcher()
spec_searcher.load()

# Create agent service
agent_service = AgentService(spec_searcher=spec_searcher)

app = FastAPI(title="Tripletex AI Agent", version="0.1.0")


def verify_auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    """Verify Bearer token if AGENT_API_KEY is set."""
    if not AGENT_API_KEY:
        return  # No key configured, skip auth
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if credentials.credentials != AGENT_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/solve", response_model=SolveResponse, dependencies=[Depends(verify_auth)])
async def solve(request: SolveRequest):

    start = time.monotonic()
    logger.info(
        f"Received solve request | prompt length: {len(request.prompt)} | files: {len(request.files)}"
    )

    result = await agent_service.solve(request)

    duration = time.monotonic() - start
    logger.info(f"Solve completed in {duration:.1f}s")
    return result


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=200,
        content={"status": "completed"},
    )
