from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from dotenv import load_dotenv

load_dotenv()

provider = GoogleProvider(vertexai=True)
model = GoogleModel("gemini-3-pro-preview", provider=provider)
agent = Agent(model)
result = agent.run_sync("What is the capital of Norway?")
print(result)


"""
DOCS FROM  https://ai.pydantic.dev/models/google/#logprobs


Google
The GoogleModel is a model that uses the google-genai package under the hood to access Google's Gemini models via both the Generative Language API and Vertex AI.

Install
To use GoogleModel, you need to either install pydantic-ai, or install pydantic-ai-slim with the google optional group:


pip
uv

uv add "pydantic-ai-slim[google]"

Configuration
GoogleModel lets you use Google's Gemini models through their Generative Language API (generativelanguage.googleapis.com) or Vertex AI API (*-aiplatform.googleapis.com).

API Key (Generative Language API)
To use Gemini via the Generative Language API, go to aistudio.google.com and create an API key.

Once you have the API key, set it as an environment variable:


export GOOGLE_API_KEY=your-api-key
You can then use GoogleModel by name (where GLA stands for Generative Language API):


With Pydantic AI Gateway
Directly to Provider API
Learn about Gateway

from pydantic_ai import Agent

agent = Agent('gateway/gemini:gemini-3-pro-preview')
...

Or you can explicitly create the provider:


from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

provider = GoogleProvider(api_key='your-api-key')
model = GoogleModel('gemini-3-pro-preview', provider=provider)
agent = Agent(model)
...
Vertex AI (Enterprise/Cloud)
If you are an enterprise user, you can also use GoogleModel to access Gemini via Vertex AI.

This interface has a number of advantages over the Generative Language API:

The VertexAI API comes with more enterprise readiness guarantees.
You can purchase provisioned throughput with Vertex AI to guarantee capacity.
If you're running Pydantic AI inside GCP, you don't need to set up authentication, it should "just work".
You can decide which region to use, which might be important from a regulatory perspective, and might improve latency.
You can authenticate using application default credentials, a service account, or an API key.

Whichever way you authenticate, you'll need to have Vertex AI enabled in your GCP account.

Application Default Credentials
If you have the gcloud CLI installed and configured, you can use GoogleProvider in Vertex AI mode by name:


With Pydantic AI Gateway
Directly to Provider API
Learn about Gateway

from pydantic_ai import Agent

agent = Agent('gateway/google-vertex:gemini-3-pro-preview')
...

Or you can explicitly create the provider and model:


from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

provider = GoogleProvider(vertexai=True)
model = GoogleModel('gemini-3-pro-preview', provider=provider)
agent = Agent(model)
...
Service Account
To use a service account JSON file, explicitly create the provider and model:

google_model_service_account.py

from google.oauth2 import service_account

from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

credentials = service_account.Credentials.from_service_account_file(
    'path/to/service-account.json',
    scopes=['https://www.googleapis.com/auth/cloud-platform'],
)
provider = GoogleProvider(credentials=credentials, project='your-project-id')
model = GoogleModel('gemini-3-flash-preview', provider=provider)
agent = Agent(model)
...
API Key
To use Vertex AI with an API key, create a key and set it as an environment variable:


export GOOGLE_API_KEY=your-api-key
You can then use GoogleModel in Vertex AI mode by name:


With Pydantic AI Gateway
Directly to Provider API
Learn about Gateway

from pydantic_ai import Agent

agent = Agent('gateway/google-vertex:gemini-3-pro-preview')
...

Or you can explicitly create the provider and model:


from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

provider = GoogleProvider(vertexai=True, api_key='your-api-key')
model = GoogleModel('gemini-3-pro-preview', provider=provider)
agent = Agent(model)
...
Customizing Location or Project
You can specify the location and/or project when using Vertex AI:

google_model_location.py

from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

provider = GoogleProvider(vertexai=True, location='asia-east1', project='your-gcp-project-id')
model = GoogleModel('gemini-3-pro-preview', provider=provider)
agent = Agent(model)
...
Model Garden
You can access models from the Model Garden that support the generateContent API and are available under your GCP project, including but not limited to Gemini, using one of the following model_name patterns:

{model_id} for Gemini models
{publisher}/{model_id}
publishers/{publisher}/models/{model_id}
projects/{project}/locations/{location}/publishers/{publisher}/models/{model_id}

from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

provider = GoogleProvider(
    project='your-gcp-project-id',
    location='us-central1',  # the region where the model is available
)
model = GoogleModel('meta/llama-3.3-70b-instruct-maas', provider=provider)
agent = Agent(model)
...
Custom HTTP Client
You can customize the GoogleProvider with a custom httpx.AsyncClient:


from httpx import AsyncClient

from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

custom_http_client = AsyncClient(timeout=30)
model = GoogleModel(
    'gemini-3-pro-preview',
    provider=GoogleProvider(api_key='your-api-key', http_client=custom_http_client),
)
agent = Agent(model)
...
Document, Image, Audio, and Video Input
GoogleModel supports multi-modal input, including documents, images, audio, and video.

YouTube video URLs can be passed directly to Google models:

youtube_input.py

from pydantic_ai import Agent, VideoUrl
from pydantic_ai.models.google import GoogleModel

agent = Agent(GoogleModel('gemini-3-flash-preview'))
result = agent.run_sync(
    [
        'What is this video about?',
        VideoUrl(url='https://www.youtube.com/watch?v=dQw4w9WgXcQ'),
    ]
)
print(result.output)
Files can be uploaded via the Files API and passed as URLs:

file_upload.py

from pydantic_ai import Agent, DocumentUrl
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

provider = GoogleProvider()
file = provider.client.files.upload(file='pydantic-ai-logo.png')
assert file.uri is not None

agent = Agent(GoogleModel('gemini-3-flash-preview', provider=provider))
result = agent.run_sync(
    [
        'What company is this logo from?',
        DocumentUrl(url=file.uri, media_type=file.mime_type),
    ]
)
print(result.output)
See the input documentation for more details and examples.

Model settings
You can customize model behavior using GoogleModelSettings:


from google.genai.types import HarmBlockThreshold, HarmCategory

from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings

settings = GoogleModelSettings(
    temperature=0.2,
    max_tokens=1024,
    google_thinking_config={'thinking_level': 'low'},
    google_safety_settings=[
        {
            'category': HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            'threshold': HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
        }
    ]
)
model = GoogleModel('gemini-3-pro-preview')
agent = Agent(model, model_settings=settings)
...
Configure thinking
Gemini 3 models use thinking_level to control thinking behavior:


from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings

# Set thinking level for Gemini 3 models
model_settings = GoogleModelSettings(google_thinking_config={'thinking_level': 'low'})  # 'low' or 'high'
model = GoogleModel('gemini-3-flash-preview')
agent = Agent(model, model_settings=model_settings)
...
For older models (pre-Gemini 3), you can use thinking_budget instead:


from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings

# Disable thinking on older models by setting budget to 0
model_settings = GoogleModelSettings(google_thinking_config={'thinking_budget': 0})
model = GoogleModel('gemini-2.5-flash')  # Older model
agent = Agent(model, model_settings=model_settings)
...
Check out the Gemini API docs for more on thinking.

Safety settings
You can customize the safety settings by setting the google_safety_settings field.


from google.genai.types import HarmBlockThreshold, HarmCategory

from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings

model_settings = GoogleModelSettings(
    google_safety_settings=[
        {
            'category': HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            'threshold': HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
        }
    ]
)
model = GoogleModel('gemini-3-flash-preview')
agent = Agent(model, model_settings=model_settings)
...
See the Gemini API docs for more on safety settings.

Logprobs
You can return logprobs from the model in your response by setting google_logprobs and google_top_logprobs in the GoogleModelSettings.

This feature is only supported for non-streaming requests and Vertex AI.


from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.providers.google import GoogleProvider

model_settings = GoogleModelSettings(
    google_logprobs=True, google_top_logprobs=2,
)

model = GoogleModel(
    model_name='gemini-2.5-flash',
    provider=GoogleProvider(location='europe-west1', vertexai=True),
)
agent = Agent(model, model_settings=model_settings)

result = agent.run_sync('Your prompt here')
# Access logprobs from provider_details
logprobs = result.response.provider_details.get('logprobs')
avg_logprobs = result.response.provider_details.get('avg_logprobs')
See the Google Dev Blog for more information.
"""
