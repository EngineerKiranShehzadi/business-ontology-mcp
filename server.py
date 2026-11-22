"""MCP stdio server for ontology discovery and graph fact queries.

Environment variables select the source adapter and graph store at startup.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from ontology.adapter import OntologyAdapter
from ontology.csv_adapter import CSVAdapter
from ontology.document_adapter import DocumentAdapter
from ontology.http_adapter import HTTPJSONAdapter
from ontology.postgres_adapter import PostgreSQLAdapter
from ontology.reasoning import SemanticReasoner
from ontology.store import Neo4jGraphStore, NetworkXGraphStore


PROJECT_DIR = Path(__file__).parent
ONTOLOGY_PATH = PROJECT_DIR / "ontology" / "ontology_definition.json"
load_dotenv(PROJECT_DIR / ".env")

ontology = OntologyAdapter(ONTOLOGY_PATH).load()
source_status = {
    "adapter": "json",
    "connected": True,
    "message": "Ontology loaded from the local JSON definition",
}

source_adapter_name = os.getenv("SOURCE_ADAPTER", "json").lower()

if source_adapter_name == "postgresql":
    try:
        ontology = PostgreSQLAdapter(os.getenv("POSTGRES_DSN", "")).load(ontology)
        source_status = {
            "adapter": "postgresql",
            "connected": True,
            "message": "Ontology facts loaded from PostgreSQL",
        }
    except Exception as exc:
        source_status = {
            "adapter": "postgresql",
            "connected": False,
            "message": f"PostgreSQL unavailable; kept JSON facts: {exc}",
        }
elif source_adapter_name == "csv":
    try:
        ontology = CSVAdapter(os.getenv("CSV_SOURCE_DIR", str(PROJECT_DIR / "data" / "csv"))).load(ontology)
        source_status = {"adapter": "csv", "connected": True, "message": "Ontology facts loaded from CSV files"}
    except Exception as exc:
        source_status = {"adapter": "csv", "connected": False, "message": f"CSV unavailable; kept JSON facts: {exc}"}
elif source_adapter_name == "http":
    try:
        timeout = float(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))
        ontology = HTTPJSONAdapter(os.getenv("HTTP_JSON_URL", ""), timeout).load(ontology)
        source_status = {"adapter": "http", "connected": True, "message": "Ontology facts loaded from HTTP JSON API"}
    except Exception as exc:
        source_status = {"adapter": "http", "connected": False, "message": f"HTTP JSON API unavailable; kept JSON facts: {exc}"}
elif source_adapter_name == "document":
    document_adapter = DocumentAdapter(os.getenv("DOCUMENT_SOURCE_DIR", str(PROJECT_DIR / "documents")))
    try:
        ontology = document_adapter.load(ontology)
        source_status = {
            "adapter": "document",
            "connected": True,
            "message": "Ontology facts loaded from document sources",
            "mapping_report": document_adapter.mapping_report,
        }
    except Exception as exc:
        report = getattr(exc, "report", document_adapter.mapping_report)
        source_status = {
            "adapter": "document",
            "connected": False,
            "message": f"Documents could not be safely mapped; kept JSON facts: {exc}",
            "mapping_report": report,
        }
elif source_adapter_name == "json":
    source_status = {
        "adapter": "json",
        "connected": True,
        "message": "Ontology facts loaded from the local JSON definition",
    }
else:
    source_status = {
        "adapter": source_adapter_name,
        "connected": False,
        "message": "Unknown SOURCE_ADAPTER; kept JSON facts",
    }

# Startup order: load JSON ontology, select a source adapter, build fallback graph,
# optionally connect to Neo4j, then expose MCP resources and tools.

fallback_store = NetworkXGraphStore(ontology)
store = fallback_store
graph_status = fallback_store.status()

if os.getenv("GRAPH_STORE", "networkx").lower() == "neo4j":
    try:
        neo4j_store = Neo4jGraphStore(
            ontology=ontology,
            uri=os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
            username=os.getenv("NEO4J_USER", "neo4j"),
            password=os.getenv("NEO4J_PASSWORD", ""),
        )
        neo4j_store.sync(ontology)
        store = neo4j_store
        graph_status = store.status()
    except Exception as exc:
        graph_status = {
            "backend": "networkx",
            "persistent": False,
            "connected": True,
            "fallback": True,
            "message": f"Neo4j unavailable; using NetworkX: {exc}",
            "node_count": fallback_store.status()["node_count"],
            "edge_count": fallback_store.status()["edge_count"],
        }

mcp = FastMCP(
    "Business Ontology MCP",
    instructions="Discover the ontology, inspect sources and graph status, and query Customer, Order, and Product facts.",
)


@mcp.resource("ontology://definition")
def ontology_resource() -> str:
    """Return the validated ontology definition as JSON."""
    return json.dumps(store.concepts(), indent=2)


@mcp.resource("graph://status")
def graph_status_resource() -> str:
    """Return source and graph-store status."""
    return json.dumps(
        {
            "source": source_status,
            "graph_store": graph_status,
        },
        indent=2,
    )


@mcp.tool()
def list_concepts() -> dict:
    """List ontology concepts and relationships."""
    return store.concepts()


@mcp.tool()
def get_concept(concept_name: str) -> dict:
    """Get one entity or relationship definition by name."""
    result = store.concept(concept_name)
    if result is None:
        return {"error": f"Concept not found: {concept_name}"}
    return result


@mcp.tool()
def query_facts(entity_id: str | None = None, relationship: str | None = None) -> dict:
    """Query graph facts by entity ID or relationship name."""
    return {
        "filters": {"entity_id": entity_id, "relationship": relationship},
        "facts": store.query_facts(entity_id=entity_id, relationship=relationship),
    }


@mcp.tool()
def get_neighbors(entity_id: str) -> dict:
    """Get outgoing and incoming graph relationships for an entity."""
    return store.neighbors(entity_id)


@mcp.tool()
def get_system_status() -> dict:
    """Get source adapter and graph-store status."""
    return {
        "source": source_status,
        "graph_store": store.status(),
    }


@mcp.tool()
def sync_graph() -> dict:
    """Synchronize the active ontology facts into Neo4j when it is the selected backend."""
    if isinstance(store, Neo4jGraphStore):
        store.sync(ontology)
        graph_status = store.status()
        return {"synchronized": True, "graph_store": graph_status}
    return {
        "synchronized": False,
        "graph_store": store.status(),
        "message": "Synchronization is only applicable to the Neo4j backend",
    }


reasoner = SemanticReasoner(ontology)


@mcp.resource("semantic://reasoning")
def semantic_reasoning_resource() -> str:
    """Return all facts derived by the semantic reasoning rule."""
    return json.dumps(reasoner.derive_facts(), indent=2)


@mcp.tool()
def derive_facts(entity_id: str | None = None) -> dict:
    """Derive Customer PURCHASES Product facts from PLACES and CONTAINS relationships."""
    return reasoner.derive_facts(entity_id=entity_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
