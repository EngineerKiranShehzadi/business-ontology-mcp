"""Graph-store abstraction with NetworkX fallback and Neo4j persistence.

The MCP server uses the same interface for both implementations.
"""

from abc import ABC, abstractmethod
from typing import Iterable

from neo4j import GraphDatabase

from .adapter import OntologyDefinition
from .graph import OntologyGraph


class GraphStore(ABC):
    """Common interface used by the MCP server."""

    @abstractmethod
    def concepts(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def concept(self, concept_name: str) -> dict | None:
        raise NotImplementedError

    @abstractmethod
    def query_facts(self, entity_id: str | None = None, relationship: str | None = None) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def neighbors(self, entity_id: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def status(self) -> dict:
        raise NotImplementedError


class NetworkXGraphStore(GraphStore):
    """In-memory fallback graph backed by NetworkX."""

    def __init__(self, ontology: OntologyDefinition):
        self.ontology = ontology
        self.graph = OntologyGraph(ontology)

    def concepts(self) -> dict:
        return self.graph.concepts()

    def concept(self, concept_name: str) -> dict | None:
        return self.graph.concept(concept_name)

    def query_facts(self, entity_id: str | None = None, relationship: str | None = None) -> list[dict]:
        return self.graph.query_facts(entity_id=entity_id, relationship=relationship)

    def neighbors(self, entity_id: str) -> dict:
        return self.graph.neighbors(entity_id)

    def status(self) -> dict:
        return {
            "backend": "networkx",
            "persistent": False,
            "connected": True,
            "node_count": self.graph.graph.number_of_nodes(),
            "edge_count": self.graph.graph.number_of_edges(),
        }


class Neo4jGraphStore(GraphStore):
    """Persistent graph store backed by Neo4j."""

    def __init__(
        self,
        ontology: OntologyDefinition,
        uri: str,
        username: str,
        password: str,
    ):
        self.ontology = ontology
        self.fallback = OntologyGraph(ontology)
        self.driver = GraphDatabase.driver(uri, auth=(username, password))

    def concepts(self) -> dict:
        return self.fallback.concepts()

    def concept(self, concept_name: str) -> dict | None:
        return self.fallback.concept(concept_name)

    def sync(self, ontology: OntologyDefinition) -> None:
        graph = OntologyGraph(ontology)
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

            for node_id, attributes in graph.graph.nodes(data=True):
                label = attributes["entity_type"]
                if label not in {"Customer", "Order", "Product"}:
                    raise ValueError(f"Unknown Neo4j label: {label}")
                properties = dict(attributes)
                properties["entity_id"] = node_id
                session.run(
                    f"MERGE (n:{label} {{entity_id: $entity_id}}) SET n += $properties",
                    entity_id=node_id,
                    properties=properties,
                )

            for source, target, attributes in graph.graph.edges(data=True):
                relationship = attributes["relationship"]
                if relationship not in {"PLACES", "CONTAINS"}:
                    raise ValueError(f"Unknown relationship for Neo4j: {relationship}")
                session.run(
                    f"MATCH (a {{entity_id: $source}}) "
                    f"MATCH (b {{entity_id: $target}}) "
                    f"MERGE (a)-[r:{relationship}]->(b)",
                    source=source,
                    target=target,
                )

    def query_facts(self, entity_id: str | None = None, relationship: str | None = None) -> list[dict]:
        cypher = """
            MATCH (source)-[r]->(target)
            WHERE ($entity_id IS NULL OR source.entity_id = $entity_id OR target.entity_id = $entity_id)
              AND ($relationship IS NULL OR type(r) = toUpper($relationship))
            RETURN source, r, target
        """
        facts = []
        with self.driver.session() as session:
            result = session.run(
                cypher,
                entity_id=entity_id,
                relationship=relationship,
            )
            for record in result:
                source = record["source"]
                target = record["target"]
                relationship_name = record["r"] .type
                facts.append(
                    {
                        "source": source["entity_id"],
                        "relationship": relationship_name,
                        "target": target["entity_id"],
                        "source_attributes": dict(source),
                        "target_attributes": dict(target),
                    }
                )
        return facts

    def neighbors(self, entity_id: str) -> dict:
        cypher = """
            MATCH (node {entity_id: $entity_id})
            OPTIONAL MATCH (node)-[outgoing]->(target)
            OPTIONAL MATCH (source)-[incoming]->(node)
            RETURN node,
                   collect(DISTINCT {relationship: type(outgoing), target: target.entity_id}) AS outgoing,
                   collect(DISTINCT {relationship: type(incoming), source: source.entity_id}) AS incoming
        """
        with self.driver.session() as session:
            record = session.run(cypher, entity_id=entity_id).single()

        if record is None:
            return {"entity_id": entity_id, "outgoing": [], "incoming": []}

        node = record["node"]
        outgoing = [item for item in record["outgoing"] if item["target"] is not None]
        incoming = [item for item in record["incoming"] if item["source"] is not None]
        return {
            "entity_id": entity_id,
            "attributes": dict(node),
            "outgoing": outgoing,
            "incoming": incoming,
        }

    def status(self) -> dict:
        try:
            with self.driver.session() as session:
                node_count = session.run("MATCH (n) RETURN count(n) AS count").single()["count"]
                edge_count = session.run("MATCH ()-[r]->() RETURN count(r) AS count").single()["count"]
            connected = True
        except Exception:
            node_count = 0
            edge_count = 0
            connected = False

        return {
            "backend": "neo4j",
            "persistent": True,
            "connected": connected,
            "node_count": node_count,
            "edge_count": edge_count,
        }

    def close(self) -> None:
        self.driver.close()

# NetworkX and Neo4j implement the same methods so server tools do not depend on one backend.
# Neo4j sync rebuilds graph nodes and relationships from the validated ontology facts.
