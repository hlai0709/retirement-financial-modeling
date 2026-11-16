from __future__ import annotations

import requests

from providers.llm.base import LLMCompletion

DEFAULT_MODEL = "gpt-4o"
API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider:
    provider_name = "openai"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        payload: dict = {
            "model": model or DEFAULT_MODEL,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        response = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return LLMCompletion(
            content=content,
            provider=self.provider_name,
            model=data.get("model", model or DEFAULT_MODEL),
        )