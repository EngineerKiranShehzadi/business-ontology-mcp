"""Ontology JSON models and validation rules.

The ontology definition is the single source of truth. Entity types, properties,
property types, identifiers, relationships and reasoning rules are all read from
the definition, so the same code serves any domain, not only Customer/Order/Product.

Pydantic validates the definition itself and, through row models generated from
the definition, every fact that enters the graph.
"""

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    create_model,
    model_validator,
)

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REF_PREFIX = "ref:"
SCALAR_TYPES = {"string", "integer", "number", "boolean", "date", "datetime"}
RESERVED_FIELDS = {"provenance"}


def _iso_date(value: str) -> str:
    date.fromisoformat(value)
    return value


def _iso_datetime(value: str) -> str:
    datetime.fromisoformat(value)
    return value


_PYTHON_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "date": Annotated[str, AfterValidator(_iso_date)],
    "datetime": Annotated[str, AfterValidator(_iso_datetime)],
}


def default_collection(entity_name: str) -> str:
    """Plural payload key for an entity, e.g. Customer -> customers."""
    return entity_name.lower() + "s"


def default_id_field(entity_name: str) -> str:
    """Conventional identifier column, e.g. OrderLine -> order_line_id."""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", entity_name).lower()
    return f"{snake}_id"


class EntityDefinition(BaseModel):
    """One entity type.

    ``properties`` maps property name to a type: string, integer, number, boolean,
    date, datetime, or ``ref:<Entity>`` for a foreign key. A plain list of names is
    accepted and means every property is a string.
    """

    model_config = ConfigDict(extra="forbid")
    description: str
    properties: dict[str, str]
    id_field: str | None = None
    collection: str | None = None  # key used in JSON payloads (default: lowercase plural)
    table: str | None = None  # table / CSV file name for tabular sources (default: collection)
    label_field: str | None = None  # property filled by a bare value in free-text documents

    @model_validator(mode="before")
    @classmethod
    def _properties_from_list(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("properties"), list):
            data = {**data, "properties": {name: "string" for name in data["properties"]}}
        return data

    def refs(self) -> dict[str, str]:
        """Property name -> referenced entity name, for ref: properties."""
        return {
            name: spec[len(REF_PREFIX):]
            for name, spec in self.properties.items()
            if spec.startswith(REF_PREFIX)
        }


class RelationshipSource(BaseModel):
    """Where a tabular source finds the edges of a relationship: a join table."""

    model_config = ConfigDict(extra="forbid")
    table: str
    from_column: str
    to_column: str


class RelationshipDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    from_entity: str = Field(alias="from")
    to_entity: str = Field(alias="to")
    description: str
    source: RelationshipSource | None = None


class RuleDefinition(BaseModel):
    """A derivation rule: following ``path`` yields a derived ``name`` relationship."""

    model_config = ConfigDict(extra="forbid")
    name: str
    path: list[str] = Field(min_length=2)
    description: str = ""


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    relationship: str
    provenance: dict[str, str | int | None] | None = None


class Facts(BaseModel):
    """Validated facts: entity rows grouped by entity type, plus edges."""

    model_config = ConfigDict(extra="forbid")
    entities: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)

    def rows(self, entity_name: str) -> list[dict[str, Any]]:
        return self.entities.get(entity_name, [])

    def counts(self) -> dict[str, int]:
        return {**{name: len(rows) for name, rows in self.entities.items()}, "edges": len(self.edges)}


class OntologyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ontology_name: str
    ontology_version: str
    entities: dict[str, EntityDefinition]
    relationships: list[RelationshipDefinition]
    rules: list[RuleDefinition] = Field(default_factory=list)
    facts: Facts = Field(default_factory=Facts)

    _row_models: dict[str, type[BaseModel]] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _resolve_and_check_schema(self) -> "OntologyDefinition":
        collections: dict[str, str] = {}
        for name, entity in self.entities.items():
            if not IDENTIFIER.match(name):
                raise ValueError(f"Invalid entity name: {name!r}")
            entity.id_field = entity.id_field or default_id_field(name)
            entity.collection = entity.collection or default_collection(name)
            entity.table = entity.table or entity.collection
            if entity.id_field not in entity.properties:
                raise ValueError(f"Entity {name} must define its id_field {entity.id_field!r} as a property")
            if entity.label_field is None:
                # Never guess beyond conventional display properties; set label_field to override.
                entity.label_field = next((p for p in ("name", "title", "label") if p in entity.properties), None)
            if entity.label_field and entity.label_field not in entity.properties:
                raise ValueError(f"Entity {name} label_field {entity.label_field!r} is not a property")
            if entity.collection in collections:
                raise ValueError(f"Entities {collections[entity.collection]} and {name} share collection {entity.collection!r}")
            collections[entity.collection] = name

            for prop, spec in entity.properties.items():
                if prop in RESERVED_FIELDS:
                    raise ValueError(f"Entity {name} cannot use reserved property {prop!r}")
                if spec in SCALAR_TYPES:
                    continue
                if spec.startswith(REF_PREFIX) and spec[len(REF_PREFIX):] in self.entities:
                    continue
                raise ValueError(f"Entity {name} property {prop!r} has unsupported type {spec!r}")

        relationships: dict[str, RelationshipDefinition] = {}
        for rel in self.relationships:
            if not IDENTIFIER.match(rel.name):
                raise ValueError(f"Invalid relationship name: {rel.name!r}")
            if rel.name in relationships:
                raise ValueError(f"Duplicate relationship: {rel.name}")
            for endpoint in (rel.from_entity, rel.to_entity):
                if endpoint not in self.entities:
                    raise ValueError(f"Relationship {rel.name} references unknown entity {endpoint}")
            relationships[rel.name] = rel

        for rule in self.rules:
            if not IDENTIFIER.match(rule.name):
                raise ValueError(f"Invalid rule name: {rule.name!r}")
            for step in rule.path:
                if step not in relationships:
                    raise ValueError(f"Rule {rule.name} references unknown relationship {step}")
            for current, following in zip(rule.path, rule.path[1:]):
                if relationships[current].to_entity != relationships[following].from_entity:
                    raise ValueError(f"Rule {rule.name}: {current} does not connect to {following}")
        return self

    # ---- schema helpers -------------------------------------------------

    def relationship(self, name: str) -> RelationshipDefinition | None:
        return next((item for item in self.relationships if item.name == name), None)

    def collection_map(self) -> dict[str, str]:
        """Payload collection key -> entity name."""
        return {entity.collection: name for name, entity in self.entities.items()}

    def id_of(self, entity_name: str, row: dict[str, Any]) -> str:
        return str(row[self.entities[entity_name].id_field])

    def row_model(self, entity_name: str) -> type[BaseModel]:
        """Pydantic model generated from an entity definition (cached)."""
        if entity_name not in self._row_models:
            definition = self.entities[entity_name]
            fields: dict[str, Any] = {
                prop: (_PYTHON_TYPES.get(spec, str), ...) for prop, spec in definition.properties.items()
            }
            fields["provenance"] = (dict[str, str | int | None] | None, None)
            self._row_models[entity_name] = create_model(
                entity_name, __config__=ConfigDict(extra="forbid"), **fields
            )
        return self._row_models[entity_name]

    # ---- facts ----------------------------------------------------------

    def normalize_payload(self, payload: dict, strict: bool = False) -> dict:
        """Turn any supported payload shape into ``{"entities": {...}, "edges": [...]}``.

        Accepted shapes: a ``facts`` / ``sample_facts`` wrapper, an ``entities`` object
        keyed by entity name or collection, and flat collection keys such as ``customers``.
        """
        payload = payload.get("facts", payload.get("sample_facts", payload))
        by_collection = self.collection_map()
        entities: dict[str, list] = {}

        def add(key: str, rows: Any) -> None:
            name = key if key in self.entities else by_collection.get(key)
            if name is None:
                if strict:
                    raise ValueError(f"Unknown entity collection in facts: {key}")
                return
            entities.setdefault(name, []).extend(rows)

        for key, rows in (payload.get("entities") or {}).items():
            add(key, rows)
        for key, rows in payload.items():
            if key not in {"entities", "edges"}:
                add(key, rows)
        return {"entities": entities, "edges": list(payload.get("edges", []))}

    def with_facts(self, payload: dict, strict: bool = False) -> "OntologyDefinition":
        """Return a copy of this ontology holding the validated facts in ``payload``."""
        normalized = self.normalize_payload(payload, strict=strict)
        entities: dict[str, list[dict[str, Any]]] = {}
        for name, rows in normalized["entities"].items():
            model = self.row_model(name)
            entities[name] = [model.model_validate(row).model_dump(exclude_none=False) for row in rows]
            for row in entities[name]:
                if row.get("provenance") is None:
                    row.pop("provenance")
        facts = Facts.model_validate({"entities": entities, "edges": normalized["edges"]})
        self.validate_facts(facts)
        copy = self.model_copy(update={"facts": facts})
        copy._row_models = self._row_models
        return copy

    def validate_facts(self, facts: Facts) -> None:
        """Check unique ids, references and edge endpoints against the schema."""
        ids: dict[str, str] = {}
        for name, rows in facts.entities.items():
            for row in rows:
                entity_id = self.id_of(name, row)
                if entity_id in ids:
                    raise ValueError(f"Duplicate id {entity_id} ({ids[entity_id]} and {name})")
                ids[entity_id] = name

        for name, rows in facts.entities.items():
            for prop, target in self.entities[name].refs().items():
                for row in rows:
                    if ids.get(str(row[prop])) != target:
                        raise ValueError(
                            f"{name} {self.id_of(name, row)} references unknown {target.lower()} {row[prop]}"
                        )

        for edge in facts.edges:
            rel = self.relationship(edge.relationship)
            if rel is None:
                raise ValueError(f"Unknown relationship: {edge.relationship}")
            if ids.get(edge.from_id) != rel.from_entity or ids.get(edge.to_id) != rel.to_entity:
                raise ValueError(
                    f"{rel.name} edge references unknown {rel.from_entity.lower()} or {rel.to_entity.lower()}"
                )


class OntologyAdapter:
    """Reads an ontology JSON file and validates its structure and bundled facts."""

    def __init__(self, definition_path: str | Path):
        self.definition_path = Path(definition_path)

    def load(self) -> OntologyDefinition:
        if not self.definition_path.exists():
            raise FileNotFoundError(f"Ontology definition not found: {self.definition_path}")

        with self.definition_path.open("r", encoding="utf-8") as file:
            raw_data: dict[str, Any] = json.load(file)

        facts_payload = raw_data.pop("facts", None)
        legacy_payload = raw_data.pop("sample_facts", None)
        ontology = OntologyDefinition.model_validate(raw_data)
        return ontology.with_facts(facts_payload or legacy_payload or {}, strict=True)
