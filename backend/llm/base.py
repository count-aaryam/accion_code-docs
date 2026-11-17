from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)


class LLMProvider(ABC):
    def count_tokens(self, text_or_messages: str | list[dict[str, Any]]) -> dict[str, Any]:
        text = text_or_messages if isinstance(text_or_messages, str) else "\n".join(str(item.get("content", "")) for item in text_or_messages)
        return {"tokens": max(1, (len(text) + 3) // 4), "exact": False}

    @abstractmethod
    async def generate(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        raise NotImplementedError
