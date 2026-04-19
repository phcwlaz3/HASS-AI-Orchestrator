"""OpenAI provider implementation for HASS-AI-Orchestrator."""

from __future__ import annotations

import logging
from typing import AsyncIterator, List, Optional

try:
    from openai import AsyncOpenAI
    from openai.types.chat import ChatCompletionMessageParam
except ImportError as e:
    raise ImportError("openai package is required: pip install openai") from e

from .base import BaseProvider, ProviderConfig, ProviderResponse

logger = logging.getLogger(__name__)

# Default timeout in seconds for API requests
DEFAULT_TIMEOUT = 60


class OpenAIProvider(BaseProvider):
    """Provider implementation for OpenAI's Chat Completion API."""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: Optional[AsyncOpenAI] = None

    @property
    def name(self) -> str:
        return "openai"

    async def initialize(self) -> None:
        """Initialize the AsyncOpenAI client."""
        api_key = self.config.api_key
        base_url = self.config.extra.get("base_url") if self.config.extra else None
        timeout = self.config.extra.get("timeout", DEFAULT_TIMEOUT) if self.config.extra else DEFAULT_TIMEOUT

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        logger.info("OpenAI provider initialized (model=%s)", self.config.model)

    async def complete(
        self,
        messages: List[dict],
        **kwargs,
    ) -> ProviderResponse:
        """Send messages to OpenAI and return a ProviderResponse.

        Args:
            messages: List of role/content dicts compatible with OpenAI API.
            **kwargs: Additional parameters forwarded to the API call.

        Returns:
            ProviderResponse with the assistant reply and usage metadata.
        """
        if self._client is None:
            await self.initialize()

        params = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        params.update(kwargs)

        # Remove None values so we don't override OpenAI defaults
        params = {k: v for k, v in params.items() if v is not None}

        logger.debug("OpenAI request: model=%s, messages=%d", params["model"], len(messages))

        response = await self._client.chat.completions.create(**params)  # type: ignore[arg-type]

        choice = response.choices[0]
        content = choice.message.content or ""

        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return ProviderResponse(
            content=content,
            provider=self.name,
            model=response.model,
            finish_reason=choice.finish_reason,
            usage=usage,
        )

    async def stream(
        self,
        messages: List[dict],
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream tokens from OpenAI one chunk at a time.

        Args:
            messages: List of role/content dicts compatible with OpenAI API.
            **kwargs: Additional parameters forwarded to the API call.

        Yields:
            String chunks of the assistant response as they arrive.
        """
        if self._client is None:
            await self.initialize()

        params = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True,
        }
        params.update(kwargs)
        params = {k: v for k, v in params.items() if v is not None}

        logger.debug("OpenAI stream request: model=%s, messages=%d", params["model"], len(messages))

        async with await self._client.chat.completions.create(**params) as stream:  # type: ignore[arg-type]
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
