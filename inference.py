"""Infer an ontology from files, using plain rules (no AI).

What it can find
  * Tables: CSV files, JSON / JSONL arrays of objects, tables in Word files, and
    table-like text in PDF / TXT / MD files (pipe tables, columns separated by two or
    more spaces or tabs, comma-separated lines, and "Customer C001: Name | status=active"
    record lines).
  * Each table becomes an entity type. From the data we work out:
      - property types (integer, number, boolean, date, datetime, string)
      - the ID column (a column named id / <entity>_id, or a first column with unique values;
        if none exists an ID is generated)
      - references: a column named after another table whose values all exist in that
        table's IDs becomes a reference and a relationship
      - link tables: a table of exactly two reference columns becomes a many-to-many
        relationship instead of an entity
      - rules: two relationships that chain together (A to B to C) become a suggested rule

What it cannot do: read free-form sentences. Prose in a PDF produces no tables, and the
report says so. Scanned (image-only) PDFs have no text to read.
"""

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from models import OntologyDefinition

SUPPORTED_SUFFIXES = {".csv", ".json", ".jsonl", ".docx", ".pdf", ".txt", ".md"}
RESERVED_COLUMNS = {"provenance", "entity_type", "entity_id"}  # names the graph uses itself
RESERVED_COLLECTIONS = {"entities", "edges", "facts", "sample_facts"}


class InferenceError(ValueError):
    def __init__(self, message: str, report: dict | None = None):
        super().__init__(message)
        self.report = report or {}


@dataclass
class Table:
    name: str  # snake_case, e.g. order_items
    sources: list
    columns: list
    rows: list  # list of {column: text}
    entity: str = ""
    id_col: str = ""
    id_note: str = ""
    id_generated: bool = False
    refs: dict = field(default_factory=dict)  # column -> referenced Table
    skipped: int = 0
    dropped: list = field(default_factory=list)


# ---------------------------------------------------------------- naming helpers

def snake(text, fallback="column") -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(text))
    out = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    out = out or fallback
    return "c_" + out if out[0].isdigit() else out


def singular(name: str) -> str:
    if name.endswith("ies") and len(name) > 3:
        return name[:-3] + "y"
    if name.endswith(("ss", "us", "is")):
        return name
    if name.endswith("s") and len(name) > 1:
        return name[:-1]
    return name


def camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part) or "Item"


def cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value).strip()


def make_table(name, headers, raw_rows, source) -> Table:
    columns, seen = [], set()
    for i, header in enumerate(headers, 1):
        col = snake(header, f"column_{i}")
        if col in RESERVED_COLUMNS:
            col += "_"
        base, n = col, 2
        while col in seen:
            col, n = f"{base}_{n}", n + 1
        seen.add(col)
        columns.append(col)
    rows = [{col: cell(value) for col, value in zip(columns, row)} for row in raw_rows]
    return Table(name=name, sources=[source], columns=columns, rows=rows)


def valid_header(cells) -> bool:
    cleaned = [c.strip() for c in cells]
    if not all(cleaned) or len({c.lower() for c in cleaned}) != len(cleaned):
        return False
    return not any(re.fullmatch(r"[-+]?\d+([.,]\d+)?|\d{4}-\d{2}-\d{2}", c) for c in cleaned)


def heading_name(text) -> str | None:
    if not text or len(text) > 60 or len(text.split()) > 6 or text.endswith((".", "?", "!")):
        return None
    text = re.sub(r"(?i)^(table|figure)\s*\d*\s*[:.\-]?\s*", "", text.strip()).rstrip(":").strip()
    return snake(text) if text else None


def table_from_cells(rows, heading, stem, source, index, warnings):
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        return []
    if not valid_header(rows[0]):
        warnings.append(f"{source}: skipped a table whose first row does not look like column names")
        return []
    name = heading_name(heading) or (stem if index == 1 else f"{stem}_{index}")
    return [make_table(name, rows[0], rows[1:], source)]


# ---------------------------------------------------------------- readers

def read_csv_file(path: Path, warnings):
    text = path.read_text(encoding="utf-8-sig")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if len(rows) < 2:
        return []
    header, body = rows[0], rows[1:]
    good = [r for r in body if len(r) == len(header)]
    if len(good) != len(body):
        warnings.append(f"{path.name}: skipped {len(body) - len(good)} row(s) with the wrong number of columns")
    return [make_table(snake(path.stem), header, good, path.name)]


