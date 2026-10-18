"""End-to-end test for LotGuard's evolving incident workflow.

Requires local FalkorDB on port 6379.
WARNING: Resets the synthetic lotguard_depth_demo graph.
"""

import subprocess
import sys
import threading
from contextlib import suppress
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from falkordb import FalkorDB
from lotguard.deep_sentinel import GRAPH_NAME, investigate_deep
from lotguard.sentinel_ui import Handler


FIRST = {"SHIP-10", "SHIP-20", "SHIP-30"}
ALL_AFFECTED = FIRST | {"SHIP-50"}


def reset_demo():
    subprocess.run(
        [sys.executable, "-m", "lotguard.deep_lineage"],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def statuses(graph):
    return dict(graph.ro_query("""
        MATCH (s:Shipment) RETURN s.id, s.status
    """).result_set)


def audit_actions(graph):
    rows = graph.ro_query("""
        MATCH (a:QuarantineAction)-[:TARGETS]->(s:Shipment)
        RETURN a.id, s.id
    """).result_set

    actions = {}
    for aid, shipment in rows:
        actions.setdefault(aid, set()).add(shipment)
    return actions


def post(base, route, values):
    data = urlencode(values).encode()
    request = Request(
        base + route,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST",
    )
    return urlopen(request, timeout=15)


def must_reject(base, route, values, expected_status):
    try:
        with post(base, route, values):
            pass
    except HTTPError as exc:
        assert exc.code == expected_status, (
            exc.code, expected_status
        )
    else:
        raise AssertionError(
            f"{route} should have returned {expected_status}"
        )


def main():
    print("Starting clean synthetic incident test.")
    reset_demo()

    graph = FalkorDB(
        host="127.0.0.1", port=6379
    ).select_graph(GRAPH_NAME)

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), Handler
    )
    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )
    thread.start()

    base = f"http://127.0.0.1:{server.server_port}"

    try:
        plan = investigate_deep()

        assert set(plan["affected"]) == FIRST
        assert set(plan["pending"]) == FIRST
        assert plan["pending_units"] == 350
        assert plan["manual_review"] == ["SHIP-50"]
        assert not audit_actions(graph)
        print("PASS 1: Initial investigation correct.")

        must_reject(
            base, "/approve",
            {"approved": "no"}, 403
        )
        must_reject(
            base, "/ingest-evidence",
            {"confirmed": "yes"}, 409
        )
        assert not audit_actions(graph)
        print("PASS 2: Unauthorized or premature actions rejected.")

        with post(base, "/approve", {"approved": "yes"}) as response:
            assert response.status == 200

        state = statuses(graph)
        assert all(state[s] == "QUARANTINED" for s in FIRST)
        assert state["SHIP-40"] == "IN_TRANSIT"
        assert state["SHIP-50"] == "IN_TRANSIT"

        first_audits = audit_actions(graph)
        assert len(first_audits) == 1
        assert list(first_audits.values()) == [FIRST]
        print("PASS 3: First quarantine and audit verified.")

        with urlopen(base, timeout=10) as response:
            page = response.read().decode()

        assert "Receive supplier evidence" in page
        print("PASS 4: Evidence action available after containment.")

        with post(
            base, "/ingest-evidence",
            {"confirmed": "yes"}
        ) as response:
            assert response.status == 200

        plan = investigate_deep()
        assert set(plan["affected"]) == ALL_AFFECTED
        assert plan["pending"] == ["SHIP-50"]
        assert plan["pending_units"] == 60
        assert not plan["manual_review"]
        assert len(audit_actions(graph)) == 1

        new_paths = [
            p for p in plan["evidence_paths"]
            if p["shipment"] == "SHIP-50"
        ]
        assert any(p["depth"] == 5 for p in new_paths)
        print("PASS 5: New evidence changes graph investigation.")

        must_reject(
            base, "/ingest-evidence",
            {"confirmed": "yes"}, 409
        )
        print("PASS 6: Repeated evidence submission rejected.")

        with post(base, "/approve", {"approved": "yes"}) as response:
            assert response.status == 200

        plan = investigate_deep()
        state = statuses(graph)

        assert set(plan["already_quarantined"]) == ALL_AFFECTED
        assert not plan["pending"]
        assert not plan["manual_review"]
        assert all(
            state[s] == "QUARANTINED"
            for s in ALL_AFFECTED
        )
        assert state["SHIP-40"] == "IN_TRANSIT"

        actions = audit_actions(graph)
        assert len(actions) == 2
        assert {
            frozenset(group)
            for group in actions.values()
        } == {
            frozenset(FIRST),
            frozenset({"SHIP-50"}),
        }
        print("PASS 7: Second quarantine and both audits verified.")

        with post(base, "/approve", {"approved": "yes"}) as response:
            assert response.status == 200

        assert audit_actions(graph) == actions
        print("PASS 8: Repeated quarantine creates no new audit.")

        with urlopen(base, timeout=10) as response:
            page = response.read().decode()

        assert "SUPPLIER EVIDENCE RECEIVED" in page
        assert "No pending quarantine actions." in page
        assert all(aid in page for aid in actions)
        print("PASS 9: Final dashboard reflects graph and audits.")

        print("\nALL 9 EVOLVING-INCIDENT CHECKS PASSED.")

    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        reset_demo()
        print("Synthetic incident reset for the demonstration.")


if __name__ == "__main__":
    main()
