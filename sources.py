"""Data sources: each one reads facts from somewhere and returns a validated ontology.

OntologySource is the shared interface. CSVAdapter, HTTPJSONAdapter and PostgreSQLAdapter
build their tables, columns and edges from the ontology definition (models.py).
Document files (JSON, text, PDF, DOCX) are handled in documents.py.
"""

import csv
import json
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.request import Request, urlopen

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from models import EntityDefinition, OntologyDefinition, RelationshipDefinition


class OntologySource(ABC):
    """Shared interface for ontology fact sources."""

    name: str

    @abstractmethod
    def load(self, ontology: OntologyDefinition) -> OntologyDefinition:
        raise NotImplementedError


def build_ontology_from_payload(ontology: OntologyDefinition, payload: dict) -> OntologyDefinition:
    """Validate a source payload and replace only the facts."""
    return ontology.with_facts(payload)


def foreign_key_for(
    ontology: OntologyDefinition, rel: RelationshipDefinition
) -> tuple[EntityDefinition, str] | None:
    """Find the ref: property that implies ``rel`` (e.g. Order.customer_id for PLACES).

    Returns the entity holding the key and the property name, or None when the
    relationship is not implied by a foreign key.
    """
    target = ontology.entities[rel.to_entity]
    for prop, referenced in target.refs().items():
        if referenced == rel.from_entity:
            return target, prop
    return None


def edges_from_rows(
    ontology: OntologyDefinition,
    rows_by_entity: dict[str, list[dict]],
    join_rows: dict[str, list[dict]],
) -> list[dict]:
    """Build edges for tabular sources.

    A relationship backed by a foreign key yields one edge per target row; a
    relationship with a ``source`` join table yields one edge per join row.
    """
    edges = []
    for rel in ontology.relationships:
        if rel.source is not None:
            for row in join_rows[rel.name]:
                edges.append(
                    {
                        "from": row[rel.source.from_column],
                        "to": row[rel.source.to_column],
                        "relationship": rel.name,
                    }
                )
            continue

        implied = foreign_key_for(ontology, rel)
        if implied is None:
            continue
        target, prop = implied
        target_name = rel.to_entity
        for row in rows_by_entity.get(target_name, []):
            edges.append({"from": row[prop], "to": row[target.id_field], "relationship": rel.name})
    return edges

# Every source payload is normalized and validated by OntologyDefinition.with_facts
# before graph construction. IDs and relationship names must match the ontology.


class CSVAdapter(OntologySource):
    """Read one CSV file per entity (and per join table) described by the ontology.

    Each entity reads ``<table>.csv`` (``table`` defaults to the entity collection,
    e.g. customers.csv) with one column per property. Relationships backed by a
    ``ref:`` property create edges from that column; relationships with a ``source``
    join table read ``<source.table>.csv``.
    """

    name = "csv"

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def load(self, ontology: OntologyDefinition) -> OntologyDefinition:
        if not self.directory.exists():
            raise FileNotFoundError(f"CSV source directory not found: {self.directory}")

        rows_by_entity = {
            name: self._read_rows(f"{definition.table}.csv") for name, definition in ontology.entities.items()
        }
        join_rows = {
            rel.name: self._read_rows(f"{rel.source.table}.csv")
            for rel in ontology.relationships
            if rel.source is not None
        }

        return build_ontology_from_payload(
            ontology,
            {
                "entities": rows_by_entity,
                "edges": edges_from_rows(ontology, rows_by_entity, join_rows),
            },
        )

    def _read_rows(self, filename: str) -> list[dict]:
        path = self.directory / filename
        if not path.exists():
            raise FileNotFoundError(f"CSV source file not found: {path}")

        with path.open("r", encoding="utf-8", newline="") as file:
            return [dict(row) for row in csv.DictReader(file)]


class HTTPJSONAdapter(OntologySource):
    """Read ontology facts from a read-only HTTP JSON endpoint.

    The endpoint response must contain the existing ontology fact collections.
    """

    name = "http"

    def __init__(self, url: str, timeout_seconds: float = 10.0):
        if not url:
            raise ValueError("HTTP_JSON_URL is required")
        self.url = url
        self.timeout_seconds = timeout_seconds

    def load(self, ontology: OntologyDefinition) -> OntologyDefinition:
        request = Request(
            self.url,
            headers={"Accept": "application/json", "User-Agent": "business-ontology-mcp/1.0"},
        )

        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if not isinstance(payload, dict):
            raise ValueError("HTTP JSON source must return a JSON object")

        # This is a read-only GET request. The response is validated as ontology facts, not executed as code.

        return build_ontology_from_payload(ontology, payload)


class PostgreSQLAdapter(OntologySource):
    """PostgreSQL source adapter driven entirely by the ontology.

    Each entity is read from its ``table`` using its property names as columns.
    Relationships backed by a ``ref:`` property create edges from that column;
    relationships with a ``source`` join table read that table. Table and column
    names are quoted identifiers taken from the ontology, never from user input.
    """

    name = "postgresql"

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("POSTGRES_DSN is required")
        self.dsn = dsn

    def load(self, ontology: OntologyDefinition) -> OntologyDefinition:
        rows_by_entity: dict[str, list[dict]] = {}
        join_rows: dict[str, list[dict]] = {}

        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                for name, definition in ontology.entities.items():
                    cursor.execute(self._select_entity(definition))
                    rows_by_entity[name] = cursor.fetchall()

                for rel in ontology.relationships:
                    if rel.source is None:
                        continue
                    cursor.execute(
                        sql.SQL("SELECT {} AS from_id, {} AS to_id FROM {}").format(
                            sql.Identifier(rel.source.from_column),
                            sql.Identifier(rel.source.to_column),
                            sql.Identifier(rel.source.table),
                        )
                    )
                    join_rows[rel.name] = [
                        {rel.source.from_column: row["from_id"], rel.source.to_column: row["to_id"]}
                        for row in cursor.fetchall()
                    ]

        return build_ontology_from_payload(
            ontology,
            {
                "entities": rows_by_entity,
                "edges": edges_from_rows(ontology, rows_by_entity, join_rows),
            },
        )

    @staticmethod
    def _select_entity(definition: EntityDefinition) -> sql.Composed:
        columns = []
        for prop, spec in definition.properties.items():
            column = sql.Identifier(prop)
            if spec in {"date", "datetime"}:
                # Dates arrive as ISO text so they validate like every other source.
                columns.append(sql.SQL("{}::text AS {}").format(column, column))
            else:
                columns.append(column)
        return sql.SQL("SELECT {} FROM {}").format(sql.SQL(", ").join(columns), sql.Identifier(definition.table))

# SQL rows pass through the same ontology-generated Pydantic models as every other source.