def tables_from_json(data, name, source):
    if isinstance(data, dict):
        data = data.get("facts", data.get("sample_facts", data))
        found = []
        for key, value in data.items():
            if key != "edges" and isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
                found += tables_from_json(value, snake(key), source)
        if not found and data and not any(isinstance(v, (dict, list)) for v in data.values()):
            found = tables_from_json([data], name, source)
        return found
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        headers = []
        for row in data:
            headers += [k for k in row if k not in headers]
        return [make_table(name, headers, [[row.get(h) for h in headers] for row in data], source)]
    return []


def read_json_file(path: Path, warnings):
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        data = json.loads(text)
    return tables_from_json(data, snake(path.stem), path.name)


def read_docx_file(path: Path, warnings):
    from docx import Document
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    tables, heading, index = [], None, 0
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(child, document).text.strip()
            if text:
                heading = text
        elif tag == "tbl":
            index += 1
            rows = [[c.text.strip() for c in row.cells] for row in DocxTable(child, document).rows]
            tables += table_from_cells(rows, heading, snake(path.stem), path.name, index, warnings)
            heading = None
    return tables


def read_pdf_lines(path: Path) -> list:
    from pypdf import PdfReader

    lines = []
    for page in PdfReader(str(path)).pages:
        try:
            text = page.extract_text(extraction_mode="layout")  # keeps column spacing
        except TypeError:
            text = page.extract_text()
        lines += (text or "").splitlines()
    return lines


RECORD = re.compile(r"^([A-Za-z][A-Za-z0-9 ]{0,30}?)\s+([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)\s*:\s*(.+)$")
KEY_VALUE = re.compile(r"^([A-Za-z_][A-Za-z0-9_ ]*?)\s*=\s*(.+)$")


def parse_record(line):
    """'Customer C001: Avery | status=active' -> ('Customer', [('Customer id','C001'), ...])."""
    match = RECORD.match(line)
    if not match:
        return None
    entity, record_id, rest = match.groups()
    pairs, label = [], None
    for k, part in enumerate(p.strip() for p in re.split(r"[|;]", rest) if p.strip()):
        kv = KEY_VALUE.match(part)
        if kv:
            pairs.append((kv.group(1).strip(), kv.group(2).strip()))
        elif k == 0:
            label = part
        else:
            return None  # stray text, so this is probably a sentence
    if not pairs:
        return None
    fields = [(f"{entity.strip()} id", record_id)] + ([("name", label)] if label else []) + pairs
    return entity.strip(), fields


def split_pipe(line):
    if "|" not in line:
        return None
    if re.fullmatch(r"[\s|:\-+]+", line):
        return "SEP"
    return [c.strip() for c in line.strip().strip("|").split("|")]


def split_spaces(line):
    cells = [c for c in re.split(r"\t+|\s{2,}", line.strip()) if c]
    return cells if len(cells) > 1 else None


def split_commas(line):
    if "," not in line:
        return None
    return [c.strip() for c in next(csv.reader([line]))]


def find_runs(cells_per_line, min_cols, min_lines):
    """Runs of consecutive lines that split into the same number of cells."""
    runs, i, n = [], 0, len(cells_per_line)
    while i < n:
        first = cells_per_line[i]
        if not isinstance(first, list) or len(first) < min_cols:
            i += 1
            continue
        rows, j = [first], i + 1
        while j < n:
            nxt = cells_per_line[j]
            if nxt == "SEP":
                j += 1
            elif isinstance(nxt, list) and len(nxt) == len(first):
                rows.append(nxt)
                j += 1
            else:
                break
        if len(rows) >= min_lines:
            runs.append((i, j, rows))
        i = max(j, i + 1)
    return runs


def tables_from_text(lines, stem, source, warnings):
    lines = [line.rstrip() for line in lines]
    used = set()
    tables = []

    # 1. "Customer C001: Avery Morgan | status=active" record lines, grouped by entity word
    records = {}
    for i, line in enumerate(lines):
        parsed = parse_record(line.strip())
        if parsed:
            entity, fields = parsed
            records.setdefault(snake(entity), []).append(dict(fields))
            used.add(i)
    for name, rows in records.items():
        headers = []
        for row in rows:
            headers += [k for k in row if k not in headers]
        tables.append(make_table(name, headers, [[row.get(h) for h in headers] for row in rows], source))

    # 2. tables laid out with pipes, runs of spaces / tabs, or commas
    index = 0
    for splitter, min_cols, min_lines in ((split_pipe, 2, 3), (split_spaces, 2, 3), (split_commas, 3, 4)):
        cells = [None if (not line.strip() or i in used) else splitter(line) for i, line in enumerate(lines)]
        for start, end, rows in find_runs(cells, min_cols, min_lines):
            used.update(range(start, end))
            heading = next((lines[k].strip() for k in range(start - 1, max(start - 4, -1), -1)
                            if lines[k].strip() and k not in used), None)
            index += 1
            tables += table_from_cells(rows, heading, stem, source, index, warnings)
    return tables


