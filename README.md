# Business Ontology MCP Server

A small business ontology project for the `Customer`, `Order`, and `Product` domain.

It provides:

- A validated JSON ontology definition
- PostgreSQL, CSV, HTTP JSON, and JSON/JSONL/TXT/MD/PDF/DOCX document source adapters
- A shared `OntologySource` interface
- A small semantic reasoning engine
- An in-memory NetworkX graph fallback
- An optional Neo4j persistent graph store
- A stdio MCP server for open-source MCP clients

RDF/OWL export is deferred. A public GitHub release has not happened yet.

## Implemented architecture

```text
Sources
  -> shared OntologySource interface
       -> JSON ontology
       -> PostgreSQL adapter
       -> CSV adapter
       -> HTTP JSON API adapter
       -> JSON/JSONL document adapter
  -> ontology facts + Pydantic validation
  -> GraphStore
       -> Neo4jGraphStore (optional persistent backend)
       -> NetworkXGraphStore (fallback backend)
  -> SemanticReasoner
  -> MCP stdio server
  -> MCP tools and resources
```

The JSON ontology remains the lightweight format. Pydantic validates the ontology structure and every source payload. Shared source validation also rejects unknown relationships and dangling entity references. NetworkX remains usable if PostgreSQL or Neo4j is unavailable.

## Project files

- `ontology/ontology_definition.json` — entities, properties, relationships, and demo facts
- `ontology/adapter.py` — JSON loading and Pydantic validation
- `ontology/postgres_adapter.py` — PostgreSQL source adapter
- `ontology/graph.py` — in-memory NetworkX directed graph
- `ontology/store.py` — graph-store interface, NetworkX fallback, and Neo4j store
- `ontology/sources.py` — shared source-adapter interface and payload validation
- `ontology/csv_adapter.py` — CSV source adapter
- `ontology/http_adapter.py` — HTTP JSON API source adapter
- `ontology/document_adapter.py` — JSON/JSONL/TXT/MD/PDF/DOCX document source adapter
- `ontology/reasoning.py` — relationship-composition semantic reasoning
- `database/init.sql` — PostgreSQL demo schema and seed data
- `docker-compose.yml` — local PostgreSQL and Neo4j services
- `server.py` — MCP stdio server
- `tests/test_ontology.py` — ontology, source, graph, and reasoning tests
- `.env.example` — environment-variable names and local defaults
- `.gitignore` — excludes `.venv`, `.env`, caches, and local database directories
- `DEMO_EVIDENCE.md` — verified MCP Inspector evidence

## Domain model

Entities:

- `Customer`: `customer_id`, `name`, `status`
- `Order`: `order_id`, `customer_id`, `order_date`, `status`
- `Product`: `product_id`, `name`, `category`, `price`

Relationships:

- `Customer -[PLACES]-> Order`
- `Order -[CONTAINS]-> Product`

The demo facts include order `O100`, containing products `P100` and `P200`.

## Installation

Python 3.11 or newer is required by `pyproject.toml`. The setup was verified with Python 3.14 on Windows.

```powershell
cd d:\ai-development-assignment\business-ontology-mcp

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Dependencies declared in `pyproject.toml` and `requirements.txt`:

- `mcp>=1.26,<2`
- `networkx>=3.5`
- `pydantic>=2.12`
- `psycopg[binary]>=3.2`
- `neo4j>=5.28`
- `python-dotenv>=1.2`
- `pytest>=9` for tests
- `pypdf>=6.1` for PDF text extraction
- `python-docx>=1.2` for DOCX text extraction

## Docker setup

Docker Desktop must be running.

```powershell
docker compose up -d postgres neo4j
```

Verified service ports:

- PostgreSQL: `127.0.0.1:5433`, database `business_ontology`
- Neo4j Bolt: `127.0.0.1:7687`
- Neo4j Browser: `http://127.0.0.1:7474`

PostgreSQL uses host port `5433` because host port `5432` may be occupied by an existing PostgreSQL installation.

On a fresh PostgreSQL volume, `database/init.sql` creates the tables and inserts the demo facts.

> **Warning:** `docker compose down -v` permanently deletes the PostgreSQL and Neo4j Docker volume data. Do not run it unless you intentionally want to reset all local demo data.

To recreate and reseed the demo services:

```powershell
docker compose down -v
docker compose up -d postgres neo4j
```

Check service status:

```powershell
docker compose ps
```

## Environment variables

Copy `.env.example` to `.env` before configuring a source or persistent store.

### JSON and NetworkX fallback

