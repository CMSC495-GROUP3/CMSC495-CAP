"""Model provider interface.

Vendor lock-in mitigation from the proposal: every model call in this codebase
goes through the LLMProvider interface below, so swapping OpenAI for a
self-hosted model means writing one new subclass and changing one environment
variable. No module outside this file names a vendor or a model.

Two logical roles are exposed instead of concrete model names:

    "answer"  — the grounded response shown to the user. Quality matters most.
    "utility" — cheap internal calls (query rewriting, follow-up suggestions).
                A smaller, faster model is appropriate here.

A provider maps those roles onto whatever models it actually has.
"""

import hashlib
import math
import os
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Literal

ModelRole = Literal["answer", "utility"]

Message = dict  # {"role": "system" | "user" | "assistant", "content": str}


class LLMProvider(ABC):
    """The contract every provider must satisfy.

    Implement all four members to add a provider. Nothing else in the codebase
    needs to change.
    """

    name: str

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return the embedding vector for a single piece of text.

        The dimensionality must match the Atlas Vector Search index. Changing
        providers therefore means re-embedding the corpus and rebuilding the
        index — see the migration note in the README.
        """

    @abstractmethod
    def embedding_dimensions(self) -> int:
        """Vector length this provider's embedding model produces."""

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        *,
        role: ModelRole = "utility",
        temperature: float = 0.0,
    ) -> str:
        """Return a complete response as a single string."""

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        *,
        role: ModelRole = "answer",
        temperature: float = 0.0,
    ) -> Iterator[str]:
        """Yield response text incrementally, one delta at a time."""

    # ── Cache fingerprints ────────────────────────────────────────────────────
    # Cache keys must change when the underlying model changes, or a model swap
    # would serve results produced by the previous one. These exist so that
    # cache code never has to know a model name — that stays in this file.
    #
    # Both have working defaults, so adding a provider does not require writing
    # them. Override when a provider can be configured with different models.

    def embedding_fingerprint(self) -> str:
        """Identifies the embedding model, for the embedding cache key."""
        return self.name

    def answer_fingerprint(self) -> str:
        """Identifies the answer model, for the answer cache key."""
        return self.name


