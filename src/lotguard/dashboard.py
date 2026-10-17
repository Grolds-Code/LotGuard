from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from falkordb import FalkorDB

def db():
    return FalkorDB(host="127.0.0.1", port=6379).select_graph("lotguard_demo")

def h(x):
    return escape(str(x), quote=True)

class App(BaseHTTPRequestHandler):
    def do_GET(self):
        g = db()
        lots = g.ro_query("""
            MATCH (l:IngredientLot)
            RETURN l.id,l.name,l.status ORDER BY l.id
        """).result_set
        if not lots:
            return self.send_error(503, "Run seed.py first")

        params = parse_qs(urlparse(self.path).query)
        selected = params.get("lot", [lots[0][0]])[0]
        if selected not in [r[0] for r in lots]:
            selected = lots[0][0]

        status = next(r[2] for r in lots if r[0] == selected)

        paths = g.ro_query("""
            MATCH (l:IngredientLot {id:$lot})
                  -[:USED_IN]->(r:Run)
                  -[:PRODUCED]->(b:Batch)
                  -[:SENT_AS]->(s:Shipment)
            RETURN r.id,b.id,b.units,s.id,s.status ORDER BY b.id
        """, params={"lot": selected}).result_set

        inventory = g.ro_query("""
            MATCH (b:Batch)-[:SENT_AS]->(s:Shipment)
            RETURN b.id,b.units,s.id,s.status ORDER BY b.id
        """).result_set

        audits = g.ro_query("""
            MATCH (a:QuarantineAction)-[:TARGETS]->(s:Shipment)
            RETURN a.id,a.reason_lot,s.id ORDER BY a.id
        """).result_set

        pending = status == "CONTAMINATED" and any(
            p[4] == "IN_TRANSIT" for p in paths
        )

        options = "".join(
            f'<option value="{h(i)}" {"selected" if i == selected else ""}>'
            f'{h(i)} — {h(n)}</option>'
            for i,n,_ in lots
        )
        evidence = "".join(
            f'<p><code>{h(selected)} → {h(r)} → {h(b)} → {h(s)}</code>'
            f' — {h(n)} units — {h(st)}</p>'
            for r,b,n,s,st in paths
        ) or "<p>No downstream shipments found.</p>"

        rows = "".join(
            f"<tr><td>{h(b)}</td><td>{h(n)}</td>"
            f"<td>{h(s)}</td><td>{h(st)}</td></tr>"
            for b,n,s,st in inventory
        )
        history = "".join(
            f"<p><code>{h(a)}</code> — {h(l)} → {h(s)}</p>"
            for a,l,s in audits
        ) or "<p>No actions recorded yet.</p>"

        action = (
            f'<form method="post" action="/quarantine">'
            f'<input type="hidden" name="lot" value="{h(selected)}">'
            f'<button name="approve" value="yes" '
            f'onclick="return confirm(\'Quarantine affected shipments?\')">'
            f'Approve quarantine</button></form>'
            if pending else "<p>No pending quarantine for this lot.</p>"
        )

        page = f"""<!doctype html><html><head>
        <meta charset="utf-8"><meta name="viewport" content="width=device-width">
        <title>LotGuard</title>
        <style>
        body{{font:16px system-ui;background:#0b1424;color:#eef4fa;
              max-width:1000px;margin:auto;padding:28px}}
        h1{{font-size:38px;margin-bottom:4px;color:#5de5c0}}
        p{{line-height:1.6}}
        .card{{background:#18263a;border:1px solid #334860;
               padding:22px;border-radius:13px;margin:20px 0}}
        button,select{{padding:11px;border-radius:7px;font-size:15px}}
        button{{background:#5de5c0;border:0;cursor:pointer;font-weight:bold}}
        select{{background:#25364d;color:white}}
        table{{width:100%;border-collapse:collapse}}
        td,th{{padding:11px;border-bottom:1px solid #415269;text-align:left}}
        code{{color:#75e7cb;overflow-wrap:anywhere}}
        .small{{color:#adc0d2;font-size:13px}}
        </style></head><body>
        <h1>LotGuard</h1>
        <p>Graph-native supply-chain investigation and recall operations</p>
        <p class="small">Powered by FalkorDB · Synthetic demo data</p>
        <div class="card"><h2>Incident Investigation</h2>
        <form method="get"><select name="lot">{options}</select>
        <button>Investigate</button></form>
        <p>Ingredient status: <strong>{h(status)}</strong></p>
        <h3>Graph Evidence Paths</h3>{evidence}
        <h3>Agent Action</h3>{action}</div>
        <div class="card"><h2>Live Inventory</h2>
        <table><tr><th>Batch</th><th>Units</th><th>Shipment</th>
        <th>Status</th></tr>{rows}</table></div>
        <div class="card"><h2>Audit History</h2>{history}</div>
        <p class="small">Graph queries and controlled actions.
        No LLM reasoning is claimed.</p>
        </body></html>"""

        data = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/quarantine":
            return self.send_error(404)
        origin = self.headers.get("Origin")
        if origin and origin not in (
            "http://localhost:8765", "http://127.0.0.1:8765"
        ):
            return self.send_error(403)
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 1024:
            return self.send_error(400)
        data = parse_qs(self.rfile.read(size).decode())
        if data.get("approve") != ["yes"]:
            return self.send_error(403)
        lot = data.get("lot", [""])[0]

        g = db()
        g.query("""
            MATCH (l:IngredientLot {id:$lot,status:'CONTAMINATED'})
                  -[:USED_IN]->(:Run)-[:PRODUCED]->(:Batch)
                  -[:SENT_AS]->(s:Shipment {status:'IN_TRANSIT'})
            WITH DISTINCT s
            MERGE (a:QuarantineAction {id:$action})
            SET a.reason_lot=$lot
            MERGE (a)-[:TARGETS]->(s)
            SET s.status='QUARANTINED'
        """, params={
            "lot": lot,
            "action": "ACTION-" + uuid4().hex[:10]
        })

        self.send_response(303)
        self.send_header("Location", "/?lot=" + lot)
        self.end_headers()

if __name__ == "__main__":
    print("LotGuard dashboard: http://localhost:8765", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8765), App).serve_forever()
