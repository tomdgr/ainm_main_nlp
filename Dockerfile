FROM python:3.13-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY src/ ./src/

# Copy OpenAPI spec for the search tool
COPY docs/task_api_docs/apispec_openapi.json ./data/apispec_openapi.json

# Copy run logs for dynamic lessons from previous runs
COPY example_runs/tripletex-agent/ ./example_runs/tripletex-agent/

EXPOSE 8080

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
