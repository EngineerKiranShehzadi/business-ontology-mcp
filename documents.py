"""Read structured and free-text document sources for the ontology.

JSON/JSONL support is structured. TXT/MD/PDF/DOCX support uses only explicit,
deterministic text patterns generated from the ontology (entity names, property
names and relationship names). It does not guess facts or use AI.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from models import EntityDefinition, OntologyDefinition
from sources import OntologySource, build_ontology_from_payload

ID_PATTERN = r"[A-Za-z0-9_-]+"
SUPPORTED_SUFFIXES = {".json", ".jsonl", ".txt", ".md", ".pdf", ".docx"}


@dataclass
class TextSegment:
    text: str
    page: int | None
    line: int | None


class DocumentMappingError(ValueError):
    """Raised when free-text documents contain no ontology facts that can be safely mapped."""

    def __init__(self, report: dict):
        self.report = report
        super().__init__(json.dumps(report, sort_keys=True))


class DocumentAdapter(OntologySource):
    """Load structured facts and deterministic free-text ontology facts."""

    name = "document"

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self._ontology: OntologyDefinition | None = None
        self.mapping_report: dict = {
            "supported_formats": sorted(SUPPORTED_SUFFIXES),
            "files": [],
            "mapped_facts": 0,
            "unmapped_content": [],
            "provenance": [],
        }

    def load(self, ontology: OntologyDefinition) -> OntologyDefinition:
        if not self.directory.exists():
            raise FileNotFoundError(f"Document source directory not found: {self.directory}")

        self._ontology = ontology
        payload = {"entities": {name: [] for name in ontology.entities}, "edges": []}
        files = sorted(
            path
            for path in self.directory.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        )
        if not files:
            raise FileNotFoundError("No supported JSON, JSONL, TXT, MD, PDF, or DOCX files found")

        for path in files:
            suffix = path.suffix.lower()
            file_report = {"filename": path.name, "format": suffix, "status": "processed", "mapped_facts": 0}
            self.mapping_report["files"].append(file_report)

            # Branch by file extension: structured JSON, plain text, PDF, or DOCX.
            # Mapping is deterministic and never uses an AI service or guesses missing values.
            if suffix in {".json", ".jsonl"}:
                added = self._load_structured(path, payload)
            elif suffix in {".txt", ".md"}:
                text = path.read_text(encoding="utf-8")
                segments = [TextSegment(line, 1, index) for index, line in enumerate(text.splitlines(), start=1)]
                added = self._extract_free_text(path, suffix, segments, payload)
            elif suffix == ".pdf":
                segments = self._read_pdf(path)
                added = self._extract_free_text(path, suffix, segments, payload)
            else:
                segments = self._read_docx(path)
                added = self._extract_free_text(path, suffix, segments, payload)

            file_report["mapped_facts"] = added
            self.mapping_report["mapped_facts"] += added

        if self.mapping_report["mapped_facts"] == 0:
            raise DocumentMappingError(self.mapping_report)

        return build_ontology_from_payload(ontology, payload)

    def _load_structured(self, path: Path, payload: dict) -> int:
        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            with path.open("r", encoding="utf-8") as file:
                documents = [json.loads(line) for line in file if line.strip()]
        else:
            with path.open("r", encoding="utf-8") as file:
                document = json.load(file)
            documents = document if isinstance(document, list) else [document]

        mapped = 0
        for document_index, document in enumerate(documents, start=1):
            if not isinstance(document, dict):
                self.mapping_report["unmapped_content"].append(
                    {"source": path.name, "format": suffix, "line": document_index, "reason": "Document entry is not a JSON object"}
                )
                continue

            normalized = self._ontology.normalize_payload(document)
            groups = [(name, rows, payload["entities"][name]) for name, rows in normalized["entities"].items()]
            groups.append(("edges", normalized["edges"], payload["edges"]))

            for label, rows, target in groups:
                for fact_index, fact in enumerate(rows, start=1):
                    if not isinstance(fact, dict):
                        self.mapping_report["unmapped_content"].append(
                            {"source": path.name, "format": suffix, "line": document_index, "reason": f"{label} entry is not an object"}
                        )
                        continue
                    fact = dict(fact)
                    fact["provenance"] = {
                        "source": path.name,
                        "format": suffix,
                        "line": document_index if suffix == ".jsonl" else None,
                        "object": fact_index,
                        "page": None,
                    }
                    target.append(fact)
                    mapped += 1
                    self.mapping_report["provenance"].append(fact["provenance"])
        return mapped

    def _read_pdf(self, path: Path) -> list[TextSegment]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF support requires the pypdf package") from exc

        reader = PdfReader(str(path))
        segments = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            for line_number, line in enumerate(text.splitlines(), start=1):
                segments.append(TextSegment(line, page_number, line_number))
        return segments

    def _read_docx(self, path: Path) -> list[TextSegment]:
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("DOCX support requires the python-docx package") from exc

        document = Document(str(path))
        segments = []
        for index, paragraph in enumerate(document.paragraphs, start=1):
            if paragraph.text.strip():
                segments.append(TextSegment(paragraph.text, None, index))
        for table_index, table in enumerate(document.tables, start=1):
            for row_index, row in enumerate(table.rows, start=1):
                text = " | ".join(cell.text.strip() for cell in row.cells)
                if text.strip(" |"):
                    segments.append(TextSegment(text, None, 1000 + table_index * 100 + row_index))
        return segments

    def _extract_free_text(self, path: Path, suffix: str, segments: list[TextSegment], payload: dict) -> int:
        term_pattern = self._term_pattern()
        mapped = 0
        for segment in segments:
            line = segment.text.strip()
            if not line:
                continue

            provenance = {
                "source": path.name,
                "format": suffix,
                "page": segment.page,
                "line": segment.line,
            }

            parsed = self._parse_entity(line, provenance)
            if parsed:
                entity_name, row = parsed
                payload["entities"][entity_name].append(row)
            else:
                edge = self._parse_edge(line, provenance)
                if edge is None:
                    if term_pattern.search(line):
                        reason = "Line contains ontology terms but does not match a supported extraction rule"
                        self.mapping_report["unmapped_content"].append(
                            {"source": path.name, "format": suffix, "page": segment.page, "line": segment.line, "text": line, "reason": reason}
                        )
                    continue
                payload["edges"].append(edge)

            mapped += 1
            self.mapping_report["provenance"].append(provenance)

        return mapped

    def _term_pattern(self) -> re.Pattern:
        """Words that mark a line as ontology content: entity/relationship names and IDs like C001."""
        terms = [re.escape(name) for name in self._ontology.entities]
        terms += [re.escape(rel.name) for rel in self._ontology.relationships]
        terms += [rf"{re.escape(name[0])}\d+" for name in self._ontology.entities]
        return re.compile("(?i)(" + "|".join(terms) + ")")

    @staticmethod
    def _keys_for(prop: str) -> list[str]:
        """Labels a document may use for a property: order_date -> order_date, date; customer_id -> customer_id, customer."""
        keys = [prop]
        if prop.endswith("_id"):
            keys.append(prop[:-3])
        elif "_" in prop:
            keys.append(prop.split("_", 1)[1])
        return keys

    def _value(self, line: str, *labels: str) -> str | None:
        for label in labels:
            match = re.search(rf"(?i){re.escape(label)}\s*[=:]\s*([^|;,]+)", line)
            if match:
                return match.group(1).strip()
            match = re.search(rf"(?i)(?:^|[|;,]\s*){re.escape(label)}\s+([^|;,]+)", line)
            if match:
                return match.group(1).strip()
        return None

    def _parse_entity(self, line: str, provenance: dict) -> tuple[str, dict] | None:
        """Parse ``<Entity> <id>: <label> | key=value ...`` for any ontology entity."""
        for name, definition in self._ontology.entities.items():
            if not re.match(rf"(?i)^\s*{re.escape(name)}\b", line):
                continue
            row = self._parse_entity_row(name, definition, line)
            if row is None:
                return None
            row["provenance"] = provenance
            try:
                self._ontology.row_model(name).model_validate(row)
            except ValidationError:
                return None
            return name, row
        return None

    def _parse_entity_row(self, name: str, definition: EntityDefinition, line: str) -> dict | None:
        id_field = definition.id_field
        entity_id = self._value(line, id_field)
        if entity_id is None:
            match = re.search(rf"(?i)^\s*{re.escape(name)}\s*[=:]?\s*({ID_PATTERN})", line)
            entity_id = match.group(1) if match else None
        if entity_id is None:
            return None

        row = {id_field: entity_id}
        other_keys = [key for prop in definition.properties if prop != id_field for key in self._keys_for(prop)]
        key_alternatives = "|".join(re.escape(key) for key in other_keys) or "(?!)"
        refs = definition.refs()

        for prop, spec in definition.properties.items():
            if prop == id_field:
                continue
            value = self._value(line, *self._keys_for(prop))

            if value is None and prop == definition.label_field:
                colon = re.search(rf"(?i)^\s*{re.escape(name)}(?:\s+{ID_PATTERN})?\s*:\s*(.+)$", line)
                if colon and not re.match(rf"(?i)\s*(?:{key_alternatives})\s*[=:]", colon.group(1)):
                    value = re.split(rf"(?i)[|;,]\s*(?:{key_alternatives})\s*[=:]?", colon.group(1))[0].strip()
            if value is None and prop in refs:
                natural = re.search(rf"(?i)\b{re.escape(refs[prop])}\s+({ID_PATTERN})", line[len(name):])
                value = natural.group(1) if natural else None
            if value is None and spec == "date":
                natural = re.search(r"(\d{4}-\d{2}-\d{2})", line)
                value = natural.group(1) if natural else None

            if not value:
                return None
            row[prop] = value.strip()
        return row

    def _parse_edge(self, line: str, provenance: dict) -> dict | None:
        names = {rel.name.upper(): rel.name for rel in self._ontology.relationships}
        if not names:
            return None
        alternatives = "|".join(re.escape(name) for name in names)
        match = re.match(rf"(?i)^\s*({ID_PATTERN})\s+({alternatives})\s+({ID_PATTERN})\s*$", line)
        if not match:
            return None
        return {
            "from": match.group(1),
            "to": match.group(3),
            "relationship": names[match.group(2).upper()],
            "provenance": provenance,
        }
