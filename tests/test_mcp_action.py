"""End-to-end MCP quarantine action safety test.

WARNING: Resets only the synthetic lotguard_depth_demo graph.
"""

import asyncio
import subprocess
import sys

from falkordb import FalkorDB
from mcp import Client

from lotguard.approval_gate import issue_local_approval
from lotguard.deep_sentinel import GRAPH_NAME
from lotguard.late_evidence import ingest_late_evidence
from lotguard.mcp_server import mcp
from lotguard.tool_agent import unpack


FIRST = ["SHIP-10", "SHIP-20", "SHIP-30"]


def reset():
    subprocess.run(
        [sys.executable, "-m", "lotguard.deep_lineage"],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def graph():
    return FalkorDB(
        host="127.0.0.1", port=6379
    ).select_graph(GRAPH_NAME)


def inventory():
    return dict(graph().ro_query("""
        MATCH (s:Shipment)
        RETURN s.id, s.status
    """).result_set)


def audits():
    rows = graph().ro_query("""
        MATCH (a:QuarantineAction)-[:TARGETS]->(s:Shipment)
        RETURN a.id, s.id
    """).result_set

    result = {}
    for aid, sid in rows:
        result.setdefault(aid, set()).add(sid)
    return result


async def reject(client, targets, ticket, label):
    before_inventory = inventory()
    before_audits = audits()

    try:
        result = await client.call_tool(
            "execute_approved_quarantine",
            {
                "lot_id": "ING-RISK",
                "shipments": targets,
                "approval_ticket": ticket,
            },
        )
    except Exception:
        pass
    else:
        assert result.is_error, (
            f"Unauthorized action accepted: {label}"
        )

    assert inventory() == before_inventory
    assert audits() == before_audits
    print(f"PASS: {label}")


async def approve(client, targets):
    ticket = issue_local_approval("ING-RISK", targets)

    result = await client.call_tool(
        "execute_approved_quarantine",
        {
            "lot_id": "ING-RISK",
            "shipments": targets,
            "approval_ticket": ticket,
        },
    )

    response = unpack(result)

    assert response["verified"] is True
    assert set(response["quarantined"]) == set(targets)
    assert response["action_id"] in audits()

    return ticket


async def main():
    reset()

    try:
        async with Client(mcp) as client:

            available = {
                t.name
                for t in (await client.list_tools()).tools
            }

            assert "execute_approved_quarantine" in available

            await reject(
                client,
                FIRST,
                "invalid-ticket",
                "Missing authorization rejected."
            )

            wrong = issue_local_approval(
                "ING-RISK", ["SHIP-10"]
            )

            await reject(
                client,
                FIRST,
                wrong,
                "Mismatched approval scope rejected."
            )

            await reject(
                client,
                ["SHIP-10"],
                "invalid-ticket",
                "Incomplete intervention scope rejected."
            )

            assert not audits()

            assert all(
                inventory()[sid] == "IN_TRANSIT"
                for sid in FIRST
            )

            first_ticket = await approve(client, FIRST)

            assert len(audits()) == 1

            assert all(
                inventory()[sid] == "QUARANTINED"
                for sid in FIRST
            )

            assert inventory()["SHIP-40"] == "IN_TRANSIT"

            print(
                "PASS: First 350-unit MCP quarantine verified."
            )

            await reject(
                client,
                FIRST,
                first_ticket,
                "Repeated action rejected."
            )

            ingest_late_evidence(apply=True)

            second_ticket = await approve(
                client, ["SHIP-50"]
            )

            assert second_ticket != first_ticket
            assert len(audits()) == 2
            assert inventory()["SHIP-50"] == "QUARANTINED"
            assert inventory()["SHIP-40"] == "IN_TRANSIT"

            print(
                "PASS: Second 60-unit MCP quarantine verified."
            )
            print(
                "PASS: Unrelated inventory preserved."
            )
            print(
                "PASS: Two distinct graph audits recorded."
            )

        print("\nALL MCP ACTION SAFETY CHECKS PASSED.")

    finally:
        reset()
        print("Synthetic demonstration graph reset.")


if __name__ == "__main__":
    asyncio.run(main())