def read_tables(path: Path, warnings) -> list:
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            return read_csv_file(path, warnings)
        if suffix in {".json", ".jsonl"}:
            return read_json_file(path, warnings)
        if suffix == ".docx":
            return read_docx_file(path, warnings)
        if suffix == ".pdf":
            return tables_from_text(read_pdf_lines(path), snake(path.stem), path.name, warnings)
        if suffix in {".txt", ".md"}:
            return tables_from_text(path.read_text(encoding="utf-8-sig").splitlines(), snake(path.stem), path.name, warnings)
    except Exception as exc:  # one unreadable file should not stop the others
        warnings.append(f"{path.name}: could not be read ({exc})")
        return []
    warnings.append(f"{path.name}: unsupported file type")
    return []


# ---------------------------------------------------------------- schema inference

def infer_type(values) -> str:
    if values and all(v in ("true", "false") for v in values):
        return "boolean"
    if values and all(re.fullmatch(r"-?(0|[1-9]\d*)", v) for v in values):
        return "integer"
    if values and all(re.fullmatch(r"-?\d+(\.\d+)?", v) for v in values) and any("." in v for v in values):
        return "number"
    if values and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in values):
        try:
            for v in values:
                date.fromisoformat(v)
            return "date"
        except ValueError:
            pass
    if values and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}.*", v) for v in values):
        try:
            for v in values:
                datetime.fromisoformat(v)
            return "datetime"
        except ValueError:
            pass
    return "string"


def convert(value: str, type_name: str):
    if type_name == "integer":
        return int(value)
    if type_name == "number":
        return float(value)
    if type_name == "boolean":
        return value == "true"
    return value


def merge_tables(tables, warnings) -> list:
    """Combine tables that share a name (the same data often appears in several files).

    * Same columns: identical rows are only kept once.
    * Different columns, but the same unique first column (the key): rows with the same key
      are joined and the columns are combined.
    * Anything else is two different tables, so the second is renamed with a numeric suffix.
    """
    merged = {}
    for table in tables:
        existing = merged.get(table.name)
        if existing is None:
            merged[table.name] = table
            continue

        if set(existing.columns) == set(table.columns):
            seen = {tuple(r[c] for c in existing.columns) for r in existing.rows}
            for row in table.rows:
                values = tuple(row[c] for c in existing.columns)
                if values not in seen:
                    seen.add(values)
                    existing.rows.append(row)
        elif combine_by_key(existing, table, warnings):
            pass
        else:
            n = 2
            while f"{table.name}_{n}" in merged:
                n += 1
            warnings.append(f"Two different tables are called '{table.name}'; the second was renamed '{table.name}_{n}'")
            table.name = f"{table.name}_{n}"
            merged[table.name] = table
            continue
        existing.sources += [s for s in table.sources if s not in existing.sources]
    return list(merged.values())


def combine_by_key(existing, table, warnings) -> bool:
    key = existing.columns[0]
    if key not in table.columns or not is_unique(existing, key) or not is_unique(table, key):
        return False
    for col in table.columns:
        if col not in existing.columns:
            existing.columns.append(col)
    by_key = {row[key]: row for row in existing.rows}
    for row in table.rows:
        target = by_key.get(row[key])
        if target is None:
            target = by_key[row[key]] = {}
            existing.rows.append(target)
        for col, value in row.items():
            if value and not target.get(col):
                target[col] = value
            elif value and target[col] != value:
                warnings.append(f"{existing.name}: '{col}' differs for {row[key]} between files; kept the first value")
    for row in existing.rows:
        for col in existing.columns:
            row.setdefault(col, "")
    warnings.append(f"{existing.name}: found in several files with different columns; combined them using '{key}'")
    return True


def clean_table(table, warnings):
    total = len(table.rows)
    for col in list(table.columns):
        if total and sum(1 for r in table.rows if not r[col]) > total / 2:
            table.columns.remove(col)
            table.dropped.append(col)
            warnings.append(f"{table.name}: column '{col}' is mostly empty, so it was left out")
    kept = [r for r in table.rows if all(r[c] for c in table.columns)]
    table.skipped = total - len(kept)
    if table.skipped:
        warnings.append(f"{table.name}: skipped {table.skipped} row(s) with empty cells")
    table.rows = kept
    if not kept or not table.columns:
        warnings.append(f"{table.name}: no usable rows, so it was left out")
        return None
    return table


