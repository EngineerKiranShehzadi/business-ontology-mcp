"""MCP stdio server for ontology discovery and graph fact queries.

The ontology definition drives everything: entity types, relationships, reasoning rules
and the source mapping. Environment variables select the source adapter and graph store,
and facts can be reloaded from the source while the server runs (``reload_data`` tool or
``REFRESH_INTERVAL_SECONDS``).
"""

import json
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from runtime import OntologyRuntime


PROJECT_DIR = Path(__file__).parent
load_dotenv(PROJECT_DIR / ".env")

# Startup order: load JSON ontology, select a source adapter, build fallback graph,
# optionally connect to Neo4j, then expose MCP resources and tools.
runtime = OntologyRuntime(PROJECT_DIR)

mcp = FastMCP(
    "Business Ontology MCP",
    instructions=(
        "Discover the ontology (list_concepts), browse entities (list_entities), "
        "query graph facts, derive new facts with the ontology's rules, and call "
        "reload_data to pick up changes in the data source."
    ),
)


@mcp.resource("ontology://definition")
def ontology_resource() -> str:
    """Return the validated ontology definition as JSON."""
    return json.dumps(runtime.store.concepts(), indent=2)


@mcp.resource("graph://status")
def graph_status_resource() -> str:
    """Return source and graph-store status."""
    return json.dumps(runtime.status_summary(), indent=2)


@mcp.resource("semantic://reasoning")
def semantic_reasoning_resource() -> str:
    """Return all facts derived by the ontology's reasoning rules."""
    return json.dumps(runtime.reasoner.derive_all(), indent=2)


@mcp.tool()
def list_concepts() -> dict:
    """List ontology concepts, relationships and reasoning rules."""
    return runtime.store.concepts()


@mcp.tool()
def get_concept(concept_name: str) -> dict:
    """Get one entity, relationship or rule definition by name."""
    result = runtime.store.concept(concept_name)
    if result is None:
        return {"error": f"Concept not found: {concept_name}"}
    return result


@mcp.tool()
def list_entities(entity_type: str | None = None, limit: int = 100) -> dict:
    """List entity instances, optionally only of one entity type (see list_concepts)."""
    entities = runtime.store.entities(entity_type=entity_type, limit=limit)
    return {"entity_type": entity_type, "count": len(entities), "entities": entities}


@mcp.tool()
def query_facts(entity_id: str | None = None, relationship: str | None = None) -> dict:
    """Query graph facts by entity ID or relationship name."""
    return {
        "filters": {"entity_id": entity_id, "relationship": relationship},
        "facts": runtime.store.query_facts(entity_id=entity_id, relationship=relationship),
    }


@mcp.tool()
def get_neighbors(entity_id: str) -> dict:
    """Get outgoing and incoming graph relationships for an entity."""
    return runtime.store.neighbors(entity_id)


@mcp.tool()
def get_system_status() -> dict:
    """Get source adapter, graph-store and fact-count status."""
    return runtime.status_summary()


@mcp.tool()
def reload_data() -> dict:
    """Re-read the ontology definition and data source, then rebuild the graph."""
    return runtime.reload()


@mcp.tool()
def sync_graph() -> dict:
    """Synchronize the active ontology facts into Neo4j when it is the selected backend."""
    return runtime.sync_graph()


@mcp.tool()
def derive_facts(entity_id: str | None = None, rule: str | None = None) -> dict:
    """Derive facts with an ontology rule (default: the first rule) for all entities or one entity ID."""
    return runtime.reasoner.derive_facts(entity_id=entity_id, rule=rule)


if __name__ == "__main__":
    mcp.run(transport="stdio")
