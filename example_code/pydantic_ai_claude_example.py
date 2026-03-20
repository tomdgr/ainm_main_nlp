"""
This is the main example file that the code should inspire from.
"""

from dotenv import load_dotenv
import os

from anthropic import AsyncAnthropicVertex
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

load_dotenv()

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")

vertex_client = AsyncAnthropicVertex(
    project_id=PROJECT_ID,
    region="global",  # or a specific region like "europe-west1"
)

provider = AnthropicProvider(anthropic_client=vertex_client)
model = AnthropicModel("claude-opus-4-6", provider=provider)

agent = Agent(model)
result = agent.run_sync("What is the capital of Norway?")

print(result.output)
