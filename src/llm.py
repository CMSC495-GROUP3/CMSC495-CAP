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
import os
from abc import ABC, abstractmethod
from typing import Iterator, Literal

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


# Register new providers here. The key is the LLM_PROVIDER environment value.
_PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAIProvider,
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
