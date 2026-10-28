import asyncio
from types import SimpleNamespace

import httpx
import pytest

from backend.llm.gemini import GeminiProvider
from backend.settings import settings


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test")
            raise httpx.HTTPStatusError("request failed", request=request, response=self)


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    async def post(self, *args, **kwargs):
        self.calls += 1
        return next(self.responses)


@pytest.mark.asyncio
async def test_429_retries_with_backoff(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_retries", 2)
    monkeypatch.setattr(settings, "llm_retry_base_seconds", 0)
    client = FakeClient([FakeResponse(429), FakeResponse(429), FakeResponse(200)])
    provider = GeminiProvider()

    response = await provider._request_with_retry(client, {}, {})

    assert response.status_code == 200
    assert client.calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_auth_errors_are_not_retried(monkeypatch, status_code):
    monkeypatch.setattr(settings, "llm_max_retries", 3)
    client = FakeClient([FakeResponse(status_code)])
    provider = GeminiProvider()

    with pytest.raises(httpx.HTTPStatusError):
        await provider._request_with_retry(client, {}, {})

    assert client.calls == 1


@pytest.mark.asyncio
async def test_retry_limit_stops_permanent_5xx(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_retries", 2)
    monkeypatch.setattr(settings, "llm_retry_base_seconds", 0)
    client = FakeClient([FakeResponse(500), FakeResponse(500), FakeResponse(500)])
    provider = GeminiProvider()

    with pytest.raises(httpx.HTTPStatusError):
        await provider._request_with_retry(client, {}, {})

    assert client.calls == 3


@pytest.mark.asyncio
async def test_concurrency_limit(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_concurrency", 2)
    monkeypatch.setattr(settings, "llm_timeout_seconds", 10)
    provider = GeminiProvider()
    active = 0
    maximum = 0

    async def fake_request(client, payload, headers):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}], "usage": {}})

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)
    results = await asyncio.gather(*[provider.generate([{"role": "user", "content": "hi"}]) for _ in range(8)])

    assert len(results) == 8
    assert maximum <= 2
