"""Tests for inferring an ontology from files (no AI)."""

import json
from pathlib import Path

import pytest

from graph import OntologyGraph
from inference import InferenceError, infer_ontology
from models import OntologyAdapter, OntologyDefinition
from reasoning import SemanticReasoner
from runtime import OntologyRuntime


def write(directory, name, text):
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def demo_csvs(tmp_path):
    return [
        write(tmp_path, "customers.csv", "customer_id,name,status\nC001,Avery Morgan,active\nC002,Jordan Lee,active\n"),
        write(tmp_path, "orders.csv", "order_id,customer_id,order_date,status\nO100,C001,2026-11-20,shipped\nO101,C002,2026-11-21,processing\n"),
        write(tmp_path, "products.csv", "product_id,name,category,price\nP100,Notebook,Office,12.50\nP200,Keyboard,Technology,48.00\n"),
        write(tmp_path, "order_items.csv", "order_id,product_id\nO100,P100\nO100,P200\nO101,P100\n"),
    ]


def load(result):
    """Build the real ontology from an inference result, exactly as the app does."""
    data = dict(result["ontology"])
    facts = data.pop("facts")
    return OntologyDefinition.model_validate(data).with_facts(facts, strict=True)


def test_csvs_become_entities_references_and_a_link_table(tmp_path):
    result = infer_ontology(demo_csvs(tmp_path))
    ontology = result["ontology"]

    assert set(ontology["entities"]) == {"Customer", "Order", "Product"}  # order_items is a link, not an entity
    assert ontology["entities"]["Order"]["properties"] == {
        "order_id": "string", "customer_id": "ref:Customer", "order_date": "date", "status": "string"}
    assert ontology["entities"]["Product"]["properties"]["price"] == "number"
    assert ontology["entities"]["Order"]["id_field"] == "order_id"

    rels = {r["name"]: r for r in ontology["relationships"]}
    assert (rels["HAS_ORDER"]["from"], rels["HAS_ORDER"]["to"]) == ("Customer", "Order")
    assert (rels["HAS_PRODUCT"]["from"], rels["HAS_PRODUCT"]["to"]) == ("Order", "Product")
    assert rels["HAS_PRODUCT"]["source"] == {"table": "order_items", "from_column": "order_id", "to_column": "product_id"}
    assert len(ontology["facts"]["edges"]) == 5  # 2 orders + 3 order items


def test_inferred_ontology_works_with_graph_and_reasoning(tmp_path):
    ontology = load(infer_ontology(demo_csvs(tmp_path)))
    assert OntologyGraph(ontology).graph.number_of_nodes() == 6

    reasoner = SemanticReasoner(ontology)
    assert reasoner.rule_names()  # Customer -> Order -> Product was suggested
    result = reasoner.derive_facts(entity_id="C001")
    assert {f["target"] for f in result["facts"]} == {"P100", "P200"}


def test_repeated_ids_across_tables_get_prefixed(tmp_path):
    files = [
        write(tmp_path, "authors.csv", "id,name\n1,Ada\n2,Bo\n"),
        write(tmp_path, "books.csv", "id,title,author_id\n1,Dune,1\n2,Emma,2\n"),
    ]
    result = infer_ontology(files)
    ontology = load(result)
    ids = {row["id"] for rows in result["ontology"]["facts"]["entities"].values() for row in rows}
    assert ids == {"author-1", "author-2", "book-1", "book-2"}
    assert result["ontology"]["entities"]["Book"]["properties"]["author_id"] == "ref:Author"
    assert any("prefixed" in w for w in result["report"]["warnings"])
    assert {(e.from_id, e.to_id) for e in ontology.facts.edges} == {("author-1", "book-1"), ("author-2", "book-2")}


def test_types_are_detected(tmp_path):
    files = [write(tmp_path, "items.csv", "sku,qty,price,active,added,note\nA1,3,1.5,true,2026-01-02,007\nA2,4,2,false,2026-01-03,008\n")]
    props = infer_ontology(files)["ontology"]["entities"]["Item"]["properties"]
    assert props == {"sku": "string", "qty": "integer", "price": "number", "active": "boolean", "added": "date", "note": "string"}


