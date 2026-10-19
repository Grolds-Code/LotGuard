import asyncio
import os
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from falkordb import FalkorDB
from lotguard.deep_sentinel import investigate_deep, GRAPH_NAME
from mcp import Client
from lotguard.mcp_server import mcp
from lotguard.approval_gate import issue_local_approval
from lotguard.late_evidence import ingest_late_evidence
from lotguard.tool_agent import investigate, unpack

PORT = 8766
LOT = "ING-RISK"
PUBLIC_DEMO = os.getenv("LOTGUARD_PUBLIC_DEMO") == "1"


def safe(value):
    return escape(str(value), quote=True)



# LOTGUARD_MCP_APPROVED_ACTION_V1
async def approved_mcp_quarantine(lot_id):
    """Investigate and execute through approval-gated MCP."""
    report = await investigate(lot_id)
    proposal = report.get("proposal", {})
    pending = proposal.get("pending", [])

    if not pending:
        print("[MCP ACTION] No quarantine required.")
        return {"verified": True, "no_action": True}

    if report["decision"] != "REQUEST_HUMAN_QUARANTINE_APPROVAL":
        raise PermissionError(
            "Agent has not requested quarantine approval."
        )

    targets = sorted(set(pending))

    # Recheck FalkorDB before issuing a scoped ticket.
    current = sorted(investigate_deep(lot_id)["pending"])
    if current != targets:
        raise PermissionError(
            "Graph targets changed during investigation."
        )

    # Issued only by the trusted local approval handler.
    ticket = issue_local_approval(lot_id, targets)

    async with Client(mcp) as client:
        available = {
            tool.name
            for tool in (await client.list_tools()).tools
        }
        if "execute_approved_quarantine" not in available:
            raise RuntimeError(
                "MCP quarantine tool unavailable."
            )

        response = unpack(
            await client.call_tool(
                "execute_approved_quarantine",
                {
                    "lot_id": lot_id,
                    "shipments": targets,
                    "approval_ticket": ticket,
                },
            )
        )

    if (
        response.get("verified") is not True
        or sorted(response.get("quarantined", [])) != targets
    ):
        raise RuntimeError(
            "MCP quarantine verification failed."
        )

    print(
        "[MCP ACTION] Verified:",
        response["action_id"],
        targets,
    )
    return response



