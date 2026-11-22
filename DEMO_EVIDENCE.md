# MCP Demo Evidence

Date: 2026-11-22

## Environment

- PostgreSQL: Docker service, host port `5433`, database `business_ontology`
- Neo4j: Docker service, host port `7687`
- MCP transport: stdio
- Open-source client: MCP Inspector CLI
- Source adapter: PostgreSQL
- Graph store: Neo4j

## MCP Inspector connection

The following documented stdio command was used:

```powershell
npx -y @modelcontextprotocol/inspector --cli --transport stdio --method tools/list --cwd "d:\ai-development-assignment\business-ontology-mcp" "d:\ai-development-assignment\business-ontology-mcp\.venv\Scripts\python.exe" "d:\ai-development-assignment\business-ontology-mcp\server.py"
```

Inspector successfully listed:

- `list_concepts`
- `get_concept`
- `query_facts`
- `get_neighbors`
- `get_system_status`
- `sync_graph`
- `derive_facts`

## Successful graph query

Inspector CLI call:

```powershell
npx -y @modelcontextprotocol/inspector --cli --transport stdio --method tools/call --tool-name query_facts --tool-arg entity_id=O100 relationship=CONTAINS --cwd "d:\ai-development-assignment\business-ontology-mcp" "d:\ai-development-assignment\business-ontology-mcp\.venv\Scripts\python.exe" "d:\ai-development-assignment\business-ontology-mcp\server.py"
```

Result:

```json
{
  "filters": {
    "entity_id": "O100",
    "relationship": "CONTAINS"
  },
  "facts": [
    {"source": "O100", "relationship": "CONTAINS", "target": "P100"},
    {"source": "O100", "relationship": "CONTAINS", "target": "P200"}
  ],
  "isError": false
}
```

Therefore, MCP Inspector verified that order `O100` contains products `P100` and `P200` in the PostgreSQL/Neo4j flow.

## Backend status

The `get_system_status` tool and `graph://status` resource returned:

```json
{
  "source": {
    "adapter": "postgresql",
    "connected": true,
    "message": "Ontology facts loaded from PostgreSQL"
  },
  "graph_store": {
    "backend": "neo4j",
    "persistent": true,
    "connected": true,
    "node_count": 6,
    "edge_count": 5
  }
}
```

## Semantic reasoning evidence

The MCP server also exposes `semantic://reasoning` and `derive_facts`. A focused smoke check returned 2 derived `PURCHASES` facts for `C001`:

- `C001 -> PURCHASES -> P100`, through `O100`
- `C001 -> PURCHASES -> P200`, through `O100`

This is deterministic relationship composition using `PLACES` and `CONTAINS`; it is not an RDF/OWL reasoner.

## NetworkX fallback

NetworkX fallback code was preserved. It was not replaced or removed. The Neo4j path was used for this demo.

## Clean-checkout evidence

A temporary clean copy excluded `.git`, `.venv`, `.env`, caches, and generated Python files. A fresh Python 3.14 virtual environment installed `requirements.txt` successfully.

Verified clean-copy commands and results:

- `python -m pytest` -> `19 passed`
- MCP server module startup -> 7 tools and 3 resources
- `query_facts(O100, CONTAINS)` -> `P100` and `P200`
- `derive_facts(C001)` -> 2 derived PURCHASES facts

## Additional source smoke checks

- CSV source -> 2 customers, 2 orders, 2 products, 5 edges
- Structured document source -> 2 customers, 2 orders, 2 products, 5 edges
- Live local HTTP JSON endpoint -> 2 products and 3 edges
- TXT, Markdown, PDF, and DOCX extraction -> covered by focused tests

## PostgreSQL and Neo4j evidence

Docker Compose showed both containers running:

- `postgres-1` mapped host port `5433`
- `neo4j-1` mapped host ports `7474` and `7687`

The clean-copy PostgreSQL/Neo4j server smoke returned 6 nodes, 5 relationships, P100/P200 for O100, and 2 derived purchases for C001.