def test_ids_are_generated_when_no_column_is_unique(tmp_path):
    files = [write(tmp_path, "colors.csv", "color,shade\nred,dark\nred,light\n")]
    result = infer_ontology(files)
    entity = result["ontology"]["entities"]["Color"]
    assert entity["id_field"] == "color_id"
    assert any("IDs were generated" in w for w in result["report"]["warnings"])


def test_incomplete_rows_and_dangling_references_are_reported(tmp_path):
    files = [
        write(tmp_path, "teams.csv", "team_id,name\nT1,Red\nT2,Blue\n"),
        write(tmp_path, "players.csv", "player_id,name,team_id\nP1,Ann,T1\nP2,,T2\nP3,Cy,T9\n"),
    ]
    result = infer_ontology(files)
    warnings = " ".join(result["report"]["warnings"])
    assert "skipped 1 row" in warnings
    assert "do not exist" in warnings  # T9 is not a team, so team_id stays plain text
    assert result["ontology"]["entities"]["Player"]["properties"]["team_id"] == "string"


def test_json_arrays_and_wrapper_objects(tmp_path):
    files = [write(tmp_path, "data.json", json.dumps({
        "customers": [{"customer_id": "C1", "name": "Ada"}],
        "orders": [{"order_id": "O1", "customer_id": "C1", "total": 9.5}],
    }))]
    ontology = infer_ontology(files)["ontology"]
    assert set(ontology["entities"]) == {"Customer", "Order"}
    assert ontology["entities"]["Order"]["properties"]["customer_id"] == "ref:Customer"


def test_docx_tables_use_the_heading_as_the_table_name(tmp_path):
    from docx import Document

    doc = Document()
    doc.add_paragraph("Customers")
    table = doc.add_table(rows=3, cols=2)
    for row, values in enumerate([("customer_id", "name"), ("C1", "Ada"), ("C2", "Bo")]):
        for col, value in enumerate(values):
            table.cell(row, col).text = value
    doc.save(tmp_path / "report.docx")

    result = infer_ontology([tmp_path / "report.docx"])
    assert set(result["ontology"]["entities"]) == {"Customer"}
    assert len(result["ontology"]["facts"]["entities"]["Customer"]) == 2


def test_text_with_pipe_tables_space_tables_and_record_lines(tmp_path):
    path = write(tmp_path, "notes.md", "\n".join([
        "Some introduction text that is just a sentence.",
        "",
        "Teams",
        "| team_id | name |",
        "|---------|------|",
        "| T1      | Red  |",
        "| T2      | Blue |",
        "",
        "Players",
        "player_id   name   team_id",
        "P1          Ann    T1",
        "P2          Bo     T2",
        "",
        "Customer C001: Avery Morgan | status=active",
        "Customer C002: Jordan Lee | status=inactive",
    ]))
    result = infer_ontology([path])
    ontology = result["ontology"]
    assert set(ontology["entities"]) == {"Team", "Player", "Customer"}
    assert ontology["entities"]["Player"]["properties"]["team_id"] == "ref:Team"
    assert ontology["entities"]["Customer"]["properties"]["status"] == "string"
    assert len(ontology["facts"]["entities"]["Customer"]) == 2


def test_pdf_text_is_read_like_any_other_text(monkeypatch, tmp_path):
    import pypdf

    class Page:
        def extract_text(self, *args, **kwargs):
            if kwargs:
                raise TypeError("layout mode not available in this fake")
            return "Products\nproduct_id   name   price\nP1   Pen   2.50\nP2   Ink   7.00\n"

    class Reader:
        pages = [Page()]

    monkeypatch.setattr(pypdf, "PdfReader", lambda path: Reader())
    (tmp_path / "catalog.pdf").write_bytes(b"placeholder")
    result = infer_ontology([tmp_path / "catalog.pdf"])
    assert result["ontology"]["entities"]["Product"]["properties"]["price"] == "number"


def test_prose_only_documents_raise_a_clear_error(tmp_path):
    path = write(tmp_path, "story.txt", "Once upon a time there was a customer who ordered a notebook.\nIt was shipped quickly.\n")
    with pytest.raises(InferenceError) as error:
        infer_ontology([path])
    assert "No usable tables" in str(error.value)
    assert any("prose" in w for w in error.value.report["warnings"])


