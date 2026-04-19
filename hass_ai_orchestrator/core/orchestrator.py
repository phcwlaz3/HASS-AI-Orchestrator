"""Core orchestrator module for HASS-AI-Orchestrator.

Manages AI model routing, conversation context, and Home Assistant
integration pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorConfig:
    """Configuration for the AI Orchestrator."""

    hass_url: str
    hass_token: str
    default_model: str = "gpt-4o-mini"
    max_context_length: int = 50  # increased from 20 - 20 felt too short for longer conversations
    timeout: int = 30
    enable_memory: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    """Represents a single conversation message."""

    role: str  # "user", "assistant", or "system"
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    """Central orchestrator that routes requests between AI providers
    and Home Assistant.
    """

    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config
        self._conversation_history: List[Message] = []
        self._providers: Dict[str, Any] = {}
        self._running = False
        logger.info(
            "Orchestrator initialised (model=%s, hass=%s)",
            config.default_model,
            config.hass_url,
        )

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def register_provider(self, name: str, provider: Any) -> None:
        """Register an AI provider under *name*."""
        if name in self._providers:
            logger.warning("Overwriting existing provider '%s'", name)
        self._providers[name] = provider
        logger.debug("Provider registered: %s", name)

    def get_provider(self, name: Optional[str] = None) -> Any:
        """Return provider by name, falling back to the default model."""
        key = name or self.config.default_model
        provider = self._providers.get(key)
        if provider is None:
            raise KeyError(f"No provider registered for '{key}'")
        return provider

    # ------------------------------------------------------------------
    # Conversation context
    # ------------------------------------------------------------------

    def add_message(self, role: str, content: str, **metadata: Any) -> None:
        """Append a message to the conversation history."""
        self._conversation_history.append(
            Message(role=role, content=content, metadata=metadata)
        )
        # Trim to configured context window
        if len(self._conversation_history) > self.config.max_context_length:
            self._conversation_history = self._conversation_history[
                -self.config.max_context_length :
            ]

    def get_history(self) -> List[Dict[str, str]]:
        """Return conversation history as plain dicts (suitable for API calls)."""
