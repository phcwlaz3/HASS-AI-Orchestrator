"""Base provider interface for AI Orchestrator.

All AI providers (OpenAI, Anthropic, local models, etc.) must implement
this abstract base class to integrate with the orchestrator.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional


@dataclass
class ProviderConfig:
    """Configuration for an AI provider."""

    name: str
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    timeout: int = 30
    max_retries: int = 3
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResponse:
    """Standardized response from an AI provider."""

    content: str
    provider: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    cached: bool = False


class BaseProvider(ABC):
    """Abstract base class for all AI providers.

    Subclasses must implement `complete` and optionally `stream`.
    The orchestrator uses this interface to route messages to the
    appropriate backend without coupling to any specific API.
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._initialized = False

    @property
    def name(self) -> str:
        """Return the provider name."""
        return self.config.name

    @property
    def model(self) -> str:
        """Return the active model identifier."""
        return self.config.model

    async def initialize(self) -> None:
        """Perform any async setup (e.g. verify credentials, warm up connection).

        Called once by the orchestrator before the provider handles requests.
        Override in subclasses as needed.
        """
        self._initialized = True

    async def shutdown(self) -> None:
        """Release resources held by this provider.

        Called by the orchestrator during graceful shutdown.
        Override in subclasses as needed.
        """
        self._initialized = False

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ProviderResponse:
        """Send a list of chat messages and return a single completion.

        Args:
            messages: List of role/content dicts, e.g.
                      [{"role": "user", "content": "Hello"}]
            **kwargs: Provider-specific overrides (temperature, max_tokens, …)

        Returns:
            A :class:`ProviderResponse` with the generated text.
        """

    async def stream(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream completion tokens as they are generated.

        Default implementation falls back to a single `complete` call.
        Providers that support native streaming should override this.
        """
        response = await self.complete(messages, **kwargs)
        yield response.content

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} name={self.name!r} model={self.model!r}>"
