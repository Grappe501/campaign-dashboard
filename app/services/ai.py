from __future__ import annotations

from typing import Optional
from openai import OpenAI
from ..config import settings

_client: Optional[OpenAI] = None

def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set in .env")
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client
