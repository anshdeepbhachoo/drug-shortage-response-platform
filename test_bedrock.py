import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    base_url="https://bedrock-mantle.us-east-1.api.aws/v1",
    api_key=os.environ["AWS_BEARER_TOKEN_BEDROCK"],
)

r = client.responses.create(
    model="openai.gpt-oss-120b",
    input="Reply with one word: ready",
    store=False,
)
print(r.output_text)