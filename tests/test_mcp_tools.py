"""Verify FalkorDB-backed MCP tools through a real MCP client."""

import asyncio
import json

from mcp import Client
from lotguard.mcp_server import mcp


def unpack(result):
    """Decode MCP structured content or JSON text content."""
    if result.is_error:
        details = [
            getattr(block, "text", repr(block))
            for block in result.content
        ]
        raise AssertionError(f"MCP tool error: {details}")

    if result.structured_content is not None:
        return result.structured_content

    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)

    raise AssertionError("MCP response has no usable content")


async def main():
    async with Client(mcp) as client:

        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}

        expected = {
            "inspect_incident",
            "trace_provenance",
            "inspect_provenance_gaps",
            "propose_response",
            "audit_history",
            "execute_approved_quarantine",
        }

        assert names == expected, names
        print("PASS 1: Six MCP tools discovered.")

        incident = unpack(
            await client.call_tool(
                "inspect_incident",
                {"lot_id": "ING-RISK"},
            )
        )

        assert incident["found"] is True
        assert incident["status"] == "CONTAMINATED"
        print("PASS 2: Ingredient incident retrieved from FalkorDB.")

        shallow = unpack(
            await client.call_tool(
                "trace_provenance",
                {"lot_id": "ING-RISK", "max_hops": 3},
            )
        )

        deep = unpack(
            await client.call_tool(
                "trace_provenance",
                {"lot_id": "ING-RISK", "max_hops": 8},
            )
        )

        assert shallow["shipments"] == ["SHIP-10"], shallow

        assert deep["shipments"] == [
            "SHIP-10",
            "SHIP-20",
            "SHIP-30",
        ], deep

        print("PASS 3: Shallow MCP traversal finds one shipment.")
        print("PASS 4: Deep MCP traversal finds three shipments.")

        gaps = unpack(
            await client.call_tool(
                "inspect_provenance_gaps",
                {},
            )
        )

        assert any(
            item["id"] == "SHIP-50"
            for item in gaps["unresolved_shipments"]
        ), gaps

        print("PASS 5: MCP detected incomplete provenance.")

        proposal = unpack(
            await client.call_tool(
                "propose_response",
                {"lot_id": "ING-RISK"},
            )
        )

        assert set(proposal["pending"]) == {
            "SHIP-10",
            "SHIP-20",
            "SHIP-30",
        }, proposal

        assert proposal["pending_units"] == 350
        assert "SHIP-50" in proposal["manual_review"]

        print("PASS 6: MCP generated correct containment proposal.")

        audit = unpack(
            await client.call_tool(
                "audit_history",
                {},
            )
        )

        assert isinstance(audit["actions"], dict)
        print("PASS 7: MCP retrieved graph audit history.")

        print("\nALL 7 MCP TOOL CHECKS PASSED.")
        print("No FalkorDB records modified.")


if __name__ == "__main__":
    asyncio.run(main())