```dotenv
SOURCE_ADAPTER=json
GRAPH_STORE=networkx
```

This is the default configuration and uses the JSON facts with the in-memory NetworkX graph.

### PostgreSQL with NetworkX fallback

```dotenv
SOURCE_ADAPTER=postgresql
POSTGRES_DSN=postgresql://ontology:ontology_dev_password@127.0.0.1:5433/business_ontology
GRAPH_STORE=networkx
```

### PostgreSQL with Neo4j

```dotenv
SOURCE_ADAPTER=postgresql
POSTGRES_DSN=postgresql://ontology:ontology_dev_password@127.0.0.1:5433/business_ontology
GRAPH_STORE=neo4j
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4j_dev_password
```

### CSV source

```dotenv
SOURCE_ADAPTER=csv
CSV_SOURCE_DIR=./data/csv
GRAPH_STORE=networkx
```

CSV mode reads `customers.csv`, `orders.csv`, `products.csv`, and `order_items.csv` from the configured directory. The included `data/csv/` directory contains matching demo data.

### HTTP JSON API source

```dotenv
SOURCE_ADAPTER=http
HTTP_JSON_URL=http://127.0.0.1:8080/ontology-facts
HTTP_TIMEOUT_SECONDS=10
GRAPH_STORE=networkx
```

The endpoint must return a JSON object containing `customers`, `orders`, `products`, and `edges`, or a wrapper object containing `sample_facts`. HTTP mode performs a read-only GET request.

### JSON, JSONL, TXT, MD, PDF, and DOCX documents

```dotenv
SOURCE_ADAPTER=document
DOCUMENT_SOURCE_DIR=./documents
GRAPH_STORE=networkx
```

Document mode supports:

- `.json` and `.jsonl` structured facts
- `.txt` and `.md` deterministic text facts
- `.pdf` text extraction through `pypdf`
- `.docx` paragraph and table extraction through `python-docx`

Free-text and PDF/DOCX lines use explicit, documented formats only:

```text
Customer C001: Avery Morgan | status=active
Order O100: customer=C001 | date=2026-11-20 | status=shipped
Product P100: Notebook | category=Office | price=12.50
C001 PLACES O100
O100 CONTAINS P100
```

Key/value variants using `=` or `:` are also recognized, for example `Customer customer_id=C001 name=Avery Morgan status=active`.

The adapter does not infer missing names, statuses, dates, prices, IDs, or relationships. A line containing ontology terms but not matching these rules is returned as unmapped content. Facts from free-text documents include filename, format, page number for PDF, and line or paragraph provenance. JSON and JSONL provenance includes filename and document/line position.

Structured and extracted facts pass through Pydantic plus shared referential validation. Unknown relationships and dangling Customer/Order/Product references are rejected consistently.

Additional source variables:

- `CSV_SOURCE_DIR`: directory containing the four demo CSV files
- `HTTP_JSON_URL`: HTTP JSON endpoint URL
- `HTTP_TIMEOUT_SECONDS`: HTTP request timeout
- `DOCUMENT_SOURCE_DIR`: directory containing `.json` or `.jsonl` documents

Docker Compose also accepts:

- `POSTGRES_PASSWORD`, default `ontology_dev_password`
- `NEO4J_PASSWORD`, default `neo4j_dev_password`

When overriding a password, keep PostgreSQL and Neo4j environment values consistent with their connection settings. Never commit `.env` or real secrets.

## NetworkX fallback behavior

The server always has a NetworkX store available.

- If PostgreSQL is unavailable, JSON facts remain in use and `source.connected` is reported as `false`.
- If Neo4j is unavailable or not selected, the server keeps the NetworkX store.
- When Neo4j initialization fails, `graph://status` includes `fallback: true` and an explanatory message.
- `get_system_status` reports the active graph store. In fallback mode, it reports the NetworkX backend.

The MCP tool and resource interface remains available in fallback mode.

## Tests

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Verified result:

```text
19 passed
```

The tests cover JSON ontology loading, NetworkX graph structure, customer neighbors, direct facts, CSV source loading, HTTP JSON source loading, JSON/JSONL document loading, shared source-interface behavior, referential consistency, and semantic reasoning.

## MCP server

Start the stdio server:

```powershell
.\.venv\Scripts\python.exe server.py
```

No HTTP or SSE MCP transport is implemented; stdio is the configured transport.

## MCP resources

- `ontology://definition` — ontology concepts and relationships
- `graph://status` — source adapter, graph backend, connection state, and node/edge counts
- `semantic://reasoning` — derived facts and reasoning-rule metadata

## MCP tools

