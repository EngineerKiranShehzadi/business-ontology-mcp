"""Focused tests for ontology validation, source adapters, graph facts, and reasoning.

Tests use the local ontology as the baseline and temporary directories for source adapters.
Each test checks behavior, not just documentation text.
"""

from models import OntologyAdapter
from graph import OntologyGraph


ONTOLOGY_PATH = "data/ontology.json"


def test_ontology_loads_and_validates():
    ontology = OntologyAdapter(ONTOLOGY_PATH).load()
    assert ontology.ontology_name == "Small Sales Ontology"
    assert set(ontology.entities) == {"Customer", "Order", "Product"}


def test_graph_contains_expected_nodes_and_edges():
    ontology = OntologyAdapter(ONTOLOGY_PATH).load()
    graph = OntologyGraph(ontology)

    assert graph.graph.number_of_nodes() == 6
    assert graph.graph.number_of_edges() == 5


def test_customer_neighbors():
    ontology = OntologyAdapter(ONTOLOGY_PATH).load()
    graph = OntologyGraph(ontology)

    result = graph.neighbors("C001")
    assert result["attributes"]["name"] == "Avery Morgan"
    assert result["outgoing"] == [{"relationship": "PLACES", "target": "O100"}]


def test_query_order_product_facts():
    ontology = OntologyAdapter(ONTOLOGY_PATH).load()
    graph = OntologyGraph(ontology)

    facts = graph.query_facts(entity_id="O100", relationship="CONTAINS")
    assert len(facts) == 2


def test_networkx_store_status_and_interface():
    ontology = OntologyAdapter(ONTOLOGY_PATH).load()
    from graph import NetworkXGraphStore

    store = NetworkXGraphStore(ontology)
    status = store.status()

    assert status["backend"] == "networkx"
    assert status["persistent"] is False
    assert status["node_count"] == 6
    assert status["edge_count"] == 5


def test_csv_adapter_loads_facts(tmp_path):
    from sources import CSVAdapter

    (tmp_path / "customers.csv").write_text("customer_id,name,status\nC001,Avery Morgan,active\n", encoding="utf-8")
    (tmp_path / "orders.csv").write_text("order_id,customer_id,order_date,status\nO100,C001,2026-11-20,shipped\n", encoding="utf-8")
    (tmp_path / "products.csv").write_text("product_id,name,category,price\nP100,Notebook,Office,12.50\nP200,Keyboard,Technology,48.00\n", encoding="utf-8")
    (tmp_path / "order_items.csv").write_text("order_id,product_id\nO100,P100\nO100,P200\n", encoding="utf-8")

    base = OntologyAdapter(ONTOLOGY_PATH).load()
    loaded = CSVAdapter(tmp_path).load(base)

    assert len(loaded.facts.rows("Customer")) == 1
    assert len(loaded.facts.rows("Order")) == 1
    assert len(loaded.facts.rows("Product")) == 2
    assert len(loaded.facts.edges) == 3


def test_document_adapter_loads_json(monkeypatch, tmp_path):
    from documents import DocumentAdapter

    (tmp_path / "example.json").write_text(
        '{"customers":[{"customer_id":"C001","name":"Avery Morgan","status":"active"}],'
        '"orders":[{"order_id":"O100","customer_id":"C001","order_date":"2026-11-20","status":"shipped"}],'
        '"products":[{"product_id":"P100","name":"Notebook","category":"Office","price":12.5}],'
        '"edges":[{"from":"C001","to":"O100","relationship":"PLACES"},{"from":"O100","to":"P100","relationship":"CONTAINS"}]}',
        encoding="utf-8",
    )

    base = OntologyAdapter(ONTOLOGY_PATH).load()
    loaded = DocumentAdapter(tmp_path).load(base)

    assert len(loaded.facts.rows("Customer")) == 1
    assert len(loaded.facts.rows("Product")) == 1


