"""Tests for the ontology-driven (domain-independent) behavior and runtime reloading.

A second, unrelated domain (a library) proves nothing is hardcoded to Customer/Order/Product.
"""

import json
import time
from pathlib import Path

import pytest

from models import OntologyAdapter, OntologyDefinition
from sources import CSVAdapter
from documents import DocumentAdapter
from reasoning import SemanticReasoner
from runtime import OntologyRuntime, register_source
from sources import OntologySource

ONTOLOGY_PATH = "data/ontology.json"

LIBRARY_DEFINITION = {
    "ontology_name": "Library",
    "ontology_version": "1",
    "entities": {
        "Member": {"description": "A reader.", "properties": {"member_id": "string", "name": "string"}},
        "Book": {
            "description": "A title.",
            "properties": {"book_id": "string", "title": "string", "pages": "integer"},
            "collection": "books",
        },
        "Loan": {
            "description": "A borrowing.",
            "properties": {"loan_id": "string", "member_id": "ref:Member", "due": "date"},
        },
    },
    "relationships": [
        {"name": "BORROWS", "from": "Member", "to": "Loan", "description": "x"},
        {
            "name": "COVERS",
            "from": "Loan",
            "to": "Book",
            "description": "y",
            "source": {"table": "loan_books", "from_column": "loan_id", "to_column": "book_id"},
        },
    ],
    "rules": [{"name": "READS", "path": ["BORROWS", "COVERS"]}],
}


@pytest.fixture
def library(tmp_path):
    path = tmp_path / "library.json"
    path.write_text(json.dumps(LIBRARY_DEFINITION), encoding="utf-8")
    return OntologyAdapter(path).load()


def test_new_domain_defaults_and_csv_source(tmp_path, library):
    assert library.entities["Member"].id_field == "member_id"
    assert library.entities["Member"].collection == "members"

    data = tmp_path / "csv"
    data.mkdir()
    (data / "members.csv").write_text("member_id,name\nM1,Ada\n", encoding="utf-8")
    (data / "books.csv").write_text("book_id,title,pages\nB1,Dune,412\n", encoding="utf-8")
    (data / "loans.csv").write_text("loan_id,member_id,due\nL1,M1,2026-12-01\n", encoding="utf-8")
    (data / "loan_books.csv").write_text("loan_id,book_id\nL1,B1\n", encoding="utf-8")

    loaded = CSVAdapter(data).load(library)
    assert loaded.facts.rows("Book")[0]["pages"] == 412  # typed, not a string
    assert {(e.from_id, e.relationship, e.to_id) for e in loaded.facts.edges} == {
        ("M1", "BORROWS", "L1"),  # inferred from the ref:Member property
        ("L1", "COVERS", "B1"),  # from the join table
    }

    result = SemanticReasoner(loaded).derive_facts(entity_id="M1")
    assert result["derived_relationship"] == "READS"
    assert [(f["source"], f["target"], f["via"]) for f in result["facts"]] == [("M1", "B1", ["L1"])]


def test_new_domain_free_text_documents(tmp_path, library):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "notes.txt").write_text(
        "Member M1: Ada\n"
        "Book B1: Dune | pages=412\n"
        "Loan L1: member=M1 | due=2026-12-01\n"
        "M1 BORROWS L1\n"
        "L1 COVERS B1\n"
        "Book B2: Emma | pages=lots\n",
        encoding="utf-8",
    )
    adapter = DocumentAdapter(docs)
    loaded = adapter.load(library)

    assert len(loaded.facts.rows("Member")) == 1
    assert loaded.facts.rows("Book")[0]["pages"] == 412
    assert len(loaded.facts.edges) == 2
    assert any("pages=lots" in item["text"] for item in adapter.mapping_report["unmapped_content"])


def test_typed_validation_rejects_bad_values(library):
    with pytest.raises(ValueError):
        library.with_facts({"books": [{"book_id": "B1", "title": "Dune", "pages": "many"}]})
    with pytest.raises(ValueError):
        library.with_facts(
            {
                "members": [{"member_id": "M1", "name": "Ada"}],
                "loans": [{"loan_id": "L1", "member_id": "M1", "due": "tomorrow"}],
            }
        )
    with pytest.raises(ValueError, match="unknown member"):
        library.with_facts({"loans": [{"loan_id": "L1", "member_id": "NOPE", "due": "2026-12-01"}]})
    with pytest.raises(ValueError, match="Duplicate id"):
        library.with_facts(
            {"members": [{"member_id": "X", "name": "a"}], "books": [{"book_id": "X", "title": "b", "pages": 1}]}
        )


def test_edge_endpoint_types_are_checked():
    base = OntologyAdapter(ONTOLOGY_PATH).load()
    with pytest.raises(ValueError, match="unknown customer or order"):
        base.with_facts(
            {
                "customers": [{"customer_id": "C001", "name": "A", "status": "active"}],
                "products": [{"product_id": "P100", "name": "N", "category": "O", "price": 1}],
                "edges": [{"from": "C001", "to": "P100", "relationship": "PLACES"}],
            }
        )


