"""Shared rules for every business ontology source adapter.

Each adapter receives the ontology and returns a validated ontology with source facts.
This keeps CSV, HTTP, document, and PostgreSQL code consistent.
"""

from abc import ABC, abstractmethod

from .adapter import Customer, Edge, OntologyDefinition, Order, Product, SampleFacts


class OntologySource(ABC):
    """Shared interface for ontology fact sources."""

    name: str

    @abstractmethod
    def load(self, ontology: OntologyDefinition) -> OntologyDefinition:
        raise NotImplementedError


def build_ontology_from_payload(ontology: OntologyDefinition, payload: dict) -> OntologyDefinition:
    """Validate a source payload and replace only its sample facts."""
    facts_payload = payload.get("sample_facts", payload)

    customers = [Customer.model_validate(row) for row in facts_payload.get("customers", [])]
    orders = [Order.model_validate(row) for row in facts_payload.get("orders", [])]
    products = []
    for row in facts_payload.get("products", []):
        normalized = dict(row)
        normalized["price"] = float(normalized["price"])
        products.append(Product.model_validate(normalized))

    edges = [Edge.model_validate(row) for row in facts_payload.get("edges", [])]

    facts = SampleFacts.model_validate(
        {
            "customers": [item.model_dump() for item in customers],
            "orders": [item.model_dump() for item in orders],
            "products": [item.model_dump() for item in products],
            "edges": [item.model_dump(by_alias=True) for item in edges],
        }
    )

    validate_sample_consistency(ontology, facts)

    return OntologyDefinition.model_validate(
        {
            "ontology_name": ontology.ontology_name,
            "ontology_version": ontology.ontology_version,
            "entities": ontology.entities,
            "relationships": ontology.relationships,
            "sample_facts": facts,
        }
    )


def validate_sample_consistency(ontology: OntologyDefinition, facts: SampleFacts) -> None:
    relationship_names = {item.name for item in ontology.relationships}
    customer_ids = {item.customer_id for item in facts.customers}
    order_ids = {item.order_id for item in facts.orders}
    product_ids = {item.product_id for item in facts.products}

    for order in facts.orders:
        if order.customer_id not in customer_ids:
            raise ValueError(
                f"Order {order.order_id} references unknown customer {order.customer_id}"
            )

    for edge in facts.edges:
        if edge.relationship not in relationship_names:
            raise ValueError(f"Unknown relationship: {edge.relationship}")

        if edge.relationship == "PLACES":
            if edge.from_id not in customer_ids or edge.to_id not in order_ids:
                raise ValueError("PLACES edge references unknown customer or order")
        elif edge.relationship == "CONTAINS":
            if edge.from_id not in order_ids or edge.to_id not in product_ids:
                raise ValueError("CONTAINS edge references unknown order or product")

# Every source payload is normalized here before graph construction.
# IDs and relationship names must match the ontology to pass validation.
