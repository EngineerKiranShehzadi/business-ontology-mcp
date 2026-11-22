"""Build and query the in-memory NetworkX knowledge graph.

Nodes represent ontology entities. Directed edges represent relationships.
"""

import networkx as nx

from .adapter import OntologyDefinition


class OntologyGraph:
    """Stores ontology concepts and sample facts in an in-memory graph."""

    def __init__(self, ontology: OntologyDefinition):
        self.ontology = ontology
        self.graph = nx.DiGraph()
        self._build()

    def _build(self) -> None:
        facts = self.ontology.sample_facts

        for customer in facts.customers:
            self.graph.add_node(
                customer.customer_id,
                entity_type="Customer",
                **customer.model_dump(),
            )

        for order in facts.orders:
            self.graph.add_node(
                order.order_id,
                entity_type="Order",
                **order.model_dump(),
            )

        for product in facts.products:
            self.graph.add_node(
                product.product_id,
                entity_type="Product",
                **product.model_dump(),
            )

        for edge in facts.edges:
            self.graph.add_edge(
                edge.from_id,
                edge.to_id,
                relationship=edge.relationship,
            )

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
        }

    def concept(self, concept_name: str) -> dict | None:
        for name, definition in self.ontology.entities.items():
            if name.lower() == concept_name.lower():
                return {"name": name, **definition.model_dump()}

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

# Customer, Order, and Product rows become uniquely identified NetworkX nodes.
# Edges carry the relationship name, such as PLACES or CONTAINS.
