import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from falkordb import FalkorDB

BASE = "http://127.0.0.1:8765"
g = FalkorDB(host="127.0.0.1", port=6379).select_graph("lotguard_demo")


def reset():
    subprocess.run(
        [sys.executable, "src/lotguard/seed.py"],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def statuses():
    rows = g.ro_query(
        "MATCH (s:Shipment) RETURN s.id, s.status"
    ).result_set
    return dict(rows)


def audits():
    return g.ro_query("""
        MATCH (a:QuarantineAction)-[:TARGETS]->(s:Shipment)
        RETURN a.id, s.id
    """).result_set


def post(lot, approval="yes"):
    data = urlencode({"lot": lot, "approve": approval}).encode()
    with urlopen(Request(BASE + "/quarantine", data=data), timeout=10) as response:
        return response.status


with urlopen(BASE, timeout=5) as response:
    assert response.status == 200

reset()

try:
    expected_initial = {
        "SHIP-101": "IN_TRANSIT",
        "SHIP-102": "IN_TRANSIT",
        "SHIP-103": "IN_TRANSIT",
    }
    assert statuses() == expected_initial
    print("PASS 1: Reproducible initial inventory")

    try:
        post("ING-001", "no")
        raise AssertionError("Unapproved action was accepted")
    except HTTPError as error:
        assert error.code == 403
    assert statuses() == expected_initial
    print("PASS 2: Unapproved quarantine rejected")

    assert post("ING-002") == 200
    assert statuses() == expected_initial
    print("PASS 3: Clear ingredient cannot trigger quarantine")

    assert post("ING-001") == 200
    assert statuses() == {
        "SHIP-101": "QUARANTINED",
        "SHIP-102": "QUARANTINED",
        "SHIP-103": "IN_TRANSIT",
    }
    print("PASS 4: Correct quarantine; safe inventory preserved")

    records = audits()
    assert len(records) == 2
    assert len({row[0] for row in records}) == 1
    assert {row[1] for row in records} == {"SHIP-101", "SHIP-102"}
    print("PASS 5: Graph audit records correct")

    assert post("ING-001") == 200
    assert audits() == records
    print("PASS 6: Repeated action creates no new audit records")

    print("\nALL 6 END-TO-END CHECKS PASSED")

finally:
    reset()
    print("Synthetic demo reset for video recording.")
