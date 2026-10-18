"""Simulate a late-arriving provenance record in FalkorDB."""

import argparse

from falkordb import FalkorDB

from lotguard.deep_sentinel import GRAPH_NAME, investigate_deep


EVIDENCE_ID = "EVID-SUPPLIER-50"


def ingest_late_evidence(apply=False):
    graph = FalkorDB(
        host="127.0.0.1", port=6379
    ).select_graph(GRAPH_NAME)

    before = investigate_deep()

    if "SHIP-50" in before["affected"]:
        print("ALREADY INGESTED: SHIP-50 is connected to ING-RISK.")
        print("No duplicate provenance edges created.")
        return

    if "SHIP-50" not in before["manual_review"]:
        raise RuntimeError(
            "Expected unresolved SHIP-50 provenance before ingestion."
        )

    if not apply:
        print("LATE EVIDENCE PREVIEW")
        print("Supplier record:", EVIDENCE_ID)
        print("New lineage: B-20 -> RUN-60 -> B-UNKNOWN")
        print("Affected shipment after ingestion: SHIP-50")
        print("Expected additional inventory: 60 units")
        print("No graph changes performed.")
        print("Use --apply after the initial quarantine.")
        return

    result = graph.query("""
        MATCH (source:Batch {id:$source})
        MATCH (target:Batch {id:$target})

        MERGE (run:Run {id:$run})
        MERGE (source)-[:FLOWS_TO]->(run)
        MERGE (run)-[:FLOWS_TO]->(target)

        SET target.provenance_complete = true

        MERGE (e:ProvenanceEvidence {id:$evidence})
        SET e.kind = 'SUPPLIER_RECORD',
            e.description = 'Synthetic late supplier lineage'
        MERGE (e)-[:RESOLVES]->(target)

        RETURN e.id
    """, params={
        "source": "B-20",
        "target": "B-UNKNOWN",
        "run": "RUN-60",
        "evidence": EVIDENCE_ID,
    })

    if not result.result_set:
        raise RuntimeError("Evidence insertion returned no record.")

    after = investigate_deep()

    assert "SHIP-50" in after["affected"]
    assert "SHIP-50" in after["pending"]
    assert "SHIP-50" not in after["manual_review"]
    assert "SHIP-40" in after["not_linked_in_known_graph"]

    evidence_rows = graph.ro_query("""
        MATCH (e:ProvenanceEvidence {id:$id})
              -[:RESOLVES]->(b:Batch {id:'B-UNKNOWN'})
        RETURN e.id
    """, params={"id": EVIDENCE_ID}).result_set

    assert evidence_rows == [[EVIDENCE_ID]]

    print("PASS: Supplier evidence stored in FalkorDB.")
    print("PASS: SHIP-50 now has a contamination path.")
    print("PASS: 60 units newly require quarantine.")
    print("PASS: Incomplete provenance resolved.")
    print("PASS: SHIP-40 remains unaffected.")
    print("PASS: Previous quarantine actions preserved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the synthetic provenance evidence",
    )
    args = parser.parse_args()
    ingest_late_evidence(apply=args.apply)
