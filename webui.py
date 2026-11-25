"""Small web UI for the business ontology (standard library only).

    python webui.py [--port 8000]

Serves webui/index.html and a JSON API backed by the same OntologyRuntime the MCP
server uses. The Import page lets you upload files and build an ontology from them
(see inference.py). Binds to 127.0.0.1 only.
"""

import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from inference import SUPPORTED_SUFFIXES, InferenceError, infer_ontology
from pdf_import import infer_pdf_ontology
from runtime import OntologyRuntime

PROJECT_DIR = Path(__file__).parent
INDEX = PROJECT_DIR / "webui" / "index.html"
UPLOAD_DIR = PROJECT_DIR / "data" / "uploads"
GENERATED = PROJECT_DIR / "data" / "generated" / "ontology.json"
MAX_UPLOAD = 25 * 1024 * 1024

load_dotenv(PROJECT_DIR / ".env")
runtime = OntologyRuntime(PROJECT_DIR)
lock = threading.Lock()  # reload swaps the whole graph, so serialize access
state = {"proposal": None}  # the latest inferred ontology, kept until it is applied


class ApiError(Exception):
    def __init__(self, status: int, payload: dict | str):
        self.status = status
        self.payload = payload if isinstance(payload, dict) else {"error": payload}


def uploaded_files() -> list[Path]:
    return sorted(p for p in UPLOAD_DIR.glob("*") if p.is_file()) if UPLOAD_DIR.exists() else []


def upload_listing() -> dict:
    return {"files": [{"name": p.name, "size": p.stat().st_size} for p in uploaded_files()]}


def api(path: str, query: dict[str, list[str]], method: str, body: bytes = b"") -> dict:
    def arg(name: str) -> str | None:
        return (query.get(name) or [None])[0] or None

    if method == "POST":
        if path == "/api/reload":
            return runtime.reload()
        if path == "/api/upload":
            name = re.sub(r"[^\w.\- ]", "_", Path(arg("name") or "").name).strip()
            if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
                raise ApiError(400, f"Unsupported file type. Use: {', '.join(sorted(SUPPORTED_SUFFIXES))}")
            if not body:
                raise ApiError(400, "The file is empty")
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            (UPLOAD_DIR / name).write_bytes(body)
            state["proposal"] = None
            return upload_listing()
        if path == "/api/uploads/clear":
            for file in uploaded_files():
                file.unlink()
            state["proposal"] = None
            return upload_listing()
        if path == "/api/infer":
            files = uploaded_files()
            if not files:
                raise ApiError(400, "Upload at least one file first")
            state["proposal"] = None
            mode = arg("mode") or "tables"
            if mode not in {"tables", "pdf-ai"}:
                raise ApiError(400, "Unknown import mode")
            infer = infer_pdf_ontology if mode == "pdf-ai" else infer_ontology
            try:
                result = infer(files, name=arg("name") or "Imported ontology")
            except InferenceError as exc:
                raise ApiError(422, {"error": str(exc), "report": exc.report})
            state["proposal"] = result["ontology"]
            return {"report": result["report"]}
        if path == "/api/apply":
            if state["proposal"] is None:
                raise ApiError(400, "Analyze the files first")
            GENERATED.parent.mkdir(parents=True, exist_ok=True)
            GENERATED.write_text(json.dumps(state["proposal"], indent=2), encoding="utf-8")
            return runtime.use_definition(GENERATED)
        if path == "/api/reset":
            return runtime.reset_definition()
        raise KeyError(path)

    if path == "/api/uploads":
        return upload_listing()
    if path == "/api/status":
        return runtime.status_summary()
    if path == "/api/concepts":
        return runtime.store.concepts()
    if path == "/api/entities":
        entities = runtime.store.entities(entity_type=arg("type"))
        return {"count": len(entities), "entities": entities}
    if path == "/api/facts":
        return {"facts": runtime.store.query_facts(entity_id=arg("entity_id"), relationship=arg("relationship"))}
    if path == "/api/neighbors":
        return runtime.store.neighbors(arg("id") or "")
    if path == "/api/derive":
        return runtime.reasoner.derive_facts(entity_id=arg("entity_id"), rule=arg("rule"))
    if path == "/api/graph":
        nodes = runtime.store.entities()
        edges = [
            {"from": f["source"], "to": f["target"], "relationship": f["relationship"]}
            for f in runtime.store.query_facts()
        ]
        return {"nodes": nodes, "edges": edges, "types": list(runtime.store.concepts()["entities"])}
    raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, default=str).encode("utf-8"), "application/json")

    def _same_origin(self) -> bool:
        """Block other websites from calling this local API (they would send a foreign Origin)."""
        port = self.server.server_address[1]
        allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        public_origin = os.getenv("WEBUI_PUBLIC_ORIGIN", "").strip().rstrip("/")
        if public_origin:
            allowed.add(public_origin)
        origin = self.headers.get("Origin")
        hosts = {urlparse(value).netloc for value in allowed}
        return (origin is None or origin in allowed) and self.headers.get("Host") in hosts

    def _handle(self, method: str) -> None:
        url = urlparse(self.path)
        if method == "GET" and url.path in {"/", "/index.html"}:
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            return
        if not url.path.startswith("/api/"):
            self._send(404, b"Not found", "text/plain")
            return
        if not self._same_origin():
            self._send_json(403, {"error": "Forbidden"})
            return

        body = b""
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD:
                self._send_json(413, {"error": f"File is larger than {MAX_UPLOAD // (1024 * 1024)} MB"})
                return
            body = self.rfile.read(length)
        try:
            with lock:
                result = api(url.path, parse_qs(url.query), method, body)
            self._send_json(200, result)
        except ApiError as exc:
            self._send_json(exc.status, exc.payload)
        except KeyError:
            self._send_json(404, {"error": "Unknown endpoint"})
        except Exception as exc:  # surface problems (bad definition, failed source) in the UI
            self._send_json(500, {"error": str(exc)})

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} {format % args}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    port = parser.parse_args().port
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Business Ontology UI running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped")
