import json
from io import BytesIO
from pathlib import Path

import pdf_import


def test_cloud_pdf_analysis_and_apply(monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCE_ADAPTER", "json")
    monkeypatch.setenv("GRAPH_STORE", "networkx")
    monkeypatch.setenv("ONTOLOGY_PATH", "data/ontology.json")
    import webui

    sample = Path(__file__).resolve().parents[1] / "data" / "samples" / "sample_report.pdf"
    monkeypatch.setattr(webui, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(webui, "GENERATED", tmp_path / "generated" / "ontology.json")
    monkeypatch.setattr(webui, "state", {"proposal": None})
    monkeypatch.setenv("PDF_LLM_PROVIDER", "cloud")
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    result = {
        "entities": [
            {"id": "customer", "type": "Customer", "name": "C001"},
            {"id": "order", "type": "Order", "name": "O100"},
            {"id": "invented", "type": "Customer", "name": "Absent name"},
        ],
        "links": [
            {"source": "customer", "target": "order", "relationship": "PLACED", "evidence": "O100 C001"},
            {"source": "order", "target": "customer", "relationship": "INVALID", "evidence": "Absent quote"},
        ],
    }

    def chat(request, timeout):
        assert request.full_url == "https://ollama.com/api/chat"
        assert request.get_header("Authorization") == "Bearer test-key"
        payload = json.loads(request.data)
        assert "format" not in payload
        assert "O100 C001" in payload["messages"][-1]["content"]
        return BytesIO(json.dumps({"message": {"content": json.dumps(result)}}).encode())

    monkeypatch.setattr(pdf_import, "urlopen", chat)
    monkeypatch.setattr(webui.runtime, "use_definition", lambda path: json.loads(path.read_text(encoding="utf-8")))
    webui.api("/api/upload", {"name": [sample.name]}, "POST", sample.read_bytes())
    report = webui.api("/api/infer", {"mode": ["pdf-ai"]}, "POST")["report"]
    assert report["link_preview"] == [["C001", "PLACED", "O100", sample.name, "O100 C001"]]
    assert len(report["warnings"]) == 2
    saved = webui.api("/api/apply", {}, "POST")
    assert saved["facts"]["edges"][0]["provenance"]["evidence"] == "O100 C001"
    assert len(saved["facts"]["entities"]["Customer"]) == 1