def is_unique(table, col) -> bool:
    values = [r[col] for r in table.rows]
    return len(set(values)) == len(values)


def choose_id(table, warnings):
    singular_name = singular(table.name)
    for col in ("id", f"{singular_name}_id", f"{table.name}_id"):
        if col in table.columns and is_unique(table, col):
            table.id_col, table.id_note = col, f"column '{col}'"
            return
    if is_unique(table, table.columns[0]):
        table.id_col, table.id_note = table.columns[0], f"first column '{table.columns[0]}' (all values unique)"
        return
    col = f"{singular_name}_id"
    while col in table.columns:
        col += "_"
    for n, row in enumerate(table.rows, 1):
        row[col] = f"{singular_name}-{n}"
    table.columns.insert(0, col)
    table.id_col, table.id_note, table.id_generated = col, "generated, because no column had unique values", True
    warnings.append(f"{table.name}: no unique ID column was found, so IDs were generated")


def name_matches(col, target) -> list:
    """Names a foreign-key column could have for `target`, best first."""
    candidates = [target.id_col, singular(target.name) + "_id", singular(target.name), target.name + "_id"]
    return [c for c in candidates if c and (col == c or col.endswith("_" + c))]


def find_reference(table, col, targets, ids, warnings):
    near_miss = None
    for target in targets:
        if target is table or not name_matches(col, target):
            continue
        values = {r[col] for r in table.rows}
        if values <= ids[target.name]:
            return target
        near_miss = near_miss or (target, len(values - ids[target.name]))
    if near_miss:
        warnings.append(f"{table.name}.{col} looks like a reference to {near_miss[0].name}, but "
                        f"{near_miss[1]} value(s) do not exist there, so it was kept as plain text")
    return None


def unique_name(name, used) -> str:
    candidate, n = name, 2
    while candidate in used:
        candidate, n = f"{name}_{n}", n + 1
    used.add(candidate)
    return candidate


