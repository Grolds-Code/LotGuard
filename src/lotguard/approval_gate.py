"""Process-local, one-time approval tickets for synthetic interventions.

This is a demonstration safeguard, not production authentication.
Only trusted application code should issue tickets.
"""

import secrets
import threading
import time

_APPROVALS = {}
_LOCK = threading.Lock()
TTL_SECONDS = 90


def issue_local_approval(lot_id, shipments):
    """Issue a short-lived ticket for an exact intervention scope."""
    targets = tuple(sorted(set(shipments)))
    if not lot_id or not targets:
        raise ValueError("Incident and targets are required.")

    ticket = secrets.token_urlsafe(32)
    with _LOCK:
        _APPROVALS[ticket] = (
            lot_id,
            targets,
            time.monotonic() + TTL_SECONDS,
        )
    return ticket


def consume_local_approval(ticket, lot_id, shipments):
    """Consume a ticket once, rejecting mismatches and expiry."""
    targets = tuple(sorted(set(shipments)))

    with _LOCK:
        approval = _APPROVALS.pop(ticket, None)

    if (
        approval is None
        or approval[0] != lot_id
        or approval[1] != targets
        or time.monotonic() >= approval[2]
    ):
        raise PermissionError(
            "Missing, expired, reused, or mismatched approval."
        )
    return True


if __name__ == "__main__":
    ticket = issue_local_approval(
        "ING-RISK", ["SHIP-10", "SHIP-20"]
    )

    try:
        consume_local_approval(
            ticket, "ING-RISK", ["SHIP-10"]
        )
    except PermissionError:
        print("PASS: Mismatched scope rejected.")
    else:
        raise AssertionError("Scope mismatch accepted.")

    ticket = issue_local_approval(
        "ING-RISK", ["SHIP-10", "SHIP-20"]
    )
    assert consume_local_approval(
        ticket, "ING-RISK", ["SHIP-10", "SHIP-20"]
    )
    print("PASS: Correct one-time approval accepted.")

    try:
        consume_local_approval(
            ticket, "ING-RISK", ["SHIP-10", "SHIP-20"]
        )
    except PermissionError:
        print("PASS: Reused approval rejected.")
    else:
        raise AssertionError("Approval reuse accepted.")

    print("ALL APPROVAL GATE CHECKS PASSED.")
    print("No graph records modified.")
