from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import yaml
import os

from agents.architect_agent import ArchitectAgent

router = APIRouter(prefix="/api/factory", tags=["factory"])

class GenerateRequest(BaseModel):
    prompt: str

class SaveRequest(BaseModel):
    config: Dict[str, Any]


def get_architect(request: Request):
    return request.app.state.architect

@router.get("/suggestions")
async def get_suggestions(request: Request):
    architect = request.app.state.architect
    if not architect:
        raise HTTPException(status_code=503, detail="Architect not initialized")
    return await architect.suggest_agents()

@router.post("/generate")
async def generate_config(req: GenerateRequest, request: Request):
    architect = request.app.state.architect
    if not architect:
        raise HTTPException(status_code=503, detail="Architect not initialized")

    config = await architect.generate_config(req.prompt)
    return config

def get_config_path():
    if os.path.exists("/config"):
        return "/config/agents.yaml"
    return "agents.yaml"

@router.post("/save")
async def save_agent(req: SaveRequest):
    """Appends the new agent config to agents.yaml."""
    config_path = get_config_path()
    new_agent = req.config

    try:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                data = yaml.safe_load(f) or {}
                if 'agents' not in data:
                    data['agents'] = []
        else:
            data = {'agents': []}

        for a in data['agents']:
            if a['id'] == new_agent['id']:
                raise HTTPException(status_code=400, detail=f"Agent ID {new_agent['id']} already exists")

        data['agents'].append(new_agent)

        with open(config_path, 'w') as f:
            yaml.dump(data, f, sort_keys=False)

        return {"status": "success", "message": "Agent saved. Restart required to activate."}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, request: Request):
    """Delete an agent from configuration and memory"""
    config_path = get_config_path()

    try:
        if hasattr(request.app.state, "agents"):
            request.app.state.agents.pop(agent_id, None)

        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                data = yaml.safe_load(f) or {}

            if 'agents' in data:
                data['agents'] = [a for a in data['agents'] if a['id'] != agent_id]
                with open(config_path, 'w') as f:
                    yaml.dump(data, f, sort_keys=False)

        return {"status": "success", "message": f"Agent {agent_id} deleted"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {str(e)}")

class UpdateAgentRequest(BaseModel):
    instruction: Optional[str] = None
    name: Optional[str] = None
    entities: Optional[List[str]] = None
    decision_interval: Optional[int] = None

@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, req: UpdateAgentRequest, request: Request):
    """Update an agent's configuration with proper hot reload."""
    config_path = get_config_path()

    try:
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail="Config file not found")

        with open(config_path, 'r') as f:
            data = yaml.safe_load(f) or {}

        discovered_entities: Optional[List[str]] = None
        found = False
        for agent in data.get('agents', []):
            if agent['id'] == agent_id:
                if req.instruction is not None:
                    agent['instruction'] = req.instruction
                    if req.entities is None:
                        try:
                            architect = request.app.state.architect
                            if architect:
                                discovered_entities = await architect.discover_entities_from_instruction(req.instruction)
                                agent['entities'] = discovered_entities
                        except Exception:
                            pass
                if req.name is not None:
                    agent['name'] = req.name
                if req.entities is not None:
                    agent['entities'] = req.entities
                if req.decision_interval is not None:
                    agent['decision_interval'] = req.decision_interval
                found = True
                break

        if not found:
            raise HTTPException(status_code=404, detail="Agent not found in config")

        with open(config_path, 'w') as f:
            yaml.dump(data, f, sort_keys=False)

        # Hot reload — sync the live agent instance in memory
        agents_dict = getattr(request.app.state, "agents", {})
        agent_instance = agents_dict.get(agent_id)
        if agent_instance is not None:
            if req.instruction is not None:
                agent_instance.instruction = req.instruction
            if req.name is not None:
                agent_instance.name = req.name
            if req.decision_interval is not None and hasattr(agent_instance, "decision_interval"):
                agent_instance.decision_interval = req.decision_interval
            if req.entities is not None:
                agent_instance.entities = req.entities
            elif discovered_entities is not None:
                agent_instance.entities = discovered_entities

        return {"status": "success", "message": "Agent updated."}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
