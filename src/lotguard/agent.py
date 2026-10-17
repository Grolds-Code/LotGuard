import argparse
from falkordb import FalkorDB


def investigate(lot_id: str) -> None:
    db = FalkorDB(host="127.0.0.1", port=6379)
    graph = db.select_graph("lotguard_demo")

    result = graph.ro_query("""
    MATCH (lot:IngredientLot {id:$lot_id})
    OPTIONAL MATCH (lot)-[:USED_IN]->(run:Run)
                       -[:PRODUCED]->(batch:Batch)
                       -[:SENT_AS]->(shipment:Shipment)
    RETURN lot.name, lot.status,
           run.id, batch.id, batch.units,
           shipment.id, shipment.status
    ORDER BY batch.id
    """, params={"lot_id": lot_id})

    rows = result.result_set

    if not rows:
        print(f"ERROR: Ingredient lot {lot_id} not found.")
        return

    name, status = rows[0][:2]

    print("\nLOTGUARD — INVESTIGATION AGENT")
    print("=" * 42)
    print(f"Incident lot: {lot_id}")
    print(f"Ingredient:   {name}")
    print(f"Lot status:   {status}")

    if status != "CONTAMINATED":
        print("\nDECISION: NO RECALL REQUIRED")
        print("Reason: This ingredient lot has no contamination flag.")
        print("No inventory changes performed.")
        return

    print("\nEVIDENCE PATHS")
    print("-" * 42)

    affected = []
    for _, _, run_id, batch_id, units, shipment_id, ship_status in rows:
        if shipment_id is None:
            continue

        print(
            f"{lot_id} -> {run_id} -> {batch_id}"
            f" -> {shipment_id} [{ship_status}]"
        )
        affected.append(
            (batch_id, shipment_id, units, ship_status)
        )

    pending = [
        item for item in affected
        if item[3] == "IN_TRANSIT"
    ]

    already_quarantined = [
        item for item in affected
        if item[3] == "QUARANTINED"
    ]

    other = [
        item for item in affected
        if item[3] not in ("IN_TRANSIT", "QUARANTINED")
    ]

    print("\nAGENT DECISION")
    print("-" * 42)

    if pending:
        print("ACTION: QUARANTINE REQUIRED")
        for batch_id, shipment_id, units, _ in pending:
            print(f"  Quarantine {shipment_id}: {units} units")

    if already_quarantined:
        print(
            f"VERIFIED: {len(already_quarantined)}"
            " shipments already quarantined."
        )

    if other:
        print("ESCALATION: Manual review required.")
        for _, shipment_id, _, ship_status in other:
            print(f"  {shipment_id}: {ship_status}")

    if not pending and not other:
        print("DECISION: NO FURTHER INTERVENTION REQUIRED")

    print("\nNo inventory changes performed.")
    print("This is a read-only investigation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lot", required=True)
    args = parser.parse_args()
    investigate(args.lot)
