"""Ontology JSON models and validation rules.

Pydantic checks entity properties and facts before they enter the graph.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# Each Pydantic model defines one entity or one source-fact shape.
# Extra fields are rejected so source data stays aligned with the ontology.

class EntityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    properties: list[str]


class RelationshipDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    from_entity: str = Field(alias="from")
    to_entity: str = Field(alias="to")
    description: str


class Customer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str
    name: str
    status: str
    provenance: dict[str, str | int | None] | None = None


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    customer_id: str
    order_date: str
    status: str
    provenance: dict[str, str | int | None] | None = None


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str
    name: str
    category: str
    price: float
    provenance: dict[str, str | int | None] | None = None


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    relationship: str
    provenance: dict[str, str | int | None] | None = None


class SampleFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customers: list[Customer]
    orders: list[Order]
    products: list[Product]
    edges: list[Edge]


class OntologyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ontology_name: str
    ontology_version: str
    entities: dict[str, EntityDefinition]
    relationships: list[RelationshipDefinition]
    sample_facts: SampleFacts


class OntologyAdapter:
    """Reads an ontology JSON file and validates its structure."""

    def __init__(self, definition_path: str | Path):
        self.definition_path = Path(definition_path)

    def load(self) -> OntologyDefinition:
        if not self.definition_path.exists():
            raise FileNotFoundError(f"Ontology definition not found: {self.definition_path}")

        with self.definition_path.open("r", encoding="utf-8") as file:
            raw_data: dict[str, Any] = json.load(file)

        ontology = OntologyDefinition.model_validate(raw_data)
        self._validate_relationship_and_fact_consistency(ontology)
        return ontology

    def _validate_relationship_and_fact_consistency(self, ontology: OntologyDefinition) -> None:
        relationship_names = {item.name for item in ontology.relationships}

        for edge in ontology.sample_facts.edges:
            if edge.relationship not in relationship_names:
                raise ValueError(f"Unknown relationship: {edge.relationship}")

        customer_ids = {item.customer_id for item in ontology.sample_facts.customers}
        order_ids = {item.order_id for item in ontology.sample_facts.orders}
        product_ids = {item.product_id for item in ontology.sample_facts.products}

        for order in ontology.sample_facts.orders:
            if order.customer_id not in customer_ids:
                raise ValueError(f"Order {order.order_id} references unknown customer {order.customer_id}")

        for edge in ontology.sample_facts.edges:
            if edge.relationship == "PLACES":
                if edge.from_id not in customer_ids or edge.to_id not in order_ids:
                    raise ValueError("PLACES edge references unknown customer or order")
            elif edge.relationship == "CONTAINS":
                if edge.from_id not in order_ids or edge.to_id not in product_ids:
                    raise ValueError("CONTAINS edge references unknown order or product")