- `list_concepts` — list ontology entities and relationships
- `get_concept` — retrieve an entity or relationship definition
- `query_facts` — filter graph facts by entity ID and relationship
- `get_neighbors` — list incoming and outgoing relationships for an entity
- `get_system_status` — report source and active graph-store status
- `sync_graph` — synchronize current facts into Neo4j when Neo4j is active
- `derive_facts` — derive Customer `PURCHASES` Product facts through the relationship chain Customer→PLACES→Order→CONTAINS→Product

## MCP Inspector demo

The verified open-source client is MCP Inspector CLI. The documented stdio connection is:

```powershell
npx -y @modelcontextprotocol/inspector --cli --transport stdio --method tools/list --cwd "d:\ai-development-assignment\business-ontology-mcp" "d:\ai-development-assignment\business-ontology-mcp\.venv\Scripts\python.exe" "d:\ai-development-assignment\business-ontology-mcp\server.py"
```

Inspector listed all seven tools.

### Verified O100 query

Call `query_facts` with:

```json
{
  "entity_id": "O100",
  "relationship": "CONTAINS"
}
```

Inspector CLI command:

```powershell
npx -y @modelcontextprotocol/inspector --cli --transport stdio --method tools/call --tool-name query_facts --tool-arg entity_id=O100 relationship=CONTAINS --cwd "d:\ai-development-assignment\business-ontology-mcp" "d:\ai-development-assignment\business-ontology-mcp\.venv\Scripts\python.exe" "d:\ai-development-assignment\business-ontology-mcp\server.py"
```

Verified result:

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

The demo used a connected PostgreSQL source, a connected Neo4j store, 6 nodes, and 5 relationships. It verified that order `O100` contains `P100` and `P200`. More output is recorded in `DEMO_EVIDENCE.md`.

### Verified reasoning result

Call `derive_facts` with:

```json
{"entity_id":"C001"}
```

It derives two `PURCHASES` facts: `C001 -> PURCHASES -> P100` and `C001 -> PURCHASES -> P200`, both through order `O100`. This is deterministic relationship composition, not RDF/OWL or a general inference engine.

## Limitations

- RDF/OWL export is deferred; JSON is the current lightweight ontology format.
- The public GitHub release has not happened yet.
- PostgreSQL and Neo4j require Docker or separately managed services.
- Neo4j synchronization rebuilds the graph by deleting existing nodes and relationships before inserting current ontology facts.

## Implemented versus limited features

Implemented:

- Shared source-adapter interface
- PostgreSQL, CSV, HTTP JSON, and JSON/JSONL document sources
- Pydantic validation for loaded source facts
- NetworkX graph construction and Neo4j persistence
- MCP tools and resources
- Deterministic two-hop semantic reasoning: `PLACES` plus `CONTAINS` derives `PURCHASES`

Limited or not implemented:

- HTTP source is read-only GET JSON and does not implement authentication or retries
- Free-text document extraction is deterministic and restricted to documented entity/relationship formats
- Scanned image-only PDFs are not supported because no OCR is included
- DOCX provenance uses paragraph/table position because DOCX has no page boundaries
- RDF/OWL export is deferred
- Reasoning is one explicit composition rule, not a general RDF/OWL reasoner or SPARQL engine
- Public GitHub release has not happened yet

## Included sample data

Sample data directories:

- `data/csv/` — sample CSV source files
- `documents/example.json` — sample structured document source

## Clean checkout validation

After cloning the repository, run these commands to validate the setup:

```powershell
cd d:\ai-development-assignment\business-ontology-mcp

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env

docker compose up -d postgres neo4j

.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -c "import asyncio, server; print([tool.name for tool in asyncio.run(server.mcp.list_tools())]); print([str(resource.uri) for resource in asyncio.run(server.mcp.list_resources())])"
```

Verified clean-copy result:

- Fresh environment installation succeeded
- `19 passed`
- 7 MCP tools discovered
- 3 MCP resources discovered
- JSON/NetworkX startup returned O100 facts and 2 C001 derived purchases

## Repository layout

```text
ontology-layer/
├── core/          # ontology models and validation exports
├── adapters/
│   ├── postgres/
│   ├── rest_api/
│   ├── csv/
│   └── documents/
├── graph/         # graph builder and store exports
├── semantic/      # relationship reasoning
├── mcp_server/    # stdio MCP entry point
├── examples/      # sample ontology definitions
└── tests/         # package-level test location
```

The existing root `ontology/` package and `server.py` remain the implementation source for compatibility. Files under `ontology-layer/` re-export and route to that implementation.
