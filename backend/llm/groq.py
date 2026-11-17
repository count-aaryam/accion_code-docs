from backend.llm.gemini import GeminiProvider
from backend.settings import settings


class GroqProvider(GeminiProvider):
    endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.groq_model
        self.api_key = settings.groq_api_key
        import asyncio
        self._semaphore = asyncio.Semaphore(settings.llm_max_concurrency)