def infer_ontology(files, name="Imported ontology") -> dict:
    """Return {"ontology": <definition with facts>, "report": {...}}. Raises InferenceError."""
    warnings, file_report, tables = [], [], []
    for path in files:
        found = read_tables(Path(path), warnings)
        file_report.append({"file": Path(path).name, "tables": [t.name for t in found]})
        if not found and not any(Path(path).name in w for w in warnings):
            warnings.append(f"{Path(path).name}: no tables or record lines found (prose cannot be read without AI)")
        tables += found

    tables = [t for t in (clean_table(t, warnings) for t in merge_tables(tables, warnings)) if t]
    if not tables:
        raise InferenceError("No usable tables were found in the uploaded files.",
                             {"files": file_report, "warnings": warnings})

    used_names = set()
    for table in tables:
        table.entity = unique_name(camel(singular(table.name)), used_names)

    # A table of exactly two columns that both look like references is a link table.
    def looks_like(col, other):
        s = singular(other.name)
        return any(col == c or col.endswith("_" + c) for c in (s, s + "_id", other.name + "_id"))

    joins = []
    for table in tables:
        if len(table.columns) == 2:
            hits = [next((o for o in tables if o is not table and looks_like(col, o)), None) for col in table.columns]
            if all(hits):
                joins.append((table, hits[0], hits[1]))
    link_names = {j[0].name for j in joins}
    joins = [j for j in joins if j[1].name not in link_names and j[2].name not in link_names]
    link_names = {j[0].name for j in joins}
    entity_tables = [t for t in tables if t.name not in link_names]
    for table in entity_tables:
        choose_id(table, warnings)
    ids = {t.name: {r[t.id_col] for r in t.rows} for t in entity_tables}

    confirmed = []
    for link, a, b in joins:
        first, second = link.columns
        if {r[first] for r in link.rows} <= ids[a.name] and {r[second] for r in link.rows} <= ids[b.name]:
            confirmed.append((link, a, b))
        else:
            warnings.append(f"{link.name} looks like a link table, but some values match nothing, so it was imported as its own entity")
            choose_id(link, warnings)
            entity_tables.append(link)
            ids[link.name] = {r[link.id_col] for r in link.rows}

    for table in entity_tables:
        for col in table.columns:
            if col != table.id_col:
                target = find_reference(table, col, entity_tables, ids, warnings)
                if target:
                    table.refs[col] = target

    # IDs must be unique across all entities, so repeated IDs (1, 2, 3 in every table) get a prefix.
    all_ids = [i for t in entity_tables for i in ids[t.name]]
    collision = len(all_ids) != len(set(all_ids))
    # (generated IDs already start with the entity name, so they are left alone)
    prefix = {t.name: (snake(singular(t.name)) + "-" if collision and not t.id_generated else "") for t in entity_tables}
    if collision:
        warnings.append("Some IDs repeat across tables, so every ID was prefixed with its entity name (for example customer-1)")

    entities, facts_entities, relationships, edge_keys, edges = {}, {}, [], set(), []
    used_rels, used_collections = set(), set()

    def add_edge(source_id, target_id, relationship):
        if (source_id, target_id, relationship) not in edge_keys:
            edge_keys.add((source_id, target_id, relationship))
            edges.append({"from": source_id, "to": target_id, "relationship": relationship})

    for table in entity_tables:
        props = {}
        for col in table.columns:
            if col == table.id_col:
                props[col] = "string"
            elif col in table.refs:
                props[col] = f"ref:{table.refs[col].entity}"
            else:
                props[col] = infer_type([r[col] for r in table.rows])
        collection = table.name + "_data" if table.name in RESERVED_COLLECTIONS else table.name
        entities[table.entity] = {
            "description": f"Imported from {', '.join(table.sources)}.",
            "properties": props,
            "id_field": table.id_col,
            "collection": unique_name(collection, used_collections),
            "table": table.name,
        }
        rows_out = []
        for r in table.rows:
            row = {}
            for col in table.columns:
                if col == table.id_col:
                    row[col] = prefix[table.name] + r[col]
                elif col in table.refs:
                    row[col] = prefix[table.refs[col].name] + r[col]
                else:
                    row[col] = convert(r[col], props[col])
            rows_out.append(row)
        facts_entities[table.entity] = rows_out

    for table in entity_tables:
        for col, target in table.refs.items():
            rel = unique_name(f"HAS_{snake(table.entity).upper()}", used_rels)
            relationships.append({"name": rel, "from": target.entity, "to": table.entity,
                                  "description": f"A {target.entity} is linked to {table.entity} records through {table.name}.{col}."})
            for r in table.rows:
                add_edge(prefix[target.name] + r[col], prefix[table.name] + r[table.id_col], rel)

    for link, a, b in confirmed:
        first, second = link.columns
        rel = unique_name(f"HAS_{snake(b.entity).upper()}", used_rels)
        relationships.append({"name": rel, "from": a.entity, "to": b.entity,
                              "description": f"{a.entity} is linked to {b.entity} through the {link.name} table.",
                              "source": {"table": link.name, "from_column": first, "to_column": second}})
        for r in link.rows:
            add_edge(prefix[a.name] + r[first], prefix[b.name] + r[second], rel)

    rules, used_rules = [], set()
    for r1 in relationships:
        for r2 in relationships:
            if r1 is not r2 and r1["to"] == r2["from"] and r1["from"] != r2["to"] and len(rules) < 6:
                rule = unique_name(f"{snake(r1['from']).upper()}_{snake(r2['to']).upper()}", used_rules)
                rules.append({"name": rule, "path": [r1["name"], r2["name"]],
                              "description": f"{r1['from']} {r1['name']} {r1['to']} {r2['name']} {r2['to']} => "
                                             f"{r1['from']} {rule} {r2['to']}"})

    definition = {"ontology_name": name, "ontology_version": "1", "entities": entities,
                  "relationships": relationships, "rules": rules}
    facts = {"entities": facts_entities, "edges": edges}

    report = {
        "files": file_report,
        "entities": [
            {
                "entity": t.entity, "table": t.name, "sources": t.sources, "rows": len(t.rows),
                "skipped_rows": t.skipped, "id_field": t.id_col, "id_note": t.id_note,
                "properties": [{"name": c, "type": entities[t.entity]["properties"][c]} for c in t.columns],
            }
            for t in entity_tables
        ],
        "relationships": [
            {"name": r["name"], "from": r["from"], "to": r["to"],
             "how": ("link table " + r["source"]["table"]) if "source" in r else "reference column"}
            for r in relationships
        ],
        "rules": [{"name": r["name"], "path": r["path"]} for r in rules],
        "edge_count": len(edges),
        "warnings": warnings,
    }

    try:
        OntologyDefinition.model_validate(definition).with_facts(facts, strict=True)
    except ValueError as exc:
        raise InferenceError(f"The inferred ontology failed validation: {exc}", report) from exc

    return {"ontology": {**definition, "facts": facts}, "report": report}
