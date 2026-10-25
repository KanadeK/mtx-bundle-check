"""Bounded line parsing and disk-backed integrity checks for count bundles."""

import gzip
import re
import sqlite3
import tempfile
import zlib
from collections import Counter
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

MAX_INT = 2**63 - 1
INTEGER = re.compile(r"[+-]?[0-9]+\Z")
REAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?\Z")


class InputError(ValueError):
    """The input is unsupported, malformed or exceeds a declared resource cap."""


class Budget:
    def __init__(self, max_bytes, max_line_bytes):
        self.max_bytes = max_bytes
        self.max_line_bytes = max_line_bytes
        self.used = 0

    def lines(self, path):
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rb") as stream:
                line_number = 0
                while True:
                    raw = stream.readline(self.max_line_bytes + 1)
                    if not raw:
                        break
                    line_number += 1
                    self.used += len(raw)
                    if len(raw) > self.max_line_bytes or self.used > self.max_bytes:
                        raise InputError("decompressed byte or line limit exceeded")
                    line = raw.decode("utf-8-sig" if line_number == 1 else "utf-8").rstrip("\r\n")
                    if "\x00" in line:
                        raise InputError(f"{path.name}:{line_number}: NUL byte is unsupported")
                    yield line_number, line
        except (OSError, EOFError, UnicodeError, zlib.error) as exc:
            raise InputError(f"{path.name}: cannot read complete UTF-8 input: {exc}") from exc


class Findings:
    def __init__(self, limit):
        self.limit = limit
        self.counts = Counter()
        self.items = []

    def add(self, code, **location):
        self.counts[code] += 1
        if len(self.items) < self.limit:
            self.items.append({"code": code, **location})


def integer(text, context):
    if len(text) > 20 or not INTEGER.fullmatch(text):
        raise InputError(f"{context}: expected an integer with at most 20 characters")
    value = int(text)
    if not -MAX_INT <= value <= MAX_INT:
        raise InputError(f"{context}: integer is outside supported 64-bit range")
    return value


def discover(directory, prefix):
    root = Path(directory)
    if not root.is_dir() or not re.fullmatch(r"[A-Za-z0-9_.-]*", prefix):
        raise InputError("expected a local directory and a filename prefix without separators")

    def one(names):
        candidates = [root / (prefix + name + suffix) for name in names for suffix in ("", ".gz")]
        present = [path for path in candidates if path.exists()]
        if len(present) != 1 or not present[0].is_file():
            raise InputError(f"expected exactly one regular file for {names}; found {len(present)}")
        return present[0]

    return one(["matrix.mtx"]), one(["features.tsv", "genes.tsv"]), one(["barcodes.tsv"])


def sidecar(path, kind, width, budget, db, findings, max_labels):
    count = 0
    with closing(budget.lines(path)) as lines:
        for number, line in lines:
            count += 1
            if count > max_labels:
                raise InputError("max_labels exceeded")
            parts = line.split("\t")
            if len(parts) != width or any(not part.strip() for part in parts):
                raise InputError(
                    f"{path.name}:{number}: expected {width} nonempty tab-separated fields"
                )
            key = parts[0]
            if key != key.strip():
                findings.add("identifier_whitespace", file=path.name, line=number, kind=kind)
            inserted = db.execute(
                "INSERT OR IGNORE INTO labels VALUES (?, ?, ?)", (kind, key, number)
            )
            if inserted.rowcount == 0:
                first = db.execute(
                    "SELECT line FROM labels WHERE kind=? AND value=?", (kind, key)
                ).fetchone()[0]
                findings.add(
                    "duplicate_identifier", file=path.name, line=number, first_line=first, kind=kind
                )
    return count


