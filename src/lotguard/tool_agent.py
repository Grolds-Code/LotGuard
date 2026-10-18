"""Bounded, evidence-driven MCP investigation agent."""

import asyncio
import json

from mcp import Client
from lotguard.mcp_server import mcp


def unpack(result):
    if result.is_error:
        details = [
            getattr(block, "text", repr(block))
            for block in result.content
        ]
        raise RuntimeError(f"MCP error: {details}")

    if result.structured_content is not None:
        return result.structured_content

    for block in result.content:
        value = getattr(block, "text", None)
        if value is not None:
            return json.loads(value)

    raise RuntimeError("Empty MCP response")


async def investigate(lot_id="ING-RISK"):
    trace = []

    async with Client(mcp) as client:
        available = {
            tool.name
            for tool in (await client.list_tools()).tools
        }

        async def use_tool(name, arguments, reason):
            if name not in available:
                raise RuntimeError(
                    f"MCP tool unavailable: {name}"
                )

            result = unpack(
                await client.call_tool(name, arguments)
            )

            trace.append({
                "tool": name,
                "reason": reason,
            })

            print(f"\n[MCP] {name}")
            print("Reason:", reason)

            return result

        incident = await use_tool(
            "inspect_incident",
            {"lot_id": lot_id},
            "Establish whether this ingredient has an active incident.",
        )

        if (
            not incident.get("found")
            or incident.get("status") != "CONTAMINATED"
        ):
            print("\nNo confirmed contaminated ingredient.")
            print("Investigation stopped without intervention.")

            return {
                "decision": "NO_CONFIRMED_INCIDENT",
                "trace": trace,
                "pending_units": 0,
            }

        shallow = await use_tool(
            "trace_provenance",
            {"lot_id": lot_id, "max_hops": 3},
            "Establish the initial directly connected risk scope.",
        )

        deep = await use_tool(
            "trace_provenance",
            {"lot_id": lot_id, "max_hops": 8},
            "Expand beyond shallow paths to avoid missing "
            "indirectly connected shipments.",
        )

        shallow_found = set(shallow["shipments"])
        deep_found = set(deep["shipments"])

        newly_discovered = sorted(
            deep_found - shallow_found
        )

        print("\nShallow investigation:", sorted(shallow_found))
        print("Deep investigation:", sorted(deep_found))
        print("Missed by shallow tracing:", newly_discovered)

        proposal = await use_tool(
            "propose_response",
            {"lot_id": lot_id},
            "Generate a read-only intervention proposal "
            "using current graph evidence and inventory states.",
        )

        unresolved = []

        if proposal["manual_review"]:
            gaps = await use_tool(
                "inspect_provenance_gaps",
                {},
                "The graph-driven proposal identified unresolved "
                "provenance requiring additional investigation.",
            )

            unresolved = gaps["unresolved_shipments"]

        history = {}

        if proposal["already_quarantined"]:
            audit = await use_tool(
                "audit_history",
                {},
                "Previous interventions exist; inspect graph audit "
                "records before recommending further action.",
            )

            history = audit["actions"]

        if proposal["pending"]:
            decision = "REQUEST_HUMAN_QUARANTINE_APPROVAL"
        elif proposal["manual_review"]:
            decision = "ESCALATE_PROVENANCE_GAP"
        else:
            decision = "NO_FURTHER_ACTION"

        print("\n" + "=" * 52)
        print("SENTINEL MCP AGENT — FINAL ASSESSMENT")
        print("=" * 52)
        print("Decision:", decision)
        print("Pending shipments:", proposal["pending"])
        print("Pending units:", proposal["pending_units"])
        print("Provenance reviews:", unresolved)
        print("Previous actions:", len(history))
        print("MCP tools invoked:", len(trace))

        print("\nEXPLAINABLE TOOL SELECTION TRACE")
        for index, step in enumerate(trace, 1):
            print(f"{index}. {step['tool']}")
            print(f"   Why: {step['reason']}")

        print("\nNo graph mutations performed.")

        return {
            "decision": decision,
            "trace": trace,
            "pending_units": proposal["pending_units"],
            "newly_discovered": newly_discovered,
            "proposal": proposal,
        }


async def main():
    print("TEST A: ACTIVE CONTAMINATION INCIDENT")
    active = await investigate("ING-RISK")

    selected = [
        entry["tool"]
        for entry in active["trace"]
    ]

    assert selected == [
        "inspect_incident",
        "trace_provenance",
        "trace_provenance",
        "propose_response",
        "inspect_provenance_gaps",
    ], selected

    assert active["pending_units"] == 350
    assert active["newly_discovered"] == [
        "SHIP-20", "SHIP-30"
    ]

    print("\nPASS: Active incident selected five MCP calls.")
    print("PASS: Deep tracing recovered two additional shipments.")

    print("\nTEST B: UNKNOWN INGREDIENT")
    unknown = await investigate("ING-NONEXISTENT")

    unknown_tools = [
        entry["tool"]
        for entry in unknown["trace"]
    ]

    assert unknown_tools == ["inspect_incident"]
    assert unknown["decision"] == "NO_CONFIRMED_INCIDENT"

    print("\nPASS: Unknown incident stopped after one MCP call.")
    print("PASS: Agent selected different tool sequences.")
    print("PASS: No database mutations performed.")


if __name__ == "__main__":
    asyncio.run(main())
