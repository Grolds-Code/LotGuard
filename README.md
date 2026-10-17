# LotGuard

### Graph-native supply-chain investigation and controlled recall operations

**Graph Hacks 2026 — FalkorDB × WeMakeDevs**  
**Submission track:** Track 01 — Agents That Act on Connected Data

LotGuard is a graph-powered incident-response prototype that traces contaminated ingredients through manufacturing runs, finished-product batches, and shipments. It identifies affected inventory, supports approval-controlled quarantine, and records intervention evidence inside FalkorDB.

**Core idea:** Quarantine what is actually connected to a contaminated lot while preserving unaffected inventory.

## The problem

An ingredient may enter several manufacturing runs, be combined with other ingredients, and reach multiple downstream shipments. Identifying affected stock requires following the relationships between the original ingredient and downstream products.

Overly broad recalls waste safe inventory. Incomplete tracing leaves potentially unsafe products in circulation.

LotGuard uses a graph to support targeted, explainable intervention.

## Demonstrated functionality

- Multi-hop ingredient-to-shipment tracing in FalkorDB
- Identification of affected and unaffected inventory
- Evidence paths showing why each shipment is implicated
- State-aware investigation of pending and completed quarantines
- Human approval before synthetic quarantine actions
- Persistent graph-based intervention records
- A local interactive dashboard
- Deterministic verification of quarantine outcomes

LotGuard currently implements a deterministic, graph-driven investigation and action workflow. It does not use an LLM, autonomous tool selection, or MCP in this version.

## Architecture

```text
Browser dashboard (localhost:8765)
              |
              v
Python HTTP server (standard library)
              |
              v
Investigation and quarantine logic
              |
              v
FalkorDB Python client
              |
              v
FalkorDB graph database (localhost:6379)
```

FalkorDB is the source of truth for ingredient lineage, shipment state, and quarantine action records. Removing the database would prevent the investigation and intervention workflows from functioning.

## Graph data model

### Node types

| Node | Purpose | Main properties |
|---|---|---|
| IngredientLot | Source ingredient lot | id, name, status |
| Run | Manufacturing event | id |
| Batch | Finished-product batch | id, units |
| Shipment | Outbound inventory movement | id, status |
| QuarantineAction | Recorded intervention | id, reason_lot |

### Relationships

```text
(IngredientLot)-[:USED_IN]->(Run)
(Run)-[:PRODUCED]->(Batch)
(Batch)-[:SENT_AS]->(Shipment)
(QuarantineAction)-[:TARGETS]->(Shipment)
```

A manufacturing run can consume multiple ingredient lots. Therefore, a finished batch may be connected to both safe and contaminated inputs.

A clear ingredient does not automatically imply that all downstream shipments are safe: another ingredient in the same manufacturing run might be contaminated.

## Essential Cypher query

The central contamination-tracing query is:

```cypher
MATCH (lot:IngredientLot {status:'CONTAMINATED'})
      -[:USED_IN]->(run:Run)
      -[:PRODUCED]->(batch:Batch)
      -[:SENT_AS]->(shipment:Shipment)
RETURN batch.id, shipment.id, batch.units
ORDER BY batch.id
```

This query traverses three relationship types to establish which shipments are downstream of contaminated ingredients.

The quarantine operation uses the same lineage relationships to select eligible shipments before changing their status and linking them to a `QuarantineAction` node.

Full query implementations are available in:

- `src/lotguard/seed.py`
- `src/lotguard/agent.py`
- `src/lotguard/quarantine.py`
- `src/lotguard/dashboard.py`

## Local setup

### Requirements

- Python 3.12+
- uv Python package manager
- Docker with Linux container support
- Local access to ports 6379 and 8765

### 1. Clone and install

```bash
git clone https://github.com/Grolds-Code/LotGuard.git
cd LotGuard
uv sync
```

### 2. Start FalkorDB

With a working Linux Docker CLI:

```bash
docker run -d --name lotguard-falkordb \
  -p 127.0.0.1:6379:6379 \
  -v lotguard-falkordb-data:/data \
  falkordb/falkordb:latest
```

If the container already exists but is stopped:

```bash
docker start lotguard-falkordb
```

On Windows with WSL, Docker Desktop's WSL integration should ordinarily make the Docker CLI available inside Ubuntu.

If that integration is unavailable but Docker Desktop's engine is running, the Windows CLI can also be invoked from WSL:

