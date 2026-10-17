from falkordb import FalkorDB

db = FalkorDB(host="127.0.0.1", port=6379)
graph = db.select_graph("lotguard_demo")

lot_id = "ING-001"
action_id = "QUARANTINE-001"

# 1. Investigate using graph relationships.
result = graph.ro_query("""
MATCH (lot:IngredientLot {id:$lot_id, status:'CONTAMINATED'})
      -[:USED_IN]->(run:Run)
      -[:PRODUCED]->(batch:Batch)
      -[:SENT_AS]->(shipment:Shipment)
RETURN DISTINCT batch.id, shipment.id, batch.units
ORDER BY batch.id
""", params={"lot_id": lot_id})

affected = result.result_set

if not affected:
    raise SystemExit("No affected shipments found. No action taken.")

expected_shipments = {row[1] for row in affected}

print("\nLOTGUARD INVESTIGATION")
print("----------------------")

for batch_id, shipment_id, units in affected:
    print(f"{batch_id} -> {shipment_id}: {units} units")

# 2. Execute a controlled quarantine on synthetic data.
graph.query("""
MATCH (lot:IngredientLot {id:$lot_id, status:'CONTAMINATED'})
      -[:USED_IN]->(:Run)
      -[:PRODUCED]->(:Batch)
      -[:SENT_AS]->(shipment:Shipment)
WITH DISTINCT lot, shipment
MERGE (action:QuarantineAction {id:$action_id})
SET action.reason_lot = $lot_id
MERGE (action)-[:TARGETS]->(shipment)
SET shipment.status = 'QUARANTINED'
""", params={
    "lot_id": lot_id,
    "action_id": action_id,
})

# 3. Verify the resulting inventory state.
result = graph.ro_query("""
MATCH (shipment:Shipment)
RETURN shipment.id, shipment.status
ORDER BY shipment.id
""")

statuses = {row[0]: row[1] for row in result.result_set}

print("\nPOST-ACTION VERIFICATION")
print("------------------------")

for shipment_id, status in statuses.items():
    print(f"{shipment_id}: {status}")

assert all(
    statuses[s] == "QUARANTINED"
    for s in expected_shipments
)

assert statuses["SHIP-103"] == "IN_TRANSIT"

# 4. Verify the graph contains an audit record.
audit = graph.ro_query("""
MATCH (action:QuarantineAction {id:$action_id})
      -[:TARGETS]->(shipment:Shipment)
RETURN DISTINCT shipment.id
""", params={"action_id": action_id})

recorded_shipments = {row[0] for row in audit.result_set}

assert recorded_shipments == expected_shipments

print("\nPASS: Affected shipments quarantined.")
print("PASS: Safe shipment remains untouched.")
print("PASS: Quarantine action recorded in FalkorDB.")
