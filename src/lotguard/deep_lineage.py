from falkordb import FalkorDB

db = FalkorDB(host="127.0.0.1", port=6379)
name = "lotguard_depth_demo"

# Separate graph: do not modify the original working demo.
if name in db.list_graphs():
    db.select_graph(name).delete()

graph = db.select_graph(name)

nodes = [
    ("IngredientLot", "ING-RISK"),
    ("IngredientLot", "ING-SAFE"),
    ("Run", "RUN-10"),
    ("Batch", "B-10"),
    ("Shipment", "SHIP-10"),
    ("Run", "RUN-20"),
    ("Batch", "B-20"),
    ("Run", "RUN-30"),
    ("Batch", "B-30"),
    ("Shipment", "SHIP-20"),
    ("Run", "RUN-40"),
    ("Batch", "B-40"),
    ("Warehouse", "WH-40"),
    ("Shipment", "SHIP-30"),
    ("Run", "RUN-50"),
    ("Batch", "B-50"),
    ("Shipment", "SHIP-40"),
    ("Batch", "B-UNKNOWN"),
    ("Shipment", "SHIP-50"),
]

for label, node_id in nodes:
    graph.query(
        f"CREATE (n:{label} {{id:$id}})",
        params={"id": node_id},
    )

# Directed material-provenance connections.
edges = [
    # Direct contamination: three hops.
    ("ING-RISK", "RUN-10"),
    ("RUN-10", "B-10"),
    ("B-10", "SHIP-10"),

    # Contaminated material enters another process.
    ("ING-RISK", "RUN-20"),
    ("RUN-20", "B-20"),

    # Reprocessing creates a five-hop risk path.
    ("B-20", "RUN-30"),
    ("ING-SAFE", "RUN-30"),
    ("RUN-30", "B-30"),
    ("B-30", "SHIP-20"),

    # Another branch travels through a warehouse.
    ("B-20", "RUN-40"),
    ("RUN-40", "B-40"),
    ("B-40", "WH-40"),
    ("WH-40", "SHIP-30"),

    # Independent safe-input processing.
    ("ING-SAFE", "RUN-50"),
    ("RUN-50", "B-50"),
    ("B-50", "SHIP-40"),

    # A shipment with incomplete upstream provenance.
    ("B-UNKNOWN", "SHIP-50"),
]

for source, target in edges:
    graph.query("""
        MATCH (a {id:$source}), (b {id:$target})
        CREATE (a)-[:FLOWS_TO]->(b)
    """, params={"source": source, "target": target})


def trace(max_depth):
    # Bound depth explicitly to control path expansion.
    query = f"""
        MATCH p=(lot:IngredientLot {{id:$lot}})
              -[:FLOWS_TO*1..{max_depth}]->(s:Shipment)
        RETURN s.id, length(p),
               [n IN nodes(p) | n.id]
        ORDER BY s.id, length(p)
    """
    return graph.ro_query(
        query, params={"lot": "ING-RISK"}
    ).result_set


shallow = trace(3)
deep = trace(8)

shallow_ids = {row[0] for row in shallow}
deep_ids = {row[0] for row in deep}

print("\nSHALLOW TRAVERSAL: MAX 3 HOPS")
print(shallow_ids)

print("\nDEEP TRAVERSAL: MAX 8 HOPS")
for shipment, depth, path in deep:
    print(f"{shipment} ({depth} hops): {' -> '.join(path)}")

# Ground truth comes from the independently defined graph.
expected_risk = {"SHIP-10", "SHIP-20", "SHIP-30"}

assert shallow_ids == {"SHIP-10"}
assert deep_ids == expected_risk
assert "SHIP-40" not in deep_ids
assert "SHIP-50" not in deep_ids

print("\nPASS: Deep traversal found all 3 known affected shipments.")
print("PASS: Shallow traversal missed 2 affected shipments.")
print("PASS: Independent shipment SHIP-40 was excluded.")
print("REVIEW: SHIP-50 has incomplete upstream provenance.")
print("Original lotguard_demo graph remains unchanged.")
