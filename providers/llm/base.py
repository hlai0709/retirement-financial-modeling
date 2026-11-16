from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMCompletion:
    content: str
    provider: str
    model: str


class LLMProvider(Protocol):
    provider_name: str

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int | None = None,
    ) -> LLMCompletion: ...