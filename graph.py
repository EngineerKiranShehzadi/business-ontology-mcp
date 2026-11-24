"""Graph storage: an in-memory NetworkX graph plus an optional Neo4j store.

OntologyGraph builds the NetworkX graph from the facts. NetworkXGraphStore and
Neo4jGraphStore offer the same methods, so the server does not care which one is active.
"""

from abc import ABC, abstractmethod

import networkx as nx
from neo4j import GraphDatabase

from models import OntologyDefinition


class OntologyGraph:
    """Stores ontology concepts and facts in an in-memory graph."""

    def __init__(self, ontology: OntologyDefinition):
        self.ontology = ontology
        self.graph = nx.DiGraph()
        self._build()

    def _build(self) -> None:
        facts = self.ontology.facts

        for entity_name, rows in facts.entities.items():
            for row in rows:
                self.graph.add_node(
                    self.ontology.id_of(entity_name, row),
                    **row,
                    entity_type=entity_name,
                )

        for edge in facts.edges:
            self.graph.add_edge(
                edge.from_id,
                edge.to_id,
                relationship=edge.relationship,
            )

    def entities(self, entity_type: str | None = None, limit: int | None = None) -> list[dict]:
        """List entity nodes, optionally of one type (case-insensitive)."""
        wanted = entity_type.lower() if entity_type else None
        items = []
        for node_id, data in self.graph.nodes(data=True):
            if wanted and data["entity_type"].lower() != wanted:
                continue
            items.append({"entity_id": node_id, **data})
        return items[:limit] if limit is not None else items

    def concepts(self) -> dict:
        return {
            "ontology_name": self.ontology.ontology_name,
            "ontology_version": self.ontology.ontology_version,
            "entities": {
                name: definition.model_dump()
                for name, definition in self.ontology.entities.items()
            },
            "relationships": [
                item.model_dump(by_alias=True)
                for item in self.ontology.relationships
            ],
            "rules": [item.model_dump() for item in self.ontology.rules],
        }

    def concept(self, concept_name: str) -> dict | None:
        for name, definition in self.ontology.entities.items():
            if name.lower() == concept_name.lower():
                return {"name": name, **definition.model_dump()}

        for rule in self.ontology.rules:
            if rule.name.lower() == concept_name.lower():
                return {"kind": "rule", **rule.model_dump()}

        for relationship in self.ontology.relationships:
            if relationship.name.lower() == concept_name.lower():
                return relationship.model_dump(by_alias=True)
        return None

    def query_facts(self, entity_id: str | None = None, relationship: str | None = None) -> list[dict]:
        facts = []

        for source, target, data in self.graph.edges(data=True):
            edge_relationship = data["relationship"]
            if relationship and edge_relationship.lower() != relationship.lower():
                continue
            if entity_id and entity_id not in (source, target):
                continue

            facts.append({
                "source": source,
                "relationship": edge_relationship,
                "target": target,
                "source_attributes": self.graph.nodes[source],
                "target_attributes": self.graph.nodes[target],
            })

        return facts

    def neighbors(self, entity_id: str) -> dict:
        if entity_id not in self.graph:
            return {"entity_id": entity_id, "outgoing": [], "incoming": []}

        outgoing = [
            {"relationship": data["relationship"], "target": target}
            for _, target, data in self.graph.out_edges(entity_id, data=True)
        ]
        incoming = [
            {"relationship": data["relationship"], "source": source}
            for source, _, data in self.graph.in_edges(entity_id, data=True)
        ]
        return {
            "entity_id": entity_id,
            "attributes": self.graph.nodes[entity_id],
            "outgoing": outgoing,
            "incoming": incoming,
        }

# Every entity row becomes a uniquely identified NetworkX node tagged with its entity type.
# Edges carry the relationship name defined in the ontology.


class GraphStore(ABC):
    """Common interface used by the MCP server."""

    @abstractmethod
    def concepts(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def concept(self, concept_name: str) -> dict | None:
        raise NotImplementedError

    @abstractmethod
    def entities(self, entity_type: str | None = None, limit: int | None = None) -> list[dict]:
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

    def entities(self, entity_type: str | None = None, limit: int | None = None) -> list[dict]:
        return self.graph.entities(entity_type=entity_type, limit=limit)

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

    def entities(self, entity_type: str | None = None, limit: int | None = None) -> list[dict]:
        return self.fallback.entities(entity_type=entity_type, limit=limit)

    def sync(self, ontology: OntologyDefinition) -> None:
        graph = OntologyGraph(ontology)
        # Labels and relationship types are interpolated into Cypher, so they must come
        # from the ontology (whose names are validated as plain identifiers) and nowhere else.
        labels = set(ontology.entities)
        relationships = {item.name for item in ontology.relationships}
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

            for node_id, attributes in graph.graph.nodes(data=True):
                label = attributes["entity_type"]
                if label not in labels:
                    raise ValueError(f"Unknown Neo4j label: {label}")
                # Neo4j properties must be primitives, so nested provenance is not stored.
                properties = {key: value for key, value in attributes.items() if not isinstance(value, dict)}
                properties["entity_id"] = node_id
                session.run(
                    f"MERGE (n:{label} {{entity_id: $entity_id}}) SET n += $properties",
                    entity_id=node_id,
                    properties=properties,
                )

            for source, target, attributes in graph.graph.edges(data=True):
                relationship = attributes["relationship"]
                if relationship not in relationships:
                    raise ValueError(f"Unknown relationship for Neo4j: {relationship}")
                session.run(
                    f"MATCH (a {{entity_id: $source}}) "
                    f"MATCH (b {{entity_id: $target}}) "
                    f"MERGE (a)-[r:{relationship}]->(b)",
                    source=source,
                    target=target,
                )

        self.ontology = ontology
        self.fallback = graph

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
