"""Read structured and free-text document sources for the ontology.

JSON/JSONL support is structured. TXT/MD/PDF/DOCX support uses only explicit,
deterministic text patterns. It does not guess facts or use AI.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .adapter import OntologyDefinition
from .sources import OntologySource, build_ontology_from_payload


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
        self.mapping_report: dict = {
            "supported_formats": [".json", ".jsonl", ".txt", ".md", ".pdf", ".docx"],
            "files": [],
            "mapped_facts": 0,
            "unmapped_content": [],
            "provenance": [],
        }

    def load(self, ontology: OntologyDefinition) -> OntologyDefinition:
        if not self.directory.exists():
            raise FileNotFoundError(f"Document source directory not found: {self.directory}")

        payload = {"customers": [], "orders": [], "products": [], "edges": []}
        files = sorted(
            path
            for path in self.directory.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".json", ".jsonl", ".txt", ".md", ".pdf", ".docx"}
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
        if path.suffix.lower() == ".jsonl":
            with path.open("r", encoding="utf-8") as file:
                documents = [json.loads(line) for line in file if line.strip()]
            provenance_line = None
        else:
            with path.open("r", encoding="utf-8") as file:
                document = json.load(file)
            documents = document if isinstance(document, list) else [document]
            provenance_line = None

        mapped = 0
        for document_index, document in enumerate(documents, start=1):
            if not isinstance(document, dict):
                self.mapping_report["unmapped_content"].append(
                    {"source": path.name, "format": path.suffix.lower(), "line": document_index, "reason": "Document entry is not a JSON object"}
                )
                continue

            facts = document.get("sample_facts", document)
            for collection in ("customers", "orders", "products", "edges"):
                for fact_index, fact in enumerate(facts.get(collection, []), start=1):
                    if not isinstance(fact, dict):
                        self.mapping_report["unmapped_content"].append(
                            {"source": path.name, "format": path.suffix.lower(), "line": document_index, "reason": f"{collection} entry is not an object"}
                        )
                        continue
                    fact = dict(fact)
                    fact["provenance"] = {
                        "source": path.name,
                        "format": path.suffix.lower(),
                        "line": document_index if path.suffix.lower() == ".jsonl" else None,
                        "object": fact_index,
                        "page": None,
                    }
                    payload[collection].append(fact)
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

            fact = self._parse_customer(line, provenance)
            if fact:
                payload["customers"].append(fact)
                mapped += 1
                self.mapping_report["provenance"].append(provenance)
                continue

            fact = self._parse_order(line, provenance)
            if fact:
                payload["orders"].append(fact)
                mapped += 1
                self.mapping_report["provenance"].append(provenance)
                continue

            fact = self._parse_product(line, provenance)
            if fact:
                payload["products"].append(fact)
                mapped += 1
                self.mapping_report["provenance"].append(provenance)
                continue

            edge = self._parse_edge(line, provenance)
            if edge:
                payload["edges"].append(edge)
                mapped += 1
                self.mapping_report["provenance"].append(provenance)
                continue

            if re.search(r"(?i)(customer|order|product|places|contains|C\d+|O\d+|P\d+)", line):
                reason = "Line contains ontology terms but does not match a supported extraction rule"
                self.mapping_report["unmapped_content"].append(
                    {"source": path.name, "format": suffix, "page": segment.page, "line": segment.line, "text": line, "reason": reason}
                )

        return mapped

    def _value(self, line: str, *labels: str) -> str | None:
        for label in labels:
            match = re.search(rf"(?i){re.escape(label)}\s*[=:]\s*([^|;,]+)", line)
            if match:
                return match.group(1).strip()
            match = re.search(rf"(?i)(?:^|[|;,]\s*){re.escape(label)}\s+([^|;,]+)", line)
            if match:
                return match.group(1).strip()
        return None

    def _parse_customer(self, line: str, provenance: dict) -> dict | None:
        if not re.match(r"(?i)^\s*customer", line):
            return None

        match = re.search(r"(?i)customer(?:_id)?\s*[=:]?\s*([A-Za-z0-9_-]+)", line)
        customer_id = match.group(1).strip() if match else None
        name = self._value(line, "name")
        status = self._value(line, "status")

        if name is None:
            colon = re.search(r"(?i)^\s*customer(?:_id)?(?:\s+[A-Za-z0-9_-]+)?\s*:\s*(.+)$", line)
            if colon:
                remainder = colon.group(1)
                remainder = re.split(r"(?i)[|;,]\s*status\s*[=:]?", remainder)[0].strip()
                name = remainder

        if customer_id and name and status:
            return {"customer_id": customer_id, "name": name, "status": status.strip(), "provenance": provenance}
        return None

    def _parse_order(self, line: str, provenance: dict) -> dict | None:
        if not re.match(r"(?i)^\s*order", line):
            return None

        order_match = re.search(r"(?i)order(?:_id)?\s*[=:]?\s*([A-Za-z0-9_-]+)", line)
        order_id = order_match.group(1).strip() if order_match else None
        customer_id = self._value(line, "customer", "customer_id")
        order_date = self._value(line, "date", "order_date")
        status = self._value(line, "status")

        if customer_id is None:
            natural = re.search(r"(?i)^\s*order\s+[A-Za-z0-9_-]+\s*:\s*customer\s+([A-Za-z0-9_-]+)", line)
            if natural:
                customer_id = natural.group(1)
        if order_date is None:
            natural = re.search(r"(\d{4}-\d{2}-\d{2})", line)
            if natural:
                order_date = natural.group(1)

        if order_id and customer_id and order_date and status:
            return {
                "order_id": order_id,
                "customer_id": customer_id,
                "order_date": order_date,
                "status": status.strip(),
                "provenance": provenance,
            }
        return None

    def _parse_product(self, line: str, provenance: dict) -> dict | None:
        if not re.match(r"(?i)^\s*product", line):
            return None

        match = re.search(r"(?i)product(?:_id)?\s*[=:]?\s*([A-Za-z0-9_-]+)", line)
        product_id = match.group(1).strip() if match else None
        name = self._value(line, "name")
        category = self._value(line, "category")
        price = self._value(line, "price")

        if name is None:
            colon = re.search(r"(?i)^\s*product(?:_id)?(?:\s+[A-Za-z0-9_-]+)?\s*:\s*(.+)$", line)
            if colon:
                remainder = colon.group(1)
                remainder = re.split(r"(?i)[|;,]\s*(?:category|price)", remainder)[0].strip()
                name = remainder

        if product_id and name and category and price:
            try:
                float(price)
            except ValueError:
                return None
            return {
                "product_id": product_id,
                "name": name,
                "category": category.strip(),
                "price": price,
                "provenance": provenance,
            }
        return None

    def _parse_edge(self, line: str, provenance: dict) -> dict | None:
        match = re.match(r"(?i)^\s*([A-Za-z0-9_-]+)\s+(PLACES|CONTAINS)\s+([A-Za-z0-9_-]+)\s*$", line)
        if not match:
            return None
        return {
            "from": match.group(1),
            "to": match.group(3),
            "relationship": match.group(2).upper(),
            "provenance": provenance,
        }
