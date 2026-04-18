"""
Live integration tests for ExternalMCPClient.

These tests connect to a real MCP server and verify tool discovery,
read-only tool invocation, resource reading, and cleanup.

All tests are skipped unless the MCP_SERVER_URL environment variable is set.
Optionally set MCP_SERVER_TOKEN for authenticated servers.

Usage:
    MCP_SERVER_URL=http://localhost:8080/mcp pytest tests/test_external_mcp_live.py -m integration
"""
import os

import pytest

from external_mcp import ExternalMCPClient

_MCP_URL = os.getenv("MCP_SERVER_URL", "")
_MCP_TOKEN = os.getenv("MCP_SERVER_TOKEN", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _MCP_URL, reason="MCP_SERVER_URL env var not set"),
]


@pytest.fixture
async def mcp_client():
    """Create, connect, and yield an ExternalMCPClient; clean up on teardown."""
    client = ExternalMCPClient(
        url=_MCP_URL,
        token=_MCP_TOKEN or None,
        name="integration-test",
        request_timeout=15.0,
    )
    connected = await client.connect()
    assert connected, f"Failed to connect to MCP server at {_MCP_URL}"
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_discover_tools(mcp_client: ExternalMCPClient):
    """Verify the MCP server exposes at least one tool."""
    assert len(mcp_client.tools) > 0, "Expected at least one tool from the MCP server"
    # Verify tool spec structure
    first_tool = next(iter(mcp_client.tools.values()))
    assert first_tool.name, "Tool must have a name"
    assert isinstance(first_tool.input_schema, dict), "Tool must have an input_schema dict"
    # Verify OpenAI-compatible schema generation works
    schema = first_tool.to_openai_schema()
    assert schema["type"] == "function"
    assert "function" in schema
    assert "name" in schema["function"]


@pytest.mark.asyncio
async def test_call_readonly_tool(mcp_client: ExternalMCPClient):
    """Call a read-only tool (list_entities, get_areas, or similar) and verify response."""
    # Try common read-only tool names in priority order
    readonly_candidates = [
        "list_entities",
        "get_areas",
        "get_entity",
        "list_areas",
        "get_states",
    ]
    tool_name = None
    for candidate in readonly_candidates:
        if candidate in mcp_client.tools:
            tool_name = candidate
            break

    if tool_name is None:
        # Fall back to calling the first available tool with empty args
        # (only if it looks safe / read-only based on name heuristics)
        for name in mcp_client.tools:
            if any(kw in name.lower() for kw in ("list", "get", "search", "read")):
                tool_name = name
                break

    if tool_name is None:
        pytest.skip("No recognisable read-only tool found on the MCP server")

    result = await mcp_client.call_tool(tool_name, {})
    assert result["ok"] is True, f"Tool call failed: {result}"
    # Should have some text or structured output
    assert result.get("text") or result.get("structured") is not None, (
        "Expected non-empty response from read-only tool"
    )


@pytest.mark.asyncio
async def test_read_resource(mcp_client: ExternalMCPClient):
    """Read a resource (e.g. hass://system) if any resources are advertised."""
    if not mcp_client.resources:
        pytest.skip("MCP server does not advertise any resources")

    # Pick the first resource
    resource = mcp_client.resources[0]
    result = await mcp_client.read_resource(resource.uri)
    assert result["ok"] is True, f"Resource read failed: {result}"
    assert isinstance(result.get("text", ""), str), "Expected text content from resource"


@pytest.mark.asyncio
async def test_disconnect_cleanup():
    """Verify connect then aclose leaves the client in a clean disconnected state."""
    client = ExternalMCPClient(
        url=_MCP_URL,
        token=_MCP_TOKEN or None,
        name="cleanup-test",
        request_timeout=10.0,
    )
    connected = await client.connect()
    assert connected, "Failed to connect for cleanup test"
    assert client.connected is True

    await client.aclose()
    assert client.connected is False

    # Calling tool after disconnect should return an error, not raise
    result = await client.call_tool("any_tool", {})
    assert result["ok"] is False
    assert "not_connected" in result.get("error", "")