def test_http_json_adapter_loads_payload(monkeypatch):
    import sources as http_module
    from sources import HTTPJSONAdapter

    payload = {
        "customers": [{"customer_id": "C001", "name": "Avery Morgan", "status": "active"}],
        "orders": [{"order_id": "O100", "customer_id": "C001", "order_date": "2026-11-20", "status": "shipped"}],
        "products": [{"product_id": "P100", "name": "Notebook", "category": "Office", "price": 12.5}],
        "edges": [
            {"from": "C001", "to": "O100", "relationship": "PLACES"},
            {"from": "O100", "to": "P100", "relationship": "CONTAINS"},
        ],
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            import json
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(http_module, "urlopen", lambda request, timeout: FakeResponse())

    base = OntologyAdapter(ONTOLOGY_PATH).load()
    loaded = HTTPJSONAdapter("http://example.test/ontology", timeout_seconds=2).load(base)

    assert len(loaded.facts.rows("Product")) == 1


def test_csv_adapter_implements_shared_source_interface():
    from sources import OntologySource
    from sources import CSVAdapter

    assert issubclass(CSVAdapter, OntologySource)
    assert CSVAdapter.name == "csv"


def test_semantic_reasoner_derives_customer_purchases():
    from reasoning import SemanticReasoner

    ontology = OntologyAdapter(ONTOLOGY_PATH).load()
    result = SemanticReasoner(ontology).derive_facts(entity_id="C001")

    assert result["derived_relationship"] == "PURCHASES"
    assert result["fact_count"] == 2
    assert {fact["target"] for fact in result["facts"]} == {"P100", "P200"}


def test_shared_source_rejects_unknown_relationship():
    from sources import build_ontology_from_payload

    base = OntologyAdapter(ONTOLOGY_PATH).load()
    try:
        build_ontology_from_payload(
            base,
            {
                "customers": [{"customer_id": "C001", "name": "Avery Morgan", "status": "active"}],
                "orders": [{"order_id": "O100", "customer_id": "C001", "order_date": "2026-11-20", "status": "shipped"}],
                "products": [{"product_id": "P100", "name": "Notebook", "category": "Office", "price": 12.5}],
                "edges": [{"from": "C001", "to": "O100", "relationship": "UNKNOWN"}],
            },
        )
        assert False, "Unknown relationship should be rejected"
    except ValueError as exc:
        assert "Unknown relationship" in str(exc)


def test_shared_source_rejects_dangling_edge():
    from sources import build_ontology_from_payload

    base = OntologyAdapter(ONTOLOGY_PATH).load()
    try:
        build_ontology_from_payload(
            base,
            {
                "customers": [{"customer_id": "C001", "name": "Avery Morgan", "status": "active"}],
                "orders": [{"order_id": "O100", "customer_id": "C001", "order_date": "2026-11-20", "status": "shipped"}],
                "products": [{"product_id": "P100", "name": "Notebook", "category": "Office", "price": 12.5}],
                "edges": [{"from": "C001", "to": "MISSING", "relationship": "PLACES"}],
            },
        )
        assert False, "Dangling edge should be rejected"
    except ValueError as exc:
        assert "unknown customer or order" in str(exc)


def test_document_adapter_loads_jsonl(tmp_path):
    from documents import DocumentAdapter

    (tmp_path / "facts.jsonl").write_text(
        '{"customers":[{"customer_id":"C001","name":"Avery Morgan","status":"active"}]}'
        + '\n'
        + '{"orders":[{"order_id":"O100","customer_id":"C001","order_date":"2026-11-20","status":"shipped"}]}'
        + '\n'
        + '{"products":[{"product_id":"P100","name":"Notebook","category":"Office","price":12.5}]}'
        + '\n'
        + '{"edges":[{"from":"C001","to":"O100","relationship":"PLACES"},{"from":"O100","to":"P100","relationship":"CONTAINS"}]}'
        + '\n',
        encoding="utf-8",
    )

    base = OntologyAdapter(ONTOLOGY_PATH).load()
    loaded = DocumentAdapter(tmp_path).load(base)

    assert len(loaded.facts.rows("Customer")) == 1
    assert len(loaded.facts.rows("Order")) == 1
    assert len(loaded.facts.rows("Product")) == 1


def test_document_adapter_extracts_txt_with_provenance(tmp_path):
    from documents import DocumentAdapter

    (tmp_path / "facts.txt").write_text(
        "Customer C001: Avery Morgan | status=active\n"
        "Order O100: customer=C001 | date=2026-11-20 | status=shipped\n"
        "Product P100: Notebook | category=Office | price=12.50\n"
        "C001 PLACES O100\n"
        "O100 CONTAINS P100\n",
        encoding="utf-8",
    )

    base = OntologyAdapter(ONTOLOGY_PATH).load()
    adapter = DocumentAdapter(tmp_path)
    loaded = adapter.load(base)

    assert len(loaded.facts.rows("Customer")) == 1
    assert len(loaded.facts.rows("Order")) == 1
    assert len(loaded.facts.rows("Product")) == 1
    assert len(loaded.facts.edges) == 2
    assert adapter.mapping_report["mapped_facts"] == 5
    assert adapter.mapping_report["provenance"][0]["source"] == "facts.txt"
    assert adapter.mapping_report["provenance"][0]["line"] == 1


def test_document_adapter_extracts_md_and_reports_unmapped(tmp_path):
    from documents import DocumentAdapter

    (tmp_path / "notes.md").write_text(
        "# Sales notes\n"
        "Customer C001: Avery Morgan | status=active\n"
        "Customer C999 is mentioned but no complete status is available\n",
        encoding="utf-8",
    )

    base = OntologyAdapter(ONTOLOGY_PATH).load()
    adapter = DocumentAdapter(tmp_path)
    loaded = adapter.load(base)

    assert len(loaded.facts.rows("Customer")) == 1
    assert adapter.mapping_report["unmapped_content"]
    assert adapter.mapping_report["unmapped_content"][0]["source"] == "notes.md"


def test_document_adapter_extracts_docx(tmp_path):
    from docx import Document
    from documents import DocumentAdapter

    doc = Document()
    doc.add_paragraph("Customer C001: Avery Morgan | status=active")
    doc.add_paragraph("Product P100: Notebook | category=Office | price=12.50")
    doc.save(tmp_path / "facts.docx")

    base = OntologyAdapter(ONTOLOGY_PATH).load()
    adapter = DocumentAdapter(tmp_path)
    loaded = adapter.load(base)

    assert len(loaded.facts.rows("Customer")) == 1
    assert len(loaded.facts.rows("Product")) == 1
    assert adapter.mapping_report["provenance"][0]["format"] == ".docx"


def test_document_adapter_extracts_pdf_with_page_provenance(monkeypatch, tmp_path):
    import pypdf
    from documents import DocumentAdapter

    class FakePage:
        def extract_text(self):
            return (
                "Customer C001: Avery Morgan | status=active\n"
                "Order O100: customer=C001 | date=2026-11-20 | status=shipped\n"
                "Product P100: Notebook | category=Office | price=12.50\n"
                "C001 PLACES O100\n"
                "O100 CONTAINS P100"
            )

    class FakeReader:
        pages = [FakePage()]

    monkeypatch.setattr(pypdf, "PdfReader", lambda path: FakeReader())

    (tmp_path / "facts.pdf").write_bytes(b"placeholder")
    base = OntologyAdapter(ONTOLOGY_PATH).load()
    adapter = DocumentAdapter(tmp_path)
    loaded = adapter.load(base)

    assert len(loaded.facts.rows("Customer")) == 1
    assert len(loaded.facts.rows("Order")) == 1
    assert len(loaded.facts.rows("Product")) == 1
    assert len(loaded.facts.edges) == 2
    assert adapter.mapping_report["provenance"][0]["source"] == "facts.pdf"
    assert adapter.mapping_report["provenance"][0]["page"] == 1


def test_document_adapter_reports_unmapped_when_no_facts_can_be_mapped(tmp_path):
    from documents import DocumentAdapter, DocumentMappingError

    (tmp_path / "unsupported.txt").write_text(
        "Customer C001 was mentioned but status and name are unclear.\n",
        encoding="utf-8",
    )

    base = OntologyAdapter(ONTOLOGY_PATH).load()
    adapter = DocumentAdapter(tmp_path)

    try:
        adapter.load(base)
        assert False, "No-fact document should report that mapping failed"
    except DocumentMappingError as exc:
        assert exc.report["unmapped_content"]
        assert exc.report["mapped_facts"] == 0


def test_document_adapter_rejects_dangling_free_text_edge(tmp_path):
    from documents import DocumentAdapter

    (tmp_path / "facts.txt").write_text(
        "Customer C001: Avery Morgan | status=active\n"
        "MISSING PLACES O100\n",
        encoding="utf-8",
    )

    base = OntologyAdapter(ONTOLOGY_PATH).load()
    try:
        DocumentAdapter(tmp_path).load(base)
        assert False, "Dangling edge should be rejected"
    except ValueError as exc:
        assert "unknown customer or order" in str(exc).lower()
