import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from falkordb import FalkorDB


def investigate(lot_id):
    graph = FalkorDB(
        host="127.0.0.1", port=6379
    ).select_graph("lotguard_demo")

    tools = []

    tools.append("LOOKUP_INGREDIENT")
    lot_rows = graph.ro_query("""
        MATCH (l:IngredientLot {id:$lot})
        RETURN l.name, l.status
    """, params={"lot": lot_id}).result_set

    if not lot_rows:
        return {
            "lot_id": lot_id,
            "decision": "UNKNOWN_LOT",
            "requires_review": True,
            "tools": tools,
            "evidence_paths": [],
            "pending_quarantine": [],
        }

    name, status = lot_rows[0]

    tools.append("TRACE_DOWNSTREAM")
    rows = graph.ro_query("""
        MATCH (l:IngredientLot {id:$lot})
              -[:USED_IN]->(r:Run)
              -[:PRODUCED]->(b:Batch)
              -[:SENT_AS]->(s:Shipment)
        RETURN DISTINCT r.id, b.id, b.units,
                        s.id, s.status
        ORDER BY s.id, b.id
    """, params={"lot": lot_id}).result_set

    paths = [
        {
            "ingredient": lot_id,
            "run": r,
            "batch": b,
            "units": n,
            "shipment": s,
            "shipment_status": st,
            "proof": f"{lot_id} -> {r} -> {b} -> {s}",
        }
        for r, b, n, s, st in rows
    ]

    tools.append("INSPECT_INVENTORY")
    all_shipments = graph.ro_query("""
        MATCH (s:Shipment)
        RETURN s.id, s.status ORDER BY s.id
    """).result_set

    connected = {p["shipment"] for p in paths}

    # Lack of a known path is NOT proof of real-world safety.
    not_connected = [
        s for s, _ in all_shipments
        if s not in connected
    ]

    pending = []
    already = []
    review = []

    if status == "CONTAMINATED":
        for p in paths:
            if p["shipment_status"] == "IN_TRANSIT":
                pending.append(p["shipment"])
            elif p["shipment_status"] == "QUARANTINED":
                already.append(p["shipment"])
            else:
                review.append(p["shipment"])

    pending = sorted(set(pending))
    already = sorted(set(already))
    review = sorted(set(review))

    tools.append("SELECT_INTERVENTION")

    if status != "CONTAMINATED":
        decision = "NO_ACTION_FOR_THIS_LOT"
    elif not paths:
        decision = "ESCALATE_MISSING_LINEAGE"
    elif review:
        decision = "ESCALATE_AND_QUARANTINE_PENDING"
    elif pending:
        decision = "REQUEST_QUARANTINE_APPROVAL"
    else:
        decision = "NO_FURTHER_ACTION"

    return {
        "incident_type": "INGREDIENT_CONTAMINATION",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lot_id": lot_id,
        "ingredient": name,
        "lot_status": status,
        "decision": decision,
        "requires_review": bool(review or (
            status == "CONTAMINATED" and not paths
        )),
        "tools": tools,
        "evidence_paths": paths,
        "pending_quarantine": pending,
        "already_quarantined": already,
        "manual_review": review,
        "not_connected_in_known_graph": not_connected,
        "limitations": (
            "Only known graph relationships are evaluated. "
            "Missing provenance can conceal affected shipments. "
            "No inventory is changed by this planner."
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lot", default="ING-001")
    args = parser.parse_args()

    report = investigate(args.lot)

    print("\nLOTGUARD SENTINEL — EVIDENCE-BASED PLAN")
    print("=" * 48)

    for tool in report["tools"]:
        print("TOOL:", tool)

    print("\nDECISION:", report["decision"])

    print("\nEVIDENCE PATHS")
    for path in report.get("evidence_paths", []):
        print(" ", path["proof"])

    print("\nPENDING:", report["pending_quarantine"])
    print("ALREADY QUARANTINED:",
          report.get("already_quarantined", []))
    print("MANUAL REVIEW:",
          report.get("manual_review", []))
    print("NO KNOWN PATH:",
          report.get("not_connected_in_known_graph", []))

    Path("reports").mkdir(exist_ok=True)
    output = Path("reports") / f"incident_{args.lot}.json"
    output.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )

    print("\nEvidence bundle:", output)
    print("No inventory changes performed.")


if __name__ == "__main__":
    main()
