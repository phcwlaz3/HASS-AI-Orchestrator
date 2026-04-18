"""
Smoke tests for agent initialization and basic functionality.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.mark.smoke
class TestAgentSmoke:
    """Smoke tests for agent framework"""

    @pytest.fixture
    def mock_skills_file(self, tmp_path):
        """Create a mock SKILLS.md file and decision dir"""
        skills_dir = tmp_path / "skills" / "heating"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skills_file = skills_dir / "SKILLS.md"

        skills_content = """# Heating Agent

## 1. Identity
Test heating agent

## 2. Controllable Entities
- climate.test_room

## 3. Observable Entities
- sensor.outdoor_temp

## 4. Available Tools
- set_temperature
- get_climate_state

## 5. Decision Criteria
Maintain comfort at 19-22°C

## 6. Example Scenarios
Test scenario

## 7. Performance Targets
95% accuracy
"""
        skills_file.write_text(skills_content)
        return str(skills_file)

    @pytest.fixture
    def decision_dir(self, tmp_path):
        """Create a temporary decisions directory"""
        d = tmp_path / "decisions"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def _create_agent(self, mock_ha_client, mock_skills_file, decision_dir,
                      mock_ollama_client=None, decision_interval=120):
        """Helper to create a HeatingAgent with mocked dependencies"""
        from agents.heating_agent import HeatingAgent
        from mcp_server import MCPServer

        mcp = MCPServer(mock_ha_client, dry_run=True)

        with patch('agents.base_agent.ollama.Client') as mock_ollama:
            mock_ollama.return_value = mock_ollama_client or MagicMock()
            with patch('agents.base_agent.Path') as mock_path_cls:
                # Make Path behave normally but redirect /data/decisions and skills path
                real_path = Path

                def path_side_effect(p):
                    p_str = str(p)
                    if p_str == "/app/skills/heating/SKILLS.md":
                        return real_path(mock_skills_file)
                    if p_str == "/data/decisions":
                        return real_path(decision_dir)
                    return real_path(p_str)

                mock_path_cls.side_effect = path_side_effect

                agent = HeatingAgent(
                    mcp_server=mcp,
                    ha_client=mock_ha_client,
                    heating_entities=["climate.test_room"],
                    model_name="test-model",
                    decision_interval=decision_interval
                )
        return agent

    @pytest.mark.asyncio
    async def test_heating_agent_initialization(self, mock_ha_client, mock_skills_file, decision_dir):
        """Test Heating Agent can be initialized"""
        agent = self._create_agent(mock_ha_client, mock_skills_file, decision_dir,
                                   decision_interval=10)

        assert agent is not None
        assert agent.agent_id == "heating"
        assert agent.name == "Heating Agent"
        assert agent.model_name == "test-model"
        assert agent.decision_interval == 10
        assert len(agent.heating_entities) == 1

    @pytest.mark.asyncio
    async def test_agent_gather_context(self, mock_ha_client, mock_skills_file, decision_dir):
        """Test agent can gather context from HA"""
        agent = self._create_agent(mock_ha_client, mock_skills_file, decision_dir)

        context = await agent.gather_context()

        assert context is not None
        assert "timestamp" in context
        assert "climate_states" in context
        assert "sensors" in context
        assert "time_of_day" in context
        assert "climate.test_room" in context["climate_states"]

    @pytest.mark.asyncio
    async def test_agent_decide(self, mock_ha_client, mock_ollama_client, mock_skills_file, decision_dir):
        """Test agent can make decisions"""
        agent = self._create_agent(mock_ha_client, mock_skills_file, decision_dir,
                                   mock_ollama_client=mock_ollama_client)

        context = {"climate_states": {}, "sensors": {}, "time_of_day": "morning", "timestamp": "2025-01-01T10:00:00"}
        decision = await agent.decide(context)

        assert decision is not None
        assert "reasoning" in decision
        assert "actions" in decision
        assert isinstance(decision["actions"], list)

    @pytest.mark.asyncio
    async def test_agent_execute_empty_actions(self, mock_ha_client, mock_skills_file, decision_dir):
        """Test agent handles empty action list"""
        agent = self._create_agent(mock_ha_client, mock_skills_file, decision_dir)

        decision = {"reasoning": "No action needed", "actions": []}
        results = await agent.execute(decision)

        assert results is not None
        assert isinstance(results, list)
        assert len(results) == 0

    def test_skills_loading(self, mock_ha_client, mock_skills_file, decision_dir):
        """Test SKILLS.md file is loaded correctly"""
        agent = self._create_agent(mock_ha_client, mock_skills_file, decision_dir)

        assert agent.skills is not None
        assert "identity" in agent.skills
        assert "controllable_entities" in agent.skills
        assert len(agent.skills["controllable_entities"]) > 0
