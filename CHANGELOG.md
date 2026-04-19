# Changelog
<br>

## [0.9.45] - 2025-12-22
### Fixed
- **HA Connectivity Robustness**: Improved error logging in `ha_client.py` with full URI and exception details. Added a startup wait period in `main.py` to prevent race conditions during early ingestion.
- **Custom HA URL Support**: Modified `run.sh` to allow user-defined `HA_URL` (from `options.json`) to take precedence even when a Long-Lived Access Token is provided.
- **Improved Reliability**: Architect suggestions and Knowledge Base ingestion now handle connection delays more gracefully.
<br>
<br>

## [0.9.44] - 2025-12-22
### Fixed
- **Chat Tool Execution**: Resolved `UnboundLocalError` (cannot access local variable 'params') when the AI Assistant triggers tools.
- **Agent Persistence**: Standardized `agents.yaml` pathing to ensure agents created through the UI are saved to the persistent `/config/agents.yaml` in Home Assistant Add-on environments.
<br>
<br>

## [0.9.43] - 2025-12-22
### Added
- **Gemini LLM Integration**: Added world-class LLM support for visual dashboard generation using Google Gemini.
- **Model Choice**: Users can now choose between local Ollama and Gemini (highly recommended for high-fidelity designs).
- **Robotics Preview Model**: Specifically added support for `gemini-robotics-er-1.5-preview` for advanced spatial and thermal visualizations.
- **Integration Settings**: New configuration fields in the UI for Gemini API Key, Model Selection, and a prioritization toggle.
- **Runtime Updates**: Gemini settings can be updated in-memory from the UI without requiring a full server restart.
<br>

## [0.9.42] - 2025-12-22
### Added
- **AI Visual Dashboard (Dynamic)**: Fully integrated natural language dashboard generation. Users can now command the dashboard style and focus via chat or a new dedicated UI tab.
- **Dynamic AI Prompting**: The Orchestrator now uses specific user instructions (e.g., "cyberpunk style", "security-focused") to architect the dashboard's HTML/CSS.
- **Background Refresh**: Implemented a periodic background loop that refreshes dashboard data every 5 minutes while preserving the user's requested aesthetic.
- **Direct UI Integration**: Dashboard is now a first-class citizen of the main UI, rendered via iframe with dedicated refresh controls.
### Fixed
- **Windows Pathing**: Resolved path normalization issues for `dynamic.html` on Windows, ensuring reliable dashboard file retrieval outside the Add-on environment.
- **Connectivity Guards**: Added safeguards to ensure Home Assistant is connected before attempting dashboard generation, preventing empty "no results" views.

## [0.9.41] - 2025-12-21
### Fixed
- **Docker Image Integrity**: Updated `Dockerfile` to correctly include `agents.yaml`, `skills/`, and `translations/` in the build, resolving issues with missing agents and tools in the Add-on environment.

## [0.9.40] - 2025-12-21
### Fixed
- **Connectivity Fallback**: Implemented automatic fallback to Direct Core Access (`http://homeassistant:8123`) when

---
<!-- Personal fork notes:
  - I changed the dashboard background refresh interval from 5 min to 2 min in main.py
    to get snappier updates on my local setup. May revert if it causes CPU issues.
-->