def matrix(path, budget, db, findings, max_entries, max_labels):
    lines = budget.lines(path)
    try:
        try:
            _, header = next(lines)
        except StopIteration as exc:
            raise InputError("matrix is empty") from exc
        parts = header.split()
        if (
            len(parts) != 5
            or parts[:3] != ["%%MatrixMarket", "matrix", "coordinate"]
            or parts[3] not in ("integer", "real")
            or parts[4] != "general"
        ):
            raise InputError(
                "supported Matrix Market types: matrix coordinate integer/real general"
            )
        field = parts[3]
        dimensions = None
        for _number, line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("%"):
                dimensions = stripped.split()
                break
        if dimensions is None or len(dimensions) != 3:
            raise InputError("missing or malformed matrix dimension line")
        rows, columns, declared = [integer(value, "dimensions") for value in dimensions]
        if min(rows, columns, declared) < 0:
            raise InputError("matrix dimensions and entry count must be nonnegative")
        if declared > max_entries or max(rows, columns) > max_labels:
            raise InputError("declared dimensions exceed max_entries or max_labels")
        entries = 0
        for number, line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("%"):
                continue
            entries += 1
            if entries > max_entries:
                raise InputError("actual record count exceeds max_entries")
            tokens = stripped.split()
            if len(tokens) != 3:
                raise InputError(f"{path.name}:{number}: expected row, column, value")
            row, col = [integer(token, f"{path.name}:{number}") for token in tokens[:2]]
            location = {"file": path.name, "line": number, "row": row, "column": col}
            valid_index = 1 <= row <= rows and 1 <= col <= columns
            if not valid_index:
                findings.add("index_out_of_bounds", **location)
            text = tokens[2]
            pattern = INTEGER if field == "integer" else REAL
            if len(text) > 128 or not pattern.fullmatch(text):
                raise InputError(f"{path.name}:{number}: invalid finite {field} value")
            try:
                value = Decimal(text)
            except InvalidOperation as exc:
                raise InputError(f"{path.name}:{number}: invalid value") from exc
            if value < 0 or value > MAX_INT:
                findings.add("count_out_of_range", **location)
            elif value != value.to_integral_value():
                findings.add("fractional_count", **location)
            elif value == 0:
                findings.add("explicit_zero", **location)
            if valid_index:
                inserted = db.execute(
                    "INSERT OR IGNORE INTO entries VALUES (?, ?, ?)", (row, col, number)
                )
                if inserted.rowcount == 0:
                    first = db.execute(
                        "SELECT line FROM entries WHERE r=? AND c=?", (row, col)
                    ).fetchone()[0]
                    findings.add("duplicate_coordinate", **location, first_line=first)
        if entries != declared:
            findings.add("entry_count_mismatch", file=path.name, declared=declared, actual=entries)
        return rows, columns, declared, entries
    finally:
        lines.close()


def audit(
    directory,
    *,
    prefix="",
    tmpdir=None,
    max_entries=50_000_000,
    max_labels=10_000_000,
    max_bytes=4 * 1024**3,
    max_line_bytes=1024**2,
    max_findings=100,
):
    """Check a raw nonnegative count bundle, not an arbitrary numerical matrix.

    IDs are compared exactly, with no trimming or case folding. SQLite indexes
    detect nonadjacent duplicates; the input matrix is never materialized.
    """
    for name, value in [
        ("max_entries", max_entries),
        ("max_labels", max_labels),
        ("max_bytes", max_bytes),
        ("max_line_bytes", max_line_bytes),
    ]:
        if not isinstance(value, int) or value < 1:
            raise InputError(f"{name} must be a positive integer")
    if not isinstance(max_findings, int) or max_findings < 0:
        raise InputError("max_findings must be nonnegative")
    matrix_path, features_path, barcode_path = discover(directory, prefix)
    budget = Budget(max_bytes, max_line_bytes)
    findings = Findings(max_findings)
    try:
        with tempfile.TemporaryDirectory(prefix="mtx-bundle-", dir=tmpdir) as temp:
            with closing(sqlite3.connect(str(Path(temp) / "index.sqlite3"))) as db:
                db.execute("PRAGMA journal_mode=OFF")
                db.execute("PRAGMA synchronous=OFF")
                db.execute("PRAGMA cache_size=-8192")
                db.execute("PRAGMA temp_store=FILE")
                db.execute(
                    "CREATE TABLE entries (r INTEGER, c INTEGER, line INTEGER, "
                    "PRIMARY KEY(r,c)) WITHOUT ROWID"
                )
                db.execute(
                    "CREATE TABLE labels (kind TEXT, value TEXT, line INTEGER, "
                    "PRIMARY KEY(kind,value)) WITHOUT ROWID"
                )
                rows, cols, declared, actual = matrix(
                    matrix_path, budget, db, findings, max_entries, max_labels
                )
                width = (
                    2
                    if features_path.name in (prefix + "genes.tsv", prefix + "genes.tsv.gz")
                    else 3
                )
                features = sidecar(
                    features_path, "feature", width, budget, db, findings, max_labels
                )
                barcodes = sidecar(barcode_path, "barcode", 1, budget, db, findings, max_labels)
                if rows != features:
                    findings.add(
                        "feature_count_mismatch", matrix_rows=rows, feature_records=features
                    )
                if cols != barcodes:
                    findings.add(
                        "barcode_count_mismatch", matrix_columns=cols, barcode_records=barcodes
                    )
                unique = db.execute("SELECT count(*) FROM entries").fetchone()[0]
    except (OSError, sqlite3.Error) as exc:
        raise InputError(f"temporary index failed: {exc}") from exc
    count = sum(findings.counts.values())
    return {
        "schema_version": 1,
        "ok": count == 0,
        "matrix": {
            "rows": rows,
            "columns": cols,
            "declared_entries": declared,
            "actual_entries": actual,
            "unique_in_bounds_coordinates": unique,
        },
        "features": features,
        "barcodes": barcodes,
        "decompressed_bytes": budget.used,
        "finding_count": count,
        "counts": dict(sorted(findings.counts.items())),
        "findings": findings.items,
        "findings_omitted": count - len(findings.items),
    }
