from falkordb import FalkorDB

db = FalkorDB(host="127.0.0.1", port=6379)
graph_name = "lotguard_demo"

# Reset only our synthetic demo graph for reproducibility.
if graph_name in db.list_graphs():
    db.select_graph(graph_name).delete()

graph = db.select_graph(graph_name)

graph.query("""
CREATE
  (bad:IngredientLot {
    id:'ING-001', name:'Maize Flour Lot A',
    status:'CONTAMINATED'
  }),
  (good:IngredientLot {
    id:'ING-002', name:'Maize Flour Lot B',
    status:'CLEAR'
  }),
  (spice:IngredientLot {
    id:'ING-003', name:'Spice Mix',
    status:'CLEAR'
  }),

  (r1:Run {id:'RUN-101'}),
  (r2:Run {id:'RUN-102'}),
  (r3:Run {id:'RUN-103'}),

  (b1:Batch {id:'BATCH-101', units:120}),
  (b2:Batch {id:'BATCH-102', units:80}),
  (b3:Batch {id:'BATCH-103', units:150}),

  (s1:Shipment {id:'SHIP-101', status:'IN_TRANSIT'}),
  (s2:Shipment {id:'SHIP-102', status:'IN_TRANSIT'}),
  (s3:Shipment {id:'SHIP-103', status:'IN_TRANSIT'}),

  (bad)-[:USED_IN]->(r1),
  (bad)-[:USED_IN]->(r2),
  (good)-[:USED_IN]->(r2),
  (good)-[:USED_IN]->(r3),
  (spice)-[:USED_IN]->(r3),

  (r1)-[:PRODUCED]->(b1),
  (r2)-[:PRODUCED]->(b2),
  (r3)-[:PRODUCED]->(b3),

  (b1)-[:SENT_AS]->(s1),
  (b2)-[:SENT_AS]->(s2),
  (b3)-[:SENT_AS]->(s3)
""")

result = graph.ro_query("""
MATCH (lot:IngredientLot {status:'CONTAMINATED'})
      -[:USED_IN]->(run:Run)
      -[:PRODUCED]->(batch:Batch)
      -[:SENT_AS]->(shipment:Shipment)
RETURN batch.id, shipment.id, batch.units
ORDER BY batch.id
""")

print("Affected batches:")
for row in result.result_set:
    print(row)

assert len(result.result_set) == 2
assert sum(row[2] for row in result.result_set) == 200

print("PASS: 2 affected batches, 200 units.")
print("Safe batch BATCH-103 is excluded.")
