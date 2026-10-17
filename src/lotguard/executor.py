import argparse
from datetime import datetime, timezone
from uuid import uuid4

from falkordb import FalkorDB
from lotguard.sentinel import investigate


def execute(lot_id: str, approved: bool = False):
    plan = investigate(lot_id)
    decision = plan["decision"]

    print("\nLOTGUARD SENTINEL — ACTION EXECUTOR")
    print("=" * 48)
    print("Incident:", lot_id)
    print("Decision:", decision)

    pending = set(plan["pending_quarantine"])

    if not pending:
        print("No pending quarantine actions.")
        print("Inventory unchanged.")
        return

    print("Proposed shipments:", sorted(pending))

    for path in plan["evidence_paths"]:
        if path["shipment"] in pending:
            print("EVIDENCE:", path["proof"])

    if not approved:
        print("\nAPPROVAL REQUIRED")
        print("Run with --approve to execute.")
        print("No inventory changes performed.")
        return

    graph = FalkorDB(
        host="127.0.0.1", port=6379
    ).select_graph("lotguard_demo")

    def inventory():
        rows = graph.ro_query("""
            MATCH (s:Shipment)
            RETURN s.id, s.status
        """).result_set
        return dict(rows)

    before = inventory()

    if any(before.get(s) != "IN_TRANSIT" for s in pending):
        raise RuntimeError("Inventory changed since planning. Reinvestigate.")

    action_id = "SENTINEL-" + uuid4().hex[:12].upper()

    changed = graph.query("""
        MATCH (l:IngredientLot {id:$lot, status:'CONTAMINATED'})
              -[:USED_IN]->(:Run)
              -[:PRODUCED]->(:Batch)
              -[:SENT_AS]->(s:Shipment {status:'IN_TRANSIT'})
        WHERE s.id IN $shipments
        WITH DISTINCT l, s
        MERGE (a:QuarantineAction {id:$action})
        SET a.reason_lot = $lot,
            a.created_at = $timestamp
        MERGE (a)-[:TRACED_FROM]->(l)
        MERGE (a)-[:TARGETS]->(s)
        SET s.status = 'QUARANTINED'
        RETURN s.id
    """, params={
        "lot": lot_id,
        "shipments": sorted(pending),
        "action": action_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    actual = {row[0] for row in changed.result_set}
    after = inventory()

    audit = graph.ro_query("""
        MATCH (a:QuarantineAction {id:$action})
              -[:TARGETS]->(s:Shipment)
        RETURN s.id
    """, params={"action": action_id})

    audited = {row[0] for row in audit.result_set}

    verified = (
        actual == pending == audited
        and all(after[s] == "QUARANTINED" for s in pending)
        and all(
            after[s] == status
            for s, status in before.items()
            if s not in pending
        )
    )

    print("\nEXECUTION RESULTS")
    print("Action:", action_id)
    print("Changed:", sorted(actual))
    print("Graph audit:", sorted(audited))
    print("Verification:", "PASS" if verified else "FAIL")

    if not verified:
        raise RuntimeError("Intervention verification failed.")

    print("PASS: Only planned shipments were quarantined.")
    print("PASS: Unaffected inventory states were preserved.")
    print("PASS: FalkorDB audit relationships verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lot", default="ING-001")
    parser.add_argument("--approve", action="store_true")
    args = parser.parse_args()

    execute(args.lot, args.approve)