def test_invalid_ontology_definitions_are_rejected():
    base = {
        "ontology_name": "x",
        "ontology_version": "1",
        "entities": {"A": {"description": "d", "properties": ["a_id"]}},
        "relationships": [],
    }
    OntologyDefinition.model_validate(base)

    with pytest.raises(ValueError, match="id_field"):
        OntologyDefinition.model_validate({**base, "entities": {"A": {"description": "d", "properties": ["x"]}}})
    with pytest.raises(ValueError, match="Invalid entity name"):
        OntologyDefinition.model_validate(
            {**base, "entities": {"A) DETACH DELETE": {"description": "d", "properties": ["a_id"]}}}
        )
    with pytest.raises(ValueError, match="unknown relationship"):
        OntologyDefinition.model_validate({**base, "rules": [{"name": "R", "path": ["X", "Y"]}]})


def test_legacy_list_properties_and_sample_facts_still_load(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "ontology_name": "Legacy",
                "ontology_version": "1",
                "entities": {"Thing": {"description": "d", "properties": ["thing_id", "label"]}},
                "relationships": [],
                "sample_facts": {"things": [{"thing_id": "T1", "label": "one"}], "edges": []},
            }
        ),
        encoding="utf-8",
    )
    loaded = OntologyAdapter(path).load()
    assert loaded.facts.rows("Thing") == [{"thing_id": "T1", "label": "one"}]


def test_reasoner_rule_selection_and_unknown_rule():
    reasoner = SemanticReasoner(OntologyAdapter(ONTOLOGY_PATH).load())
    assert reasoner.derive_facts(rule="purchases")["fact_count"] == 3
    assert "error" in reasoner.derive_facts(rule="nope")
    assert set(reasoner.derive_all()["rules"]) == {"PURCHASES"}


# ---- dynamic data: the runtime reloads from the source -----------------------------


def _csv_env(directory):
    return {"SOURCE_ADAPTER": "csv", "CSV_SOURCE_DIR": str(directory)}


def _write_csv(directory, customers):
    (directory / "customers.csv").write_text("customer_id,name,status\n" + customers, encoding="utf-8")
    (directory / "orders.csv").write_text(
        "order_id,customer_id,order_date,status\nO1,C1,2026-11-20,shipped\n", encoding="utf-8"
    )
    (directory / "products.csv").write_text("product_id,name,category,price\nP1,Pen,Office,2.5\n", encoding="utf-8")
    (directory / "order_items.csv").write_text("order_id,product_id\nO1,P1\n", encoding="utf-8")


def test_runtime_reload_picks_up_changed_source(tmp_path):
    _write_csv(tmp_path, "C1,Ada,active\n")
    runtime = OntologyRuntime(Path("."), _csv_env(tmp_path))
    assert runtime.source_status["connected"] and runtime.source_status["loaded_at"]
    assert len(runtime.store.entities("customer")) == 1
    assert runtime.reasoner.derive_facts("C1")["fact_count"] == 1

    _write_csv(tmp_path, "C1,Ada,active\nC2,Bo,active\n")
    assert len(runtime.store.entities("customer")) == 1  # nothing changes until reload
    summary = runtime.reload()
    assert summary["fact_counts"]["Customer"] == 2
    assert len(runtime.store.entities("customer")) == 2


def test_runtime_keeps_previous_data_when_reload_fails(tmp_path):
    _write_csv(tmp_path, "C1,Ada,active\n")
    runtime = OntologyRuntime(Path("."), _csv_env(tmp_path))
    (tmp_path / "orders.csv").unlink()

    summary = runtime.reload()
    assert summary["source"]["connected"] is False
    assert "kept previously loaded data" in summary["source"]["message"]
    assert len(runtime.store.entities("customer")) == 1


def test_runtime_falls_back_to_json_on_first_load_failure(tmp_path):
    runtime = OntologyRuntime(Path("."), _csv_env(tmp_path / "missing"))
    assert runtime.source_status["connected"] is False
    assert runtime.status_summary()["fact_counts"]["Customer"] == 2  # bundled JSON facts


def test_runtime_auto_refresh_interval(tmp_path):
    _write_csv(tmp_path, "C1,Ada,active\n")
    runtime = OntologyRuntime(Path("."), {**_csv_env(tmp_path), "REFRESH_INTERVAL_SECONDS": "0.01"})
    _write_csv(tmp_path, "C1,Ada,active\nC2,Bo,active\n")
    time.sleep(0.05)
    assert len(runtime.store.entities("customer")) == 2


def test_register_custom_source():
    class Static(OntologySource):
        name = "static"

        def load(self, ontology):
            return ontology.with_facts({"customers": [{"customer_id": "C9", "name": "Zed", "status": "active"}]})

    register_source("static", lambda env, root: Static())
    runtime = OntologyRuntime(Path("."), {"SOURCE_ADAPTER": "static"})
    assert [item["entity_id"] for item in runtime.store.entities("Customer")] == ["C9"]
