"""Verify MCP tool selection across evolving incident states."""

import asyncio
import subprocess
import sys

from lotguard.deep_executor import execute_deep
from lotguard.late_evidence import ingest_late_evidence
from lotguard.tool_agent import investigate


def reset_demo():
    subprocess.run(
        [sys.executable, "-m", "lotguard.deep_lineage"],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def selected_tools(report):
    return [step["tool"] for step in report["trace"]]


async def main():
    reset_demo()

    try:
        print("\n=== STATE 1: INITIAL INCIDENT ===")
        initial = await investigate("ING-RISK")

        assert initial["pending_units"] == 350
        assert "inspect_provenance_gaps" in selected_tools(initial)
        assert "audit_history" not in selected_tools(initial)
        print("PASS: Initial response selected appropriate tools.")

        execute_deep("ING-RISK", approved=True)

        print("\n=== STATE 2: FIRST QUARANTINE COMPLETE ===")
        contained = await investigate("ING-RISK")

        assert contained["pending_units"] == 0
        assert contained["decision"] == "ESCALATE_PROVENANCE_GAP"
        assert "inspect_provenance_gaps" in selected_tools(contained)
        assert "audit_history" in selected_tools(contained)
        print("PASS: Agent inspected earlier actions and unresolved provenance.")

        ingest_late_evidence(apply=True)

        print("\n=== STATE 3: NEW SUPPLIER EVIDENCE ===")
        updated = await investigate("ING-RISK")

        assert updated["pending_units"] == 60
        assert updated["decision"] == "REQUEST_HUMAN_QUARANTINE_APPROVAL"
        assert "SHIP-50" in updated["newly_discovered"]

        tools = selected_tools(updated)
        assert "audit_history" in tools
        assert "inspect_provenance_gaps" not in tools

        print("PASS: Agent revised its proposal to 60 units.")
        print("PASS: Previously resolved provenance tool was skipped.")
        print("PASS: Earlier graph audit was inspected.")

        print("\nALL EVOLVING MCP AGENT CHECKS PASSED.")

    finally:
        reset_demo()
        print("Synthetic demo reset to its initial state.")


if __name__ == "__main__":
    asyncio.run(main())
