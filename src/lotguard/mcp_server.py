"""Read-only FalkorDB MCP tools for LotGuard Sentinel."""

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


if __name__ == "__main__":
    mcp.run(transport="stdio")
