from anthropic import AnthropicVertex

from dotenv import load_dotenv
import os

load_dotenv()

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")

client = AnthropicVertex(region="global", project_id=PROJECT_ID)
message = client.messages.create(
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello! Can you help me?"}],
    model="claude-opus-4-6",
)
print(message.content[0].text)