class OpenAIProvider(LLMProvider):
    """OpenAI-backed implementation — the default for the pilot."""

    name = "openai"

    # Model choice lives here and nowhere else. Overridable so the models can be
    # changed without touching code.
    ANSWER_MODEL = os.getenv("OPENAI_ANSWER_MODEL", "gpt-4o")
    UTILITY_MODEL = os.getenv("OPENAI_UTILITY_MODEL", "gpt-4o-mini")
    EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    EMBEDDING_DIMENSIONS = int(os.getenv("OPENAI_EMBEDDING_DIMENSIONS", "1536"))

    def __init__(self) -> None:
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required when LLM_PROVIDER=openai. "
                "Set it in .env — see .env.example."
            )
        self._client = OpenAI(api_key=api_key)

    def _model_for(self, role: ModelRole) -> str:
        return self.ANSWER_MODEL if role == "answer" else self.UTILITY_MODEL

    def embedding_fingerprint(self) -> str:
        return f"{self.name}:{self.EMBEDDING_MODEL}"

    def answer_fingerprint(self) -> str:
        return f"{self.name}:{self.ANSWER_MODEL}"

    def embed(self, text: str) -> list[float]:
        response = self._client.embeddings.create(
            model=self.EMBEDDING_MODEL,
            input=text,
        )
        return response.data[0].embedding

    def embedding_dimensions(self) -> int:
        return self.EMBEDDING_DIMENSIONS

    def complete(
        self,
        messages: list[Message],
        *,
        role: ModelRole = "utility",
        temperature: float = 0.0,
    ) -> str:
        response = self._client.chat.completions.create(
            model=self._model_for(role),
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    def stream(
        self,
        messages: list[Message],
        *,
        role: ModelRole = "answer",
        temperature: float = 0.0,
    ) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=self._model_for(role),
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


class FakeProvider(LLMProvider):
    """Deterministic offline provider. No network, no API key, no cost.

    Exists so the application can be run, tested, and load-tested without
    spending money or depending on OpenAI availability. Delays are configurable
    so a fake generation takes roughly as long as a real one — which is what
    makes it useful for measuring concurrency, where the thing that matters is
    how long a request occupies a worker, not what it says.

    The embeddings are deterministic but meaningless: two texts that are
    semantically identical will not land near each other. Any similarity score
    computed from them is noise, so do not use this provider to evaluate
    retrieval quality or to tune SIMILARITY_THRESHOLD.
    """

    name = "fake"

    STREAM_DELAY_MS = int(os.getenv("FAKE_STREAM_DELAY_MS", "20"))
    UTILITY_DELAY_MS = int(os.getenv("FAKE_UTILITY_DELAY_MS", "300"))
    EMBED_DELAY_MS = int(os.getenv("FAKE_EMBED_DELAY_MS", "50"))
    DIMENSIONS = int(os.getenv("FAKE_EMBED_DIMENSIONS", "1536"))

    ANSWER = (
        "Based on the policy documents provided, full-time employees accrue 15 days "
        "of paid time off per year for the first two years of service, rising to 20 "
        "days from year three and 25 days from year six. Accrual begins on your first "
        "day and there is no waiting period before you may use it. You may carry a "
        "maximum of 10 unused days into the following calendar year; anything above "
        "that is forfeited on December 31. This is drawn from the Paid Time Off (PTO) "
        "Policy, effective 2026-01-01. For absences longer than five consecutive "
        "business days you will also need approval from People Operations."
    )

    def __init__(self) -> None:
        if os.getenv("APP_ENV", "").lower() == "production":
            raise RuntimeError(
                "LLM_PROVIDER=fake refuses to start with APP_ENV=production. "
                "A fake provider silently answering real policy questions would be "
                "worse than an outage."
            )

    @staticmethod
    def _sleep(milliseconds: int) -> None:
        if milliseconds > 0:
            time.sleep(milliseconds / 1000)

    def embed(self, text: str) -> list[float]:
        self._sleep(self.EMBED_DELAY_MS)
        # Deterministic pseudo-random unit vector seeded by the text, so repeated
        # calls agree with each other and runs are reproducible.
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        rng = random.Random(seed)
        vector = [rng.uniform(-1.0, 1.0) for _ in range(self.DIMENSIONS)]
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embedding_dimensions(self) -> int:
        return self.DIMENSIONS

    def complete(
        self,
        messages: list[Message],
        *,
        role: ModelRole = "utility",
        temperature: float = 0.0,
    ) -> str:
        if role == "answer":
            self._sleep(self.STREAM_DELAY_MS * len(self.ANSWER.split()))
            return self.ANSWER
        self._sleep(self.UTILITY_DELAY_MS)
        # Utility calls ask for three newline-separated questions.
        return (
            "How do I request time off?\n"
            "What happens to unused days when I leave?\n"
            "Do company holidays count against my balance?"
        )

    def stream(
        self,
        messages: list[Message],
        *,
        role: ModelRole = "answer",
        temperature: float = 0.0,
    ) -> Iterator[str]:
        for index, word in enumerate(self.ANSWER.split()):
            self._sleep(self.STREAM_DELAY_MS)
            yield word if index == 0 else f" {word}"


# Register new providers here. The key is the LLM_PROVIDER environment value.
_PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "fake": FakeProvider,
}

_instance: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """Return the configured provider, constructing it once per process.

    Construction is lazy so that importing this module never requires
    credentials — useful for tests and for tooling that only needs the config.
    """
    global _instance
    if _instance is not None:
        return _instance

    key = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    provider_cls = _PROVIDERS.get(key)
    if provider_cls is None:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER {key!r}. Available: {', '.join(sorted(_PROVIDERS))}. "
            f"To add one, subclass LLMProvider in src/llm.py and register it in _PROVIDERS."
        )

    _instance = provider_cls()
    return _instance