def render():
    plan = investigate_deep(LOT)
    graph = FalkorDB(
        host="127.0.0.1", port=6379
    ).select_graph(GRAPH_NAME)

    inventory = graph.ro_query("""
        MATCH (s:Shipment)
        RETURN s.id, s.units, s.status
        ORDER BY s.id
    """).result_set

    audits = graph.ro_query("""
        MATCH (a:QuarantineAction)-[:TARGETS]->(s:Shipment)
        RETURN a.id, s.id ORDER BY a.id, s.id
    """).result_set

    units = {sid: int(n) for sid, n, _ in inventory}
    risk = set(plan["affected"])
    review = set(plan["manual_review"])
    unrelated = set(plan["not_linked_in_known_graph"])

    affected_units = sum(units[s] for s in risk)
    review_units = sum(units[s] for s in review)
    unrelated_units = sum(units[s] for s in unrelated)

    shallow = len({
        p["shipment"] for p in plan["evidence_paths"]
        if p["depth"] <= 3
    })
    total = len(risk)
    shallow_pct = 100 * shallow / max(total, 1)

    evidence_html = "".join(
        '<div class="evidence">'
        f'<span class="hop">{p["depth"]} HOPS</span>'
        f'<code>{safe(" → ".join(p["nodes"]))}</code>'
        '</div>'
        for p in plan["evidence_paths"]
    )

    inventory_html = ""
    for sid, quantity, status in inventory:
        category = (
            "Review" if sid in review
            else "Affected" if sid in risk
            else "No known risk path"
        )
        tone = (
            "review" if sid in review
            else "risk" if sid in risk
            else "neutral"
        )
        state_tone = (
            "quarantined" if status == "QUARANTINED"
            else "transit"
        )
        inventory_html += (
            f"<tr class='inventory-row'>"
            f"<td class='shipment-id'><strong>{safe(sid)}</strong></td>"
            f"<td class='unit-count'>{safe(quantity)}</td>"
            f"<td><span class='assessment {tone}'>"
            f"{safe(category)}</span></td>"
            f"<td><span class='chip state-{state_tone}'>"
            f"{safe(status)}</span></td>"
            "</tr>"
        )

    grouped = {}
    for action, shipment in audits:
        grouped.setdefault(action, []).append(shipment)

    audit_html = "".join(
        "<div class='audit-group'>"
        "<div class='audit-heading'>VERIFIED GRAPH ACTION</div>"
        f"<div class='audit-id'>{safe(action)}</div>"
        "<div class='audit-targets'>"
        + "".join(
            f"<span class='target'>{safe(sid)}</span>"
            for sid in sorted(set(shipments))
        )
        + "</div></div>"
        for action, shipments in grouped.items()
    ) or "<p class='muted'>No quarantine actions recorded yet.</p>"


    # LOTGUARD_EVOLVING_INCIDENTS_V2
    evidence_received = bool(graph.ro_query("""
        MATCH (e:ProvenanceEvidence {id:'EVID-SUPPLIER-50'})
        RETURN e.id
    """).result_set)

    first_wave_done = {
        "SHIP-10", "SHIP-20", "SHIP-30"
    }.issubset(set(plan["already_quarantined"]))

    if "SHIP-50" in review:
        provenance_html = (
            '<div class="notice">'
            'SHIP-50 has incomplete upstream provenance. '
            'Missing lineage is not proof of safety.'
            '</div>'
        )
        if first_wave_done and not plan["pending"] and not PUBLIC_DEMO:
            provenance_html += """
            <form method="post" action="/ingest-evidence">
              <input type="hidden" name="confirmed" value="yes">
              <button type="submit" class="secondary-action"
                onclick="return confirm('Receive synthetic supplier evidence and reassess the incident?')">
                Receive supplier evidence &amp; reassess →
              </button>
            </form>
            """
        else:
            provenance_html += (
                '<p class="small">'
                'Complete the initial containment before '
                'introducing additional supplier evidence.'
                '</p>'
            )
    elif evidence_received:
        provenance_html = (
            '<div class="notice evidence-resolved">'
            'Supplier evidence EVID-SUPPLIER-50 received. '
            'SHIP-50 is now connected to the contaminated '
            'ingredient through a verified graph path.'
            '</div>'
        )
    else:
        provenance_html = (
            '<div class="complete">'
            'No unresolved provenance gaps detected.'
            '</div>'
        )

    if evidence_received:
        audit_html = (
            '<div class="audit-group">'
            '<div class="audit-heading">SUPPLIER EVIDENCE RECEIVED</div>'
            '<div class="audit-id">EVID-SUPPLIER-50</div>'
            '<p class="small">B-20 → RUN-60 → '
            'B-UNKNOWN → SHIP-50</p>'
            '</div>'
        ) + audit_html

    if plan["pending"] and not PUBLIC_DEMO:
        action_html = """
        <form method="post" action="/approve">
          <input type="hidden" name="approved" value="yes">
          <button onclick="return confirm('Approve quarantine of the identified synthetic shipments?')">
            Approve and verify quarantine →
          </button>
        </form>
        """
    elif PUBLIC_DEMO:
        action_html = (
            '<div class="notice">Read-only public demo. '
            'Watch the recorded demonstration for approved actions.</div>'
            '<p style="margin:16px 0 4px">'
            '<a href="https://www.youtube.com/watch?v=tTOlHlMm5kE" '
            'target="_blank" rel="noopener noreferrer" '
            'style="display:inline-block;max-width:100%;'
            'box-sizing:border-box;padding:14px 18px;'
            'background:#F4C96D;color:#172033;'
            'border:2px solid #FFE3A0;border-radius:10px;'
            'font-size:16px;font-weight:800;'
            'text-decoration:none;text-align:center;">'
            '&#9654; Watch the full intervention demo &rarr;'
            '</a></p>'
        )
    else:
        action_html = """
        <div class="complete">No pending quarantine actions.</div>
        """

    page = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LotGuard Sentinel | Provenance Operations</title>
