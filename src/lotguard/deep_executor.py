import argparse
import json
from datetime import datetime, timezone
from uuid import uuid4

from falkordb import FalkorDB
from lotguard.deep_sentinel import investigate_deep, GRAPH_NAME


def inventory(graph):
    rows = graph.ro_query("""
        MATCH (s:Shipment)
        RETURN s.id, s.status
    """).result_set
    return dict(rows)


def execute_deep(lot_id="ING-RISK", approved=False):
    graph = FalkorDB(
        host="127.0.0.1", port=6379
    ).select_graph(GRAPH_NAME)

    plan = investigate_deep(lot_id)
    pending = set(plan["pending"])

    print("\nLOTGUARD SENTINEL — DEEP EXECUTOR")
    print("=" * 48)
    print("Incident:", lot_id)
    print("Decision:", plan["decision"])

    print("\nEVIDENCE PATHS")
    for path in plan["evidence_paths"]:
        print(
            f"{path['depth']} hops: "
            + " -> ".join(path["nodes"])
        )

    print("\nProposed quarantine:", sorted(pending))
    print("Manual provenance review:", plan["manual_review"])

    if not pending:
        print("\nNo pending quarantine.")
        print("Inventory unchanged.")
        return

    if not approved:
        print("\nAPPROVAL REQUIRED")
        print("Run with --approve to execute.")
        print("No inventory changes performed.")
        return

    before = inventory(graph)

    # Reinvestigate before changing synthetic inventory.
    fresh_plan = investigate_deep(lot_id)

    if set(fresh_plan["pending"]) != pending:
        raise RuntimeError(
            "Investigation changed. Approval must be reconsidered."
        )

    if any(before.get(s) != "IN_TRANSIT" for s in pending):
        raise RuntimeError(
            "Shipment states changed. Reinvestigation required."
        )

    action_id = "DEEP-" + uuid4().hex[:12].upper()

    evidence = [
        path for path in fresh_plan["evidence_paths"]
        if path["shipment"] in pending
    ]

    result = graph.query("""
        MATCH (l:IngredientLot {
            id:$lot, status:'CONTAMINATED'
        })
        MATCH p=(l)-[:FLOWS_TO*1..8]->
              (s:Shipment {status:'IN_TRANSIT'})
        WHERE s.id IN $targets
        WITH DISTINCT l, s

        MERGE (a:QuarantineAction {id:$action})
        SET a.reason_lot = $lot,
            a.created_at = $timestamp,
            a.evidence_json = $evidence

        MERGE (a)-[:TRACED_FROM]->(l)
        MERGE (a)-[:TARGETS]->(s)

        SET s.status = 'QUARANTINED'

        RETURN DISTINCT s.id
    """, params={
        "lot": lot_id,
        "targets": sorted(pending),
        "action": action_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "evidence": json.dumps(evidence),
    })

    changed = {row[0] for row in result.result_set}
    after = inventory(graph)

    audit = graph.ro_query("""
        MATCH (a:QuarantineAction {id:$action})
              -[:TRACED_FROM]->
              (l:IngredientLot {id:$lot})
        MATCH (a)-[:TARGETS]->(s:Shipment)
        RETURN DISTINCT s.id, a.evidence_json
    """, params={
        "action": action_id,
        "lot": lot_id,
    }).result_set

    audited = {row[0] for row in audit}

    verified = (
        changed == pending == audited
        and all(after[s] == "QUARANTINED" for s in pending)
        and all(
            after[s] == state
            for s, state in before.items()
            if s not in pending
        )
        and all(
            json.loads(saved_evidence) == evidence
            for _, saved_evidence in audit
        )
    )

    print("\nEXECUTION VERIFICATION")
    print("-" * 48)
    print("Action ID:", action_id)
    print("Quarantined:", sorted(changed))
    print("Graph audit:", sorted(audited))
    print("SHIP-40:", after["SHIP-40"])
    print("SHIP-50:", after["SHIP-50"])
    print("Verification:", "PASS" if verified else "FAIL")

    if not verified:
        raise RuntimeError(
            "Verification failed. Inspect graph state before retrying."
        )

    print("\nPASS: All planned shipments quarantined.")
    print("PASS: Unrelated shipment states unchanged.")
    print("PASS: Evidence stored in FalkorDB audit.")
    print("REVIEW: SHIP-50 still requires provenance investigation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lot", default="ING-RISK")
    parser.add_argument("--approve", action="store_true")
    args = parser.parse_args()

    execute_deep(args.lot, args.approve)
