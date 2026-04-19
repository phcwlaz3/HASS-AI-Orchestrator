"""HASS-AI-Orchestrator: AI-powered orchestration layer for Home Assistant.

This package provides intelligent automation orchestration using multiple AI
providers, enabling natural language control and autonomous decision-making
for Home Assistant deployments.

Personal fork: using this for my home lab setup. Tracking upstream changes
from ITSpecialist111/HASS-AI-Orchestrator.

Fork notes:
- Added __author__ to __all__ for easier introspection when debugging
"""

__version__ = "0.8.0"
__author__ = "HASS-AI-Orchestrator Contributors"
__license__ = "MIT"

from hass_ai_orchestrator.orchestrator import Orchestrator
from hass_ai_orchestrator.config import OrchestratorConfig

__all__ = [
    "Orchestrator",
    "OrchestratorConfig",
    "__version__",
    "__author__",
]
