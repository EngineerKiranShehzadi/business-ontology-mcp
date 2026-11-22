"""Small deterministic semantic reasoning layer.

It composes Customer -> PLACES -> Order -> CONTAINS -> Product
into a derived Customer -> PURCHASES -> Product fact.
"""

from .adapter import OntologyDefinition
from .graph import OntologyGraph


class SemanticReasoner:
    """Derives useful facts through explicit ontology relationship composition."""

    def __init__(self, ontology: OntologyDefinition):
        self.ontology = ontology
        self.graph = OntologyGraph(ontology)

    def derive_facts(self, entity_id: str | None = None) -> dict:
        derived = {}

        # Start at Customer nodes, follow PLACES to Orders, then follow CONTAINS to Products.
        # The derived PURCHASES edge is created only from those existing relationships.
        for customer_id in self.graph.graph.nodes:
            if self.graph.graph.nodes[customer_id].get("entity_type") != "Customer":
                continue
            if entity_id and entity_id not in (customer_id,):
                continue

            for _, order_id, places in self.graph.graph.out_edges(customer_id, data=True):
                if places.get("relationship") != "PLACES":
                    continue

                for _, product_id, contains in self.graph.graph.out_edges(order_id, data=True):
                    if contains.get("relationship") != "CONTAINS":
                        continue

                    key = (customer_id, product_id)
                    if key not in derived:
                        derived[key] = {
                            "source": customer_id,
                            "relationship": "PURCHASES",
                            "target": product_id,
                            "via_orders": [],
                        }
                    derived[key]["via_orders"].append(order_id)

        facts = []
        for fact in derived.values():
            fact["via_orders"] = sorted(set(fact["via_orders"]))
            customer_id = fact["source"]
            product_id = fact["target"]
            facts.append(
                {
                    **fact,
                    "source_attributes": self.graph.graph.nodes[customer_id],
                    "target_attributes": self.graph.graph.nodes[product_id],
                }
            )

        return {
            "rule": "Customer PLACES Order CONTAINS Product => Customer PURCHASES Product",
            "filter": entity_id,
            "base_relationships": ["PLACES", "CONTAINS"],
            "derived_relationship": "PURCHASES",
            "fact_count": len(facts),
            "facts": facts,
        }
