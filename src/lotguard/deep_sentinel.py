from falkordb import FalkorDB

GRAPH_NAME = "lotguard_depth_demo"


def investigate_deep(lot_id="ING-RISK"):
    graph = FalkorDB(
        host="127.0.0.1", port=6379
    ).select_graph(GRAPH_NAME)

    lot_rows = graph.ro_query("""
        MATCH (l:IngredientLot {id:$lot})
        RETURN l.status
    """, params={"lot": lot_id}).result_set

    if not lot_rows:
        raise ValueError(f"Unknown ingredient: {lot_id}")

    lot_status = lot_rows[0][0]

    # Discover paths of different lengths.
    rows = graph.ro_query("""
        MATCH p=(l:IngredientLot {id:$lot})
              -[:FLOWS_TO*1..8]->(s:Shipment)
        RETURN s.id, s.status, s.units,
               length(p), [n IN nodes(p) | n.id]
        ORDER BY s.id, length(p)
    """, params={"lot": lot_id}).result_set

    paths = [
        {
            "shipment": shipment,
            "status": status,
            "units": units,
            "depth": depth,
            "nodes": nodes,
        }
        for shipment, status, units, depth, nodes in rows
    ]

    # Distinct affected shipments, not distinct paths.
    affected = sorted({
        p["shipment"] for p in paths
    }) if lot_status == "CONTAMINATED" else []

    inventory_rows = graph.ro_query("""
        MATCH (s:Shipment)
        RETURN s.id, s.status, s.units
        ORDER BY s.id
    """).result_set

    inventory = {
        sid: {"status": status, "units": units}
        for sid, status, units in inventory_rows
    }

    # Detect missing upstream provenance explicitly.
    unknown_rows = graph.ro_query("""
        MATCH (b:Batch {provenance_complete:false})
              -[:FLOWS_TO*1..8]->(s:Shipment)
        RETURN DISTINCT s.id
        ORDER BY s.id
    """).result_set

    manual_review = sorted({
        row[0] for row in unknown_rows
    })

    pending = [
        sid for sid in affected
        if inventory[sid]["status"] == "IN_TRANSIT"
    ]

    already = [
        sid for sid in affected
        if inventory[sid]["status"] == "QUARANTINED"
    ]

    not_linked = sorted(
        set(inventory) - set(affected) - set(manual_review)
    )

    if pending and manual_review:
        decision = "QUARANTINE_AND_ESCALATE"
    elif pending:
        decision = "REQUEST_QUARANTINE_APPROVAL"
    elif manual_review:
        decision = "ESCALATE_PROVENANCE_GAP"
    else:
        decision = "NO_FURTHER_ACTION"

    return {
        "lot": lot_id,
        "lot_status": lot_status,
        "decision": decision,
        "evidence_paths": paths,
        "affected": affected,
        "pending": pending,
        "already_quarantined": already,
        "manual_review": manual_review,
        "not_linked_in_known_graph": not_linked,
        "pending_units": sum(
            inventory[s]["units"] for s in pending
        ),
    }


if __name__ == "__main__":
    report = investigate_deep()

    print("\nLOTGUARD SENTINEL — DEEP INVESTIGATION")
    print("=" * 48)
    print("Decision:", report["decision"])

    print("\nEVIDENCE PATHS")
    for p in report["evidence_paths"]:
        print(
            f"{p['depth']} hops: "
            + " -> ".join(p["nodes"])
        )

    print("\nAffected:", report["affected"])
    print("Pending:", report["pending"])
    print("Units requiring quarantine:", report["pending_units"])
    print("Manual review:", report["manual_review"])
    print("No known contamination path:",
          report["not_linked_in_known_graph"])

    assert report["affected"] == [
        "SHIP-10", "SHIP-20", "SHIP-30"
    ]
    assert report["pending_units"] == 350
    assert report["manual_review"] == ["SHIP-50"]
    assert report["not_linked_in_known_graph"] == ["SHIP-40"]

    print("\nPASS: Deep investigation and planning verified.")
    print("No inventory changes performed.")
