"""Live ontology state: loads facts from the configured source and can reload them.

The server used to read its source once at import time. ``OntologyRuntime`` keeps the
active ontology, graph store and reasoner together so they can be rebuilt from the
source at any time (``reload``) or automatically when the data gets older than
``REFRESH_INTERVAL_SECONDS``.
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from documents import DocumentAdapter
from graph import GraphStore, Neo4jGraphStore, NetworkXGraphStore, OntologyGraph
from models import OntologyAdapter, OntologyDefinition
from reasoning import SemanticReasoner
from sources import CSVAdapter, HTTPJSONAdapter, OntologySource, PostgreSQLAdapter

SourceFactory = Callable[[Mapping[str, str], Path], OntologySource]

# Register a new source here (or at runtime with ``register_source``) to make it
# selectable through SOURCE_ADAPTER without touching the server.
SOURCE_FACTORIES: dict[str, SourceFactory] = {
    "postgresql": lambda env, root: PostgreSQLAdapter(env.get("POSTGRES_DSN", "")),
    "csv": lambda env, root: CSVAdapter(env.get("CSV_SOURCE_DIR") or root / "data" / "csv"),
    "http": lambda env, root: HTTPJSONAdapter(
        env.get("HTTP_JSON_URL", ""), float(env.get("HTTP_TIMEOUT_SECONDS", "10"))
    ),
    "document": lambda env, root: DocumentAdapter(env.get("DOCUMENT_SOURCE_DIR") or root / "data" / "documents"),
}


def register_source(name: str, factory: SourceFactory) -> None:
    SOURCE_FACTORIES[name.lower()] = factory


class OntologyRuntime:
    def __init__(self, project_dir: Path, env: Mapping[str, str] | None = None):
        self.project_dir = Path(project_dir)
        self.env = dict(env if env is not None else os.environ)
        self.ontology_path = Path(self.env.get("ONTOLOGY_PATH") or self.project_dir / "data" / "ontology.json")
        self.refresh_interval = float(self.env.get("REFRESH_INTERVAL_SECONDS", "0") or 0)
        # Remembered so an imported ontology can be switched back out again.
        self._default_env = dict(self.env)
        self._default_path = self.ontology_path

        self._ontology: OntologyDefinition | None = None
        self._store: GraphStore | None = None
        self._reasoner: SemanticReasoner | None = None
        self._neo4j: Neo4jGraphStore | None = None
        self._graph_message: str | None = None
        self.source_status: dict = {}
        self._loaded_at = 0.0
        self.reload()

    # ---- accessors (refresh lazily when stale) ----------------------------

    @property
    def ontology(self) -> OntologyDefinition:
        self._refresh_if_stale()
        return self._ontology

    @property
    def store(self) -> GraphStore:
        self._refresh_if_stale()
        return self._store

    @property
    def reasoner(self) -> SemanticReasoner:
        self._refresh_if_stale()
        return self._reasoner

    def use_definition(self, path: str | Path) -> dict:
        """Switch to another ontology file (with its facts inside) and reload.

        The file is validated first, so a bad file never replaces the working ontology.
        """
        path = Path(path)
        OntologyAdapter(path).load()
        self.ontology_path = path
        self.env = {**self.env, "SOURCE_ADAPTER": "json"}
        return self.reload()

    def reset_definition(self) -> dict:
        """Go back to the ontology and source that were configured at startup."""
        self.ontology_path = self._default_path
        self.env = dict(self._default_env)
        return self.reload()

    def _refresh_if_stale(self) -> None:
        if self.refresh_interval > 0 and time.monotonic() - self._loaded_at >= self.refresh_interval:
            self.reload()

    # ---- loading ----------------------------------------------------------

    def reload(self) -> dict:
        """Re-read the ontology definition and the source, then rebuild graph and reasoner.

        On the first load a failing source falls back to the facts bundled in the JSON
        definition. On later reloads a failing source keeps the previous, working data.
        """
        base = OntologyAdapter(self.ontology_path).load()
        name = self.env.get("SOURCE_ADAPTER", "json").lower()
        loaded, status = self._load_source(name, base)

        if self._ontology is not None and not status["connected"]:
            status["message"] = f"{status['message']} (kept previously loaded data)"
            self.source_status = {**status, "loaded_at": self.source_status.get("loaded_at")}
            self._loaded_at = time.monotonic()  # do not hammer a failing source
            return self.status_summary()

        self._ontology = loaded
        self.source_status = {**status, "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self._loaded_at = time.monotonic()
        self._rebuild_graph()
        self._reasoner = SemanticReasoner(loaded, OntologyGraph(loaded))
        return self.status_summary()

    def _load_source(self, name: str, base: OntologyDefinition) -> tuple[OntologyDefinition, dict]:
        if name == "json":
            return base, {"adapter": "json", "connected": True, "message": "Ontology facts loaded from the local JSON definition"}

        factory = SOURCE_FACTORIES.get(name)
        if factory is None:
            return base, {"adapter": name, "connected": False, "message": "Unknown SOURCE_ADAPTER; kept JSON facts"}

        adapter: OntologySource | None = None
        try:
            adapter = factory(self.env, self.project_dir)
            loaded = adapter.load(base)
            status = {"adapter": name, "connected": True, "message": f"Ontology facts loaded from {name}"}
        except Exception as exc:
            loaded = base
            status = {"adapter": name, "connected": False, "message": f"{name} unavailable; kept JSON facts: {exc}"}
            if hasattr(exc, "report"):
                status["mapping_report"] = exc.report

        report = getattr(adapter, "mapping_report", None)
        if report is not None and "mapping_report" not in status:
            status["mapping_report"] = report
        return loaded, status

    def _rebuild_graph(self) -> None:
        ontology = self._ontology
        fallback = NetworkXGraphStore(ontology)
        self._store = fallback
        self._graph_message = None

        if self.env.get("GRAPH_STORE", "networkx").lower() != "neo4j":
            return
        try:
            if self._neo4j is None:
                self._neo4j = Neo4jGraphStore(
                    ontology=ontology,
                    uri=self.env.get("NEO4J_URI", "bolt://127.0.0.1:7687"),
                    username=self.env.get("NEO4J_USER", "neo4j"),
                    password=self.env.get("NEO4J_PASSWORD", ""),
                )
            self._neo4j.sync(ontology)
            self._store = self._neo4j
        except Exception as exc:
            self._graph_message = f"Neo4j unavailable; using NetworkX: {exc}"

    # ---- status -----------------------------------------------------------

    def graph_status(self) -> dict:
        status = self._store.status()
        if self._graph_message:
            status = {**status, "fallback": True, "message": self._graph_message}
        return status

    def status_summary(self) -> dict:
        return {
            "source": self.source_status,
            "graph_store": self.graph_status(),
            "fact_counts": self._ontology.facts.counts(),
            "definition": {"path": str(self.ontology_path), "is_default": self.ontology_path == self._default_path},
            "refresh_interval_seconds": self.refresh_interval,
        }

    def sync_graph(self) -> dict:
        if isinstance(self._store, Neo4jGraphStore):
            self._store.sync(self._ontology)
            return {"synchronized": True, "graph_store": self.graph_status()}
        return {
            "synchronized": False,
            "graph_store": self.graph_status(),
            "message": "Synchronization is only applicable to the Neo4j backend",
        }
