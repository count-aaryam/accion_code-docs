import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from backend.llm.base import LLMProvider, LLMResponse, TokenUsage, ToolCall
from backend.settings import settings

logger = logging.getLogger(__name__)

_log_path = Path(__file__).resolve().parents[2] / "logs" / "llm.log"
_log_path.parent.mkdir(parents=True, exist_ok=True)
if not any(isinstance(handler, logging.FileHandler) for handler in logger.handlers):
    _file_handler = logging.FileHandler(_log_path, encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_file_handler)
logger.setLevel(logging.INFO)


class GeminiProvider(LLMProvider):
    endpoint = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.gemini_model
        self._semaphore = asyncio.Semaphore(settings.llm_max_concurrency)

    async def generate(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.model in {"gemini-3.8-flash", "gemini-3-flash-preview"}:
            payload["reasoning_effort"] = "low"
        if tools:
            payload["tools"] = tools
        headers = {"Authorization": f"Bearer {settings.gemini_api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        async with self._semaphore:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                response = await self._request_with_retry(client, payload, headers)
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCall(call.get("id", ""), function.get("name", ""), arguments))
        usage_data = data.get("usage") or {}
        usage = TokenUsage(usage_data.get("prompt_tokens", 0), usage_data.get("completion_tokens", 0), usage_data.get("total_tokens", 0))
        logger.info("llm_call model=%s status=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s duration_ms=%d", self.model, response.status_code, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens, int((time.perf_counter() - started) * 1000))
        return LLMResponse(message.get("content") or "", calls, usage)

    async def _request_with_retry(self, client: httpx.AsyncClient, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        for attempt in range(settings.llm_max_retries + 1):
            response = await client.post(self.endpoint, json=payload, headers=headers)
            if response.status_code < 400 or response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                return response
            if attempt >= settings.llm_max_retries:
                response.raise_for_status()
            delay = settings.llm_retry_base_seconds * (2 ** attempt)
            logger.warning("llm_retry model=%s status=%s attempt=%s delay_seconds=%s", self.model, response.status_code, attempt + 1, delay)
            await asyncio.sleep(delay)
        raise RuntimeError("LLM request retry loop ended unexpectedly")