<style>
* { box-sizing:border-box; }
body {
 margin:0;background:#0b1220;color:#e6ebf2;
 font-family:Inter,Segoe UI,system-ui,Arial,sans-serif;
}
main {max-width:1150px;margin:auto;padding:34px 22px 70px}
header {
 display:flex;justify-content:space-between;align-items:center;
 border-bottom:1px solid #294055;padding-bottom:24px;gap:12px;
}
h1 {font-size:30px;letter-spacing:-1px;margin:5px 0}
h2 {font-size:17px;margin:0 0 18px}
p {line-height:1.55}
.muted {color:#98abc0}
.eyebrow {font-size:11px;letter-spacing:2px;color:#8ea4bf;font-weight:800}

.connected {
 color:#afdfd1;
 background:#152e2b;
 border:1px solid #467a6d;
 padding:8px 12px;
 border-radius:30px;
 font-size:12px;
 font-weight:600;
}

.grid {
 display:grid;grid-template-columns:repeat(3,1fr);gap:17px;
 margin-top:25px;
}
.card {
 background:#111a2b;border:1px solid #253247;
 border-radius:15px;padding:22px;margin-top:18px;
}
.grid .card {margin:0}

.stat {
 display:flex;
 align-items:baseline;
 gap:12px;
 flex-wrap:nowrap;
 font-size:32px;
 font-weight:800;
 margin-top:10px;
 letter-spacing:-1px;
 line-height:1.2;
}
.stat .small {
 font-size:14px;
 font-weight:400;
 color:#a6b9cb;
 letter-spacing:0;
 line-height:1.4;
 white-space:nowrap;
}

.small {font-size:12px;color:#94a3b8}
.accent {color:#9fb3c8}
.warning {color:#AFCBE6}
.section {
 display:grid;grid-template-columns:1.1fr 0.9fr;
 gap:18px;margin-top:20px;
}
.section .card {margin:0}
.track {
 background:#1e2a3c;height:10px;border-radius:20px;
 overflow:hidden;margin:10px 0 20px;
}
.fill {height:100%;background:#7189a6;border-radius:20px}
.compare {display:flex;justify-content:space-between;gap:12px}
.evidence {
 padding:13px 0;border-bottom:1px solid #30465c;
 display:flex;gap:15px;align-items:flex-start;
}
.hop {
 background:#223146;color:#c8d3df;border-radius:6px;
 padding:5px 8px;font-size:10px;font-weight:800;
 white-space:nowrap;
}
code {font-size:12px;color:#b7c5d6;overflow-wrap:anywhere}
table {width:100%;border-collapse:collapse;font-size:13px}
td,th {text-align:left;padding:13px 8px;border-bottom:1px solid #30465c}
th {color:#a6b4c5;font-weight:600}
.chip {
 background:#1c2839;padding:5px 9px;border-radius:7px;
 font-size:11px;
}
button {
 background:#7087a4;color:#f8fafc;border:0;
 border-radius:9px;padding:14px 19px;
 font-size:14px;font-weight:800;cursor:pointer;
}
button:hover {background:#8197b2}
.complete {
 background:#1b2433;border:1px solid #34465b;
 padding:14px;border-radius:9px;color:#d3dde8;
}
.audit {
 display:flex;gap:14px;padding:11px 0;
 border-bottom:1px solid #30465c;font-size:13px;
}

.notice {
 margin-top:17px;
 padding:15px 17px;
 background:#32271e;
 color:#f0d2aa;
 border:1px solid #66503a;
 border-left:4px solid #7296BA;
 border-radius:9px;
 font-size:13px;
 line-height:1.6;
}

footer {margin-top:32px;color:#8d9caf;font-size:12px}
@media(max-width:760px) {
 .grid,.section {grid-template-columns:1fr}
 h1 {font-size:25px}
 header {align-items:flex-start}
 .card {overflow-x:auto}
}

/* LOTGUARD_FINAL_OPERATIONAL_STYLE */

/* A restrained blue-gold operational identity */
.connected {
  color:#BFD5EB;
  background:#172638;
  border:1px solid #547398;
  font-weight:600;
}

.notice {
  background:#172638;
  color:#D6E0EB;
  border:1px solid #354B65;
  border-left:3px solid #7296BA;
  border-radius:7px;
  padding:16px 18px;
  line-height:1.6;
}
.notice::before {
  content:"PROVENANCE REVIEW REQUIRED";
  display:block;
  margin-bottom:7px;
  color:#B8D2E9;
  font-size:11px;
  font-weight:700;
  letter-spacing:1.1px;
}

/* Inventory layout */
table {
  border-collapse:separate;
  border-spacing:0;
}
table th {
  background:#1C2D42;
  color:#BED0E5;
  font-size:12px;
  font-weight:650;
  letter-spacing:.35px;
}
.inventory-row:nth-child(even) {
  background:rgba(123,153,190,.065);
}
.inventory-row:hover {
  background:#1D3047;
}
.inventory-row td {
  padding:15px 9px;
}
.shipment-id {
  color:#BED4ED;
}
.unit-count {
  color:#E3EAF2;
  font-variant-numeric:tabular-nums;
}
.assessment {
  font-size:12px;
  font-weight:650;
}
.assessment.risk,
.assessment-affected {
  color:#C8D7E6;
}
.assessment.review,
.assessment-review {
  color:#C6D9E8;
}
.assessment.neutral,
.assessment-unlinked {
  color:#A7C0DA;
}

/* Calm but distinct status indicators */
.chip {
  display:inline-block;
  border:1px solid transparent;
  border-radius:6px;
  padding:6px 10px;
  font-size:11px;
  font-weight:650;
}
.state-quarantined {
  background:#22372F;
  border-color:#425B4E;
  color:#B5D2BD;
}
.state-transit {
  background:#25364C;
  border-color:#40536A;
  color:#BFD0E4;
}
.state-other {
  background:#26384A;
  color:#C8D8E9;
}

/* Grouped audit records */
.audit-group,
.audit-entry {
  padding:18px;
  margin-top:12px;
  background:#192A3D;
  border:1px solid #3C526B;
  border-left:3px solid #7899BD;
  border-radius:8px;
}
.audit-heading,
.audit-label {
  font-size:10px;
  color:#A3BAD3;
  font-weight:700;
  letter-spacing:1.2px;
  margin-bottom:9px;
}
.audit-id,
.audit-identifier {
  color:#D9E5F2;
  font-size:13px;
  font-weight:650;
  overflow-wrap:anywhere;
}
.audit-targets {
  display:flex;
  gap:9px;
  flex-wrap:wrap;
  margin-top:14px;
}
.target,
.target-pill {
  background:#253A52;
  color:#C9DBED;
  border:1px solid #425C79;
  border-radius:5px;
  padding:6px 10px;
  font-size:12px;
}

@media(max-width:760px) {
  .inventory-row td {padding:11px 8px}
  .assessment,.chip {font-size:11px}
}


/* LOTGUARD_MOBILE_COMPACT_V1 */

@media screen and (max-width:600px) {

  html {
    -webkit-text-size-adjust:100%;
    text-size-adjust:100%;
  }

  body {
    font-size:13px;
    line-height:1.45;
  }

  main {
    padding:18px 14px 42px;
  }

  header {
    padding-bottom:16px;
    gap:10px;
    align-items:flex-start;
  }

  h1 {
    font-size:21px;
    line-height:1.2;
    letter-spacing:-0.5px;
  }

  h2 {
    font-size:15px;
    line-height:1.3;
    margin-bottom:13px;
  }

  .eyebrow {
    font-size:9px;
    letter-spacing:1.1px;
  }

  header .muted {
    font-size:12px;
    line-height:1.4;
  }

  .connected {
    font-size:10px;
    line-height:1.3;
    padding:6px 8px;
    max-width:112px;
    text-align:center;
  }

  .grid,
  .section {
    grid-template-columns:1fr;
    gap:10px;
    margin-top:14px;
  }

  .card {
    padding:15px;
    border-radius:11px;
    margin-top:10px;
  }

  .grid .card,
  .section .card {
    margin:0;
  }

  .stat {
    font-size:25px;
    line-height:1.15;
    margin-top:6px;
    gap:8px;
    letter-spacing:-0.6px;
  }

  .stat .small {
    font-size:12px;
    font-weight:400;
  }

  .small {
    font-size:11px;
  }

  p {
    font-size:12px;
    line-height:1.5;
  }

  .evidence {
    padding:10px 0;
    gap:9px;
  }

  .evidence code {
    font-size:10px;
    line-height:1.5;
  }

  .hop {
    font-size:9px;
    padding:4px 6px;
  }

  .compare {
    font-size:12px;
  }

  .notice {
    padding:12px;
    font-size:12px;
    line-height:1.5;
  }

  .notice::before {
    font-size:10px;
    letter-spacing:0.7px;
  }

  /* Allow wide data tables to scroll */
  table {
    min-width:480px;
    font-size:12px;
  }

  th,
  td {
    font-size:11px;
    padding:10px 7px;
  }

  .assessment,
  .chip {
    font-size:10px;
  }

  .audit-group,
  .audit-entry {
    padding:12px;
  }

  .audit-id,
  .audit-identifier {
    font-size:12px;
  }

  .audit-targets {
    gap:7px;
  }

  .target,
  .target-pill {
    font-size:11px;
    padding:5px 8px;
  }

  button {
    font-size:12px;
    padding:11px 13px;
  }

  footer {
    font-size:11px;
    line-height:1.5;
  }
}


/* EVOLVING INCIDENT WORKFLOW */
.secondary-action {
    display:block;
    margin-top:14px;
    background:#26415C;
    border:1px solid #6689AC;
    color:#E1ECF7;
}
.secondary-action:hover {
    background:#345573;
}
.evidence-resolved {
    border-left-color:#86A99B;
}
.evidence-resolved::before {
    content:"SUPPLIER EVIDENCE RECEIVED";
    color:#B7D5C7;
}
@media(max-width:600px) {
    .secondary-action {
        width:100%;
        white-space:normal;
        text-align:center;
    }
}


/* LOTGUARD_MCP_TRACE_V2 */
.trace-link {
  display:inline-block;
  margin-top:10px;
  padding:12px 17px;
  border:1px solid #597796;
  border-radius:8px;
  background:#253B54;
  color:#E1ECF7;
  font-size:13px;
  font-weight:650;
  text-decoration:none;
}
.trace-link:hover {
  background:#324E6C;
}
@media(max-width:600px) {
  .trace-link {
    display:block;
    text-align:center;
    font-size:12px;
  }
}

</style>
</head>
<body>
<main>
<header>
 <div>
  <div class="eyebrow">GRAPH-NATIVE INCIDENT RESPONSE</div>
  <h1>LotGuard <span class="accent">Sentinel</span></h1>
  <div class="muted">Supply Chain Provenance Operations Center</div>
 </div>
 <span class="connected">● FalkorDB Connected</span>
</header>

<div class="grid">
 <div class="card">
  <div class="small">KNOWN AFFECTED INVENTORY</div>
  <div class="stat">__AFFECTED__ <span class="small">units</span></div>
  <div class="small">__AFFECTED_COUNT__ connected shipments</div>
 </div>
 <div class="card">
  <div class="small">NO KNOWN CONTAMINATION PATH</div>
  <div class="stat accent">__UNRELATED__ <span class="small">units</span></div>
  <div class="small">Not automatically declared safe</div>
 </div>
 <div class="card">
  <div class="small">INCOMPLETE PROVENANCE</div>
  <div class="stat warning">__REVIEW__ <span class="small">units</span></div>
  <div class="small">Manual investigation required</div>
 </div>
</div>

<div class="section">
 <div class="card">
  <h2>Contamination evidence paths</h2>
  <p class="muted">Source: ING-RISK · Live FalkorDB query</p>
  __EVIDENCE__
 </div>
 <div class="card">
  <h2>Why graph depth matters</h2>
  <div class="compare"><span>3-hop traversal</span><strong>__SHALLOW__ / __TOTAL__</strong></div>
  <div class="track"><div class="fill" style="width:__SHALLOW_PCT__%"></div></div>
  <div class="compare"><span>8-hop traversal</span><strong>__TOTAL__ / __TOTAL__</strong></div>
  <div class="track"><div class="fill" style="width:100%"></div></div>
  <p class="muted">Detection of known affected shipments in the synthetic scenario.</p>
  __PROVENANCE_PANEL__
  <h2 style="margin-top:24px">Human-approved MCP intervention</h2>
  <p class="small">Decision: __DECISION__</p>
  __ACTION__
 </div>
</div>


<div class="card">
 <h2>Adaptive MCP investigator</h2>
 <p class="muted">
  Execute a read-only investigation using real
  FalkorDB-backed MCP tools and inspect why the
  agent selected each operation.
 </p>
 <a class="trace-link" href="/agent-trace">
  Run MCP investigation and view tool trace →
 </a>
</div>
<div class="card">
 <h2>Live shipment inventory</h2>
 <table>
 <tr><th>Shipment</th><th>Units</th><th>Assessment</th><th>State</th></tr>
 __INVENTORY__
 </table>
</div>

<div class="card">
 <h2>Recorded graph actions</h2>
 __AUDITS__
</div>

<footer>
 FalkorDB · Python 3.12 · Graph provenance · Human-approved synthetic actions<br>
 Deterministic graph-based reasoning; no LLM autonomy claimed.
</footer>
</main>
</body>
</html>"""

    substitutions = {
        "__AFFECTED__": affected_units,
        "__AFFECTED_COUNT__": total,
        "__UNRELATED__": unrelated_units,
        "__REVIEW__": review_units,
        "__EVIDENCE__": evidence_html,
        "__SHALLOW__": shallow,
        "__TOTAL__": total,
        "__SHALLOW_PCT__": f"{shallow_pct:.1f}",
        "__DECISION__": safe(plan["decision"]),
        "__ACTION__": action_html,
        "__PROVENANCE_PANEL__": provenance_html,
        "__INVENTORY__": inventory_html,
        "__AUDITS__": audit_html,
    }

    for key, value in substitutions.items():
        page = page.replace(key, str(value))

    return page.encode("utf-8")



def render_agent_trace():
    """Run the real read-only MCP agent and render its tool trace."""
    report = asyncio.run(investigate(LOT))

    steps = "".join(
        "<li>"
        f"<strong>{safe(step['tool'])}</strong>"
        f"<p>{safe(step['reason'])}</p>"
        "</li>"
        for step in report["trace"]
    )

    proposal = report.get("proposal", {})
    pending = ", ".join(proposal.get("pending", [])) or "None"
    missed = ", ".join(report.get("newly_discovered", [])) or "None"

    html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LotGuard | MCP Agent Trace</title>
<style>
* {box-sizing:border-box}
body {
 margin:0;background:#0b1220;color:#e6ebf2;
 font-family:Inter,Segoe UI,system-ui,Arial,sans-serif
}
main {max-width:850px;margin:auto;padding:30px 18px 70px}
h1 {font-size:25px}
h2 {font-size:17px}
p {line-height:1.55}
.card {
 margin-top:18px;padding:22px;background:#111a2b;
 border:1px solid #30465c;border-radius:12px
}
.muted {color:#a5b9cc;font-size:13px}
a {color:#b8d6f3}
li {
 padding:14px 5px;
 border-bottom:1px solid #30465c;
 overflow-wrap:anywhere
}
li strong {color:#c5dbef;font-size:14px}
li p {color:#aebed0;font-size:12px}
@media(max-width:600px) {
 h1 {font-size:21px}
 .card {padding:15px}
}

/* LOTGUARD_MCP_COMPACT_METRICS_V1 */

body {
  font-size:13px;
}

main {
  max-width:800px;
}

h1 {
  font-size:23px;
  letter-spacing:-0.45px;
}

.card {
  padding:18px;
}

.assessment-grid {
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:9px;
}

.metric {
  min-width:0;
  background:#16263B;
  border:1px solid #2D435B;
  border-radius:8px;
  padding:12px 14px;
}

.metric-wide {
  grid-column:1/-1;
}

.metric-label {
  color:#A5BCD3;
  font-size:11px;
  font-weight:600;
  line-height:1.4;
  margin-bottom:6px;
}

.metric-value {
  color:#E1EAF4;
  font-size:13px;
  line-height:1.45;
  overflow-wrap:anywhere;
}

.metric-value.decision {
  color:#CEE1F4;
  font-size:13px;
  font-weight:650;
}

.metric-value.number {
  font-size:20px;
  font-weight:700;
  line-height:1.15;
  letter-spacing:-0.4px;
}

.assessment-grid + p {
  font-size:12px;
}

@media(max-width:600px) {
  main {
    padding:20px 13px 45px;
  }

  h1 {
    font-size:20px;
    line-height:1.25;
  }

  h2 {
    font-size:15px;
  }

  .card {
    padding:14px;
    margin-top:12px;
  }

  .assessment-grid {
    gap:8px;
  }

  .metric {
    padding:10px 11px;
  }

  .metric-label {
    font-size:10px;
  }

  .metric-value,
  .metric-value.decision {
    font-size:12px;
  }

  .metric-value.number {
    font-size:18px;
  }
}

</style>
</head>
<body>
<main>
<a href="/">← Back to operations dashboard</a>
<h1>Sentinel MCP Investigation Trace</h1>
<p class="muted">
Live, read-only MCP investigation against FalkorDB.
Policy-driven tool selection; no LLM autonomy.
</p>
<div class="card">
<h2>Graph-grounded assessment</h2>
<div class="assessment-grid">
 <div class="metric metric-wide">
  <div class="metric-label">Agent decision</div>
  <div class="metric-value decision">__DECISION__</div>
 </div>

 <div class="metric">
  <div class="metric-label">Pending units</div>
  <div class="metric-value number">__UNITS__</div>
 </div>

 <div class="metric">
  <div class="metric-label">MCP tool calls</div>
  <div class="metric-value number">__COUNT__</div>
 </div>

 <div class="metric metric-wide">
  <div class="metric-label">Pending shipments</div>
  <div class="metric-value">__PENDING__</div>
 </div>

 <div class="metric metric-wide">
  <div class="metric-label">Additional paths beyond 3 hops</div>
  <div class="metric-value">__MISSED__</div>
 </div>
</div>
</div>
<div class="card">
<h2>Explainable tool selection</h2>
<ol>__STEPS__</ol>
</div>
<p class="muted">
This investigation cannot approve or execute quarantine.
</p>
</main>
</body>
</html>"""

    substitutions = {
        "__DECISION__": safe(report["decision"].replace("_", " ").capitalize()),
        "__UNITS__": safe(report["pending_units"]),
        "__PENDING__": safe(pending),
        "__MISSED__": safe(missed),
        "__COUNT__": str(len(report["trace"])),
        "__STEPS__": steps,
    }

    for key, value in substitutions.items():
        html = html.replace(key, str(value))

    return html.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        endpoint = urlparse(self.path).path
        if endpoint not in ("/", "/agent-trace"):
            return self.send_error(404)
        try:
            data = (
                render() if endpoint == "/"
                else render_agent_trace()
            )
        except Exception as exc:
            return self.send_error(500, str(exc))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if PUBLIC_DEMO:
            return self.send_error(403, "Read-only public demo")
        endpoint = urlparse(self.path).path
        if endpoint not in ("/approve", "/ingest-evidence"):
            return self.send_error(404)
        origin = self.headers.get("Origin", "")
        if origin and origin not in (
            f"http://127.0.0.1:{PORT}",
            f"http://localhost:{PORT}",
        ):
            return self.send_error(403, "Cross-origin request blocked")
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
            return self.send_error(415)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1024:
                return self.send_error(400)
            data = parse_qs(self.rfile.read(size).decode())
            if endpoint == "/approve":
                if data.get("approved") != ["yes"]:
                    return self.send_error(403, "Approval required")
                asyncio.run(approved_mcp_quarantine(LOT))
            else:
                if data.get("confirmed") != ["yes"]:
                    return self.send_error(403, "Confirmation required")

                current = investigate_deep(LOT)
                first_wave_done = {
                    "SHIP-10", "SHIP-20", "SHIP-30"
                }.issubset(
                    set(current["already_quarantined"])
                )

                if (
                    not first_wave_done
                    or current["pending"]
                    or "SHIP-50" not in current["manual_review"]
                ):
                    return self.send_error(
                        409,
                        "Evidence ingestion is unavailable "
                        "at this incident stage."
                    )

                ingest_late_evidence(apply=True)
        except Exception as exc:
            return self.send_error(500, str(exc))
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


if __name__ == "__main__":
    print(
        f"LotGuard Sentinel: http://127.0.0.1:{PORT}",
        flush=True,
    )
    ThreadingHTTPServer(
        ("0.0.0.0" if PUBLIC_DEMO else "127.0.0.1", PORT), Handler
    ).serve_forever()