```bash
"/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe" version
```

Use that executable instead of `docker` for the container commands if necessary.

### 3. Seed the synthetic graph

```bash
uv run --no-sync python src/lotguard/seed.py
```

This creates the `lotguard_demo` graph with three ingredient lots, three manufacturing runs, three product batches, and three shipments.

**Warning:** Running the seed script again deletes and recreates only `lotguard_demo`, including its previous quarantine audit records. Use it to reset the synthetic demonstration.

### 4. Run the dashboard

```bash
uv run --no-sync python -m lotguard.dashboard
```

Open:

http://127.0.0.1:8765

### 5. Demonstrate an intervention

1. Select `ING-001`, the contaminated maize-flour lot.
2. Click **Investigate**.
3. Inspect the evidence paths to `SHIP-101` and `SHIP-102`.
4. Click **Approve quarantine** and accept the confirmation.
5. Verify that both affected shipments become `QUARANTINED`.
6. Verify that unaffected `SHIP-103` remains `IN_TRANSIT`.
7. Inspect the generated quarantine action under Audit History.

### 6. Run the command-line investigation

```bash
uv run --no-sync python -m lotguard.agent --lot ING-001
```

The investigator reports the paths and whether any pending interventions remain.

## Verified synthetic demonstration

| Shipment | Units | Post-action state |
|---|---:|---|
| SHIP-101 | 120 | QUARANTINED |
| SHIP-102 | 80 | QUARANTINED |
| SHIP-103 | 150 | IN_TRANSIT |

The demonstrated intervention quarantines 200 affected units while preserving 150 unaffected units.

A manual FalkorDB verification confirmed that the affected shipments were quarantined, the unaffected shipment was unchanged, and the quarantine action had relationships to both affected shipments.

These observations establish correctness for the small synthetic example, not general recall accuracy across real-world supply chains.

## Current limitations

- Synthetic dataset only
- Fixed, three-hop manufacturing lineage model
- Deterministic decision rules, not LLM reasoning
- No real shipment-system or ERP integration
- Local-only demo without production authentication
- No formal optimization of replacement stock
- Not validated for operational food-safety decisions

This is a hackathon prototype, not a certified recall-management system.

## Technology

- Python 3.12
- FalkorDB graph database
- FalkorDB Python client
- Cypher
- Python standard-library HTTP server
- Docker
- uv

## Hackathon and AI-assistance disclosure

Created for Graph Hacks 2026, October 15–18.

ChatGPT was used extensively for concept exploration, architecture discussion, code drafting, debugging instructions, and documentation assistance.

The participant configured the environment, executed and tested the implementation, verified FalkorDB graph operations and quarantine outcomes, and is responsible for reviewing, understanding, and submitting the project.

## Repository

https://github.com/Grolds-Code/LotGuard

## LotGuard Sentinel — Evidence-Based Intervention

Sentinel extends LotGuard with an evidence-producing investigation planner and an approval-controlled quarantine executor.

### Generate an investigation plan

~~~bash
uv run --no-sync python -m lotguard.sentinel --lot ING-001
~~~

The planner queries FalkorDB and produces:
- Ingredient-to-shipment evidence paths
- Pending quarantine recommendations
- Already-quarantined shipments
- Inventory with no known contamination path
- Escalation recommendations when appropriate
- A JSON evidence report in `reports/`

### Preview the intervention

~~~bash
uv run --no-sync python -m lotguard.executor --lot ING-001
~~~

This shows the proposed intervention without changing inventory.

### Execute an approved intervention

~~~bash
uv run --no-sync python -m lotguard.executor --lot ING-001 --approve
~~~

The executor quarantines targeted synthetic shipments, creates audit relationships in FalkorDB, and verifies the resulting inventory state.

Run `seed.py` beforehand to reset the demonstration.

### End-to-end evaluation

Start the local dashboard and execute:

~~~bash
uv run --no-sync python tests/test_e2e.py
~~~

The six tests verify initial inventory, approval enforcement, protection against recall actions for clear lots, targeted quarantine, graph audit records, and repeated-action behavior.

### Agent capabilities and limitations

Sentinel uses a deterministic, graph-driven tool workflow. It does not currently use an LLM, MCP-based tool selection, or open-ended autonomous reasoning.

Its evidence bundles provide auditable graph paths rather than formal proofs. All demonstrations use synthetic data.
