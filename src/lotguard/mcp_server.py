"""FalkorDB MCP investigation and approval-gated action tools."""

from falkordb import FalkorDB
from mcp.server import MCPServer

from lotguard.deep_sentinel import GRAPH_NAME, investigate_deep


mcp = MCPServer("LotGuard Sentinel")


def graph_connection():
    return FalkorDB(
        host="127.0.0.1",
        port=6379,
    ).select_graph(GRAPH_NAME)


@mcp.tool()
def inspect_incident(lot_id: str = "ING-RISK") -> dict:
    """Inspect an ingredient incident and its contamination status."""
    graph = graph_connection()
    rows = graph.ro_query(
        """
        MATCH (l:IngredientLot {id:$lot})
        RETURN l.status
        """,
        params={"lot": lot_id},
    ).result_set

    if not rows:
        return {"found": False, "lot_id": lot_id}

    return {
        "found": True,
        "lot_id": lot_id,
        "status": rows[0][0],
    }


@mcp.tool()
def trace_provenance(
    lot_id: str = "ING-RISK",
    max_hops: int = 3,
) -> dict:
    """Trace directed supply-chain paths using FalkorDB.

    Choose 3 hops for a shallow investigation or 8 for deep tracing.
    """
    if max_hops not in (3, 8):
        raise ValueError("max_hops must be 3 or 8")

    graph = graph_connection()

    # Only the validated integer above is interpolated.
    rows = graph.ro_query(
        f"""
        MATCH p=(l:IngredientLot {{id:$lot}})
              -[:FLOWS_TO*1..{max_hops}]->(s:Shipment)
        RETURN s.id, s.units, length(p),
               [n IN nodes(p) | n.id]
        ORDER BY s.id, length(p)
        """,
        params={"lot": lot_id},
    ).result_set

    return {
        "lot_id": lot_id,
        "max_hops": max_hops,
        "shipments": sorted({r[0] for r in rows}),
        "paths": [
            {
                "shipment": sid,
                "units": units,
                "depth": depth,
                "nodes": nodes,
            }
            for sid, units, depth, nodes in rows
        ],
    }


@mcp.tool()
def inspect_provenance_gaps() -> dict:
    """Find shipments with incomplete upstream provenance."""
    graph = graph_connection()

    rows = graph.ro_query(
        """
        MATCH (b:Batch {provenance_complete:false})
              -[:FLOWS_TO*1..8]->(s:Shipment)
        RETURN DISTINCT s.id, s.units
        ORDER BY s.id
        """
    ).result_set

    return {
        "unresolved_shipments": [
            {"id": sid, "units": units}
            for sid, units in rows
        ]
    }


@mcp.tool()
def propose_response(lot_id: str = "ING-RISK") -> dict:
    """Compute a read-only containment proposal from graph evidence.

    This tool cannot authorize or execute quarantine.
    """
    plan = investigate_deep(lot_id)

    return {
        "decision": plan["decision"],
        "pending": plan["pending"],
        "pending_units": plan["pending_units"],
        "manual_review": plan["manual_review"],
        "already_quarantined": plan["already_quarantined"],
        "evidence_paths": plan["evidence_paths"],
    }


@mcp.tool()
def audit_history() -> dict:
    """Read previously recorded quarantine actions from FalkorDB."""
    graph = graph_connection()

    rows = graph.ro_query(
        """
        MATCH (a:QuarantineAction)-[:TARGETS]->(s:Shipment)
        RETURN a.id, s.id
        ORDER BY a.id, s.id
        """
    ).result_set

    actions = {}
    for action_id, shipment_id in rows:
        actions.setdefault(action_id, []).append(shipment_id)

    return {"actions": actions}



@mcp.tool()
def execute_approved_quarantine(
    lot_id: str,
    shipments: list[str],
    approval_ticket: str,
) -> dict:
    """Execute a scoped, explicitly approved synthetic quarantine.

    A trusted local operator interface must issue the one-time
    approval ticket. This tool cannot create its own approval.
    The executor rechecks FalkorDB before making changes.
    """
    from lotguard.approval_gate import consume_local_approval
    from lotguard.deep_executor import execute_deep

    if not shipments or any(
        not isinstance(item, str) or not item
        for item in shipments
    ):
        raise ValueError("Valid shipment identifiers required.")

    targets = sorted(set(shipments))
    if len(targets) != len(shipments):
        raise ValueError("Duplicate shipment targets rejected.")

    current = investigate_deep(lot_id)
    if sorted(current["pending"]) != targets:
        raise PermissionError(
            "Requested targets differ from current graph evidence."
        )

    consume_local_approval(approval_ticket, lot_id, targets)

    previous = audit_history()["actions"]
    execute_deep(lot_id=lot_id, approved=True)
    updated = audit_history()["actions"]

    new_ids = set(updated) - set(previous)
    if len(new_ids) != 1:
        raise RuntimeError(
            "Expected one newly verified quarantine audit."
        )

    action_id = next(iter(new_ids))
    if set(updated[action_id]) != set(targets):
        raise RuntimeError(
            "Graph audit differs from approved targets."
        )

    return {
        "verified": True,
        "action_id": action_id,
        "lot_id": lot_id,
        "quarantined": targets,
        "new_action_count": len(new_ids),
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")