def test_unreadable_files_are_reported_but_do_not_stop_the_rest(tmp_path):
    files = [write(tmp_path, "bad.json", "{not json"), *demo_csvs(tmp_path)]
    result = infer_ontology(files)
    assert any("bad.json" in w and "could not be read" in w for w in result["report"]["warnings"])
    assert "Customer" in result["ontology"]["entities"]


def test_runtime_can_switch_to_an_inferred_ontology_and_back(tmp_path):
    result = infer_ontology(demo_csvs(tmp_path))
    generated = tmp_path / "generated.json"
    generated.write_text(json.dumps(result["ontology"]), encoding="utf-8")
    assert OntologyAdapter(generated).load().facts.counts()["edges"] == 5  # the file is a valid ontology

    runtime = OntologyRuntime(Path("."), {})
    default_counts = runtime.status_summary()["fact_counts"]
    assert runtime.status_summary()["definition"]["is_default"]

    summary = runtime.use_definition(generated)
    assert not summary["definition"]["is_default"]
    assert summary["fact_counts"]["Customer"] == 2
    assert runtime.reasoner.derive_facts(entity_id="C001")["fact_count"] == 2

    assert runtime.reset_definition()["fact_counts"] == default_counts


def test_runtime_rejects_an_invalid_ontology_file(tmp_path):
    runtime = OntologyRuntime(Path("."), {})
    broken = write(tmp_path, "broken.json", json.dumps({"ontology_name": "x"}))
    with pytest.raises(Exception):
        runtime.use_definition(broken)
    assert runtime.status_summary()["definition"]["is_default"]  # nothing changed


# ---- the same data showing up in several files ---------------------------------------

def test_identical_rows_in_several_files_are_kept_once(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    files = [
        write(tmp_path / "a", "customers.csv", "customer_id,name\nC1,Ada\nC2,Bo\n"),
        write(tmp_path / "b", "customers.csv", "customer_id,name\nC2,Bo\nC3,Cy\n"),
    ]
    result = infer_ontology(files)
    rows = result["ontology"]["facts"]["entities"]["Customer"]
    assert [r["customer_id"] for r in rows] == ["C1", "C2", "C3"]
    assert result["ontology"]["entities"]["Customer"]["id_field"] == "customer_id"


def test_tables_with_different_columns_are_combined_by_their_key(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    files = [
        write(tmp_path / "a", "customers.csv", "customer_id,name\nC1,Ada\nC2,Bo\n"),
        write(tmp_path / "b", "customers.csv", "customer_id,city\nC1,Perth\nC2,Hobart\n"),
        write(tmp_path / "a", "orders.csv", "order_id,customer_id\nO1,C1\n"),
    ]
    result = infer_ontology(files)
    assert result["ontology"]["entities"]["Customer"]["properties"].keys() == {"customer_id", "name", "city"}
    assert result["ontology"]["facts"]["entities"]["Customer"][0] == {"customer_id": "C1", "name": "Ada", "city": "Perth"}
    assert any("combined them using 'customer_id'" in w for w in result["report"]["warnings"])
    assert result["ontology"]["entities"]["Order"]["properties"]["customer_id"] == "ref:Customer"


def test_unrelated_tables_with_the_same_name_stay_separate(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    files = [
        write(tmp_path / "a", "items.csv", "sku,qty\nA1,3\n"),
        write(tmp_path / "b", "items.csv", "code,label\nX,Thing\n"),
    ]
    result = infer_ontology(files)
    assert len(result["ontology"]["entities"]) == 2
    assert any("renamed" in w for w in result["report"]["warnings"])


def test_generated_ids_are_not_prefixed_twice(tmp_path):
    files = [
        write(tmp_path, "colors.csv", "color,shade\nred,dark\nred,light\n"),
        write(tmp_path, "sizes.csv", "id,label\ncolor-1,big\ncolor-2,small\n"),  # collides with the generated IDs
    ]
    result = infer_ontology(files)
    ids = [row[result["ontology"]["entities"][e]["id_field"]] for e, rows in result["ontology"]["facts"]["entities"].items() for row in rows]
    assert len(ids) == len(set(ids))
    assert not any("color-color" in i for i in ids)
