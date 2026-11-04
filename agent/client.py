import os

from anthropic import Anthropic

from agent.config import BASE_URL


if BASE_URL:
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

client = Anthropic(base_url=BASE_URL)