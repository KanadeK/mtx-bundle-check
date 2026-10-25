# mtx-bundle-check

Check a sparse raw-count matrix together with its feature and barcode files before
loading it into an analysis environment. Stream plain or gzipped files, find
nonadjacent duplicate coordinates using a disposable SQLite index, and report the
offending line numbers without loading a dense or sparse analysis matrix.

[中文说明](README.zh-CN.md) · Python 3.11+ · MIT · v0.1.0 · No runtime dependencies

A matrix declaring 20,000 rows and a feature file containing 19,999 labels is not a
usable bundle. A repeated coordinate can also be silently summed by some sparse
conversion paths. This tool provides a strict raw-count preflight with evidence.

## Install and run

```sh
git clone https://github.com/KanadeK/mtx-bundle-check.git
cd mtx-bundle-check
python -m venv .venv
# POSIX: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install .
mtx-bundle-check examples/clean --json
# exit 0: two features, two barcodes, three unique coordinates
mtx-bundle-check examples/duplicate --json
# exit 1: duplicate_coordinate at line 6, first seen at line 4
```

Examples are synthetic fixtures, not biological measurements. Source installation
and GitHub Release wheels are supported; the package is not on PyPI.
Uninstall with `python -m pip uninstall mtx-bundle-check`. Delete the clone or
virtual environment if no longer needed. Inputs are never modified.

## Supported count-bundle profile

Provide a directory with exactly one file for each role:

- `matrix.mtx` or `matrix.mtx.gz`
- `features.tsv` / `features.tsv.gz` (three columns: ID, name, type), **or**
  `genes.tsv` / `genes.tsv.gz` (legacy two columns: ID, name)
- `barcodes.tsv` or `barcodes.tsv.gz` (one column)

Compression can differ between roles. If two alternatives coexist, selection is
ambiguous and fails. Prefixes are explicit: `--prefix sample_` looks for
`sample_matrix.mtx`, `sample_features.tsv`, etc. Plain files are UTF-8; an initial
BOM and CRLF are accepted. TSV has no header or CSV-style quote interpretation.
Sidecar fields must be nonempty; additional annotation columns are unsupported.

The matrix must be Matrix Market `matrix coordinate integer general` or
`matrix coordinate real general`. Only raw counts are accepted: every stored value
must be an exact positive integer <= 2^63−1. Real syntax such as `3.0` or `3e0` is
accepted when integral. Decimal parsing avoids float rounding. Negative, fractional,
out-of-range or explicitly stored zero counts are findings. This is intentionally
stricter than generic Matrix Market; normalized expression matrices are out of scope.

Checks cover:

- The complete entry count, 1-based indices and duplicate coordinates, even when
  duplicates are separated or records are unsorted.
- Feature/barcode counts against the matrix dimensions, duplicate IDs within each
  sidecar, and surrounding identifier whitespace. IDs are never silently trimmed.
- Duplicate **feature names** are permitted; feature IDs must be unique, even
  across types. No particular barcode sequence syntax is imposed.
- Gzip end-of-stream/CRC, UTF-8 decoding, numeric syntax and configurable limits.
  Zero-length axes with matching empty sidecars and no entries are supported;
  this proves structural consistency, not the presence of useful measurements.

Exit codes: **0** complete and no findings; **1** complete with findings;
**2** unsupported/malformed input, incomplete reading, I/O failure or exceeded
resource limit. A plain-text entry-count mismatch is a finding (1); a truncated
gzip stream is an incomplete read (2). JSON errors contain `error`; argparse
usage errors go to stderr.

## Resource bounds and report

```sh
mtx-bundle-check exported/ --tmpdir scratch/ --max-entries 10000000 --json
mtx-bundle-check exported/ --max-findings 20 --max-bytes 1073741824
```

The scratch parent must exist. A private temporary directory contains the index
and is removed when the check finishes or raises an error. Abrupt process death
may leave a directory named `mtx-bundle-*`; remove that directory only after the
process has stopped. The index holds IDs and coordinates, so choose an appropriate
local disk for sensitive data. No network requests or telemetry are made.

Defaults: 50 million entries, 10 million labels per axis/sidecar, 4 GiB aggregate
**decompressed** bytes and 1 MiB per physical line. Oversized actual input also
fails even if its header understates its size. SQLite cache is 8 MiB; index disk
space grows with entries and labels, potentially beyond source-file size. Time
is roughly O(N log N); there is no matrix-sized memory allocation.

JSON schema_version 1 reports dimensions, actual/declared entry counts, unique
in-bounds coordinates, label counts, decompressed bytes and findings. Findings
contain line numbers, matrix coordinates and filenames, not identifier values or
count values. Shape and filenames may still be sensitive. `--max-findings` caps
stored examples only; counts, `ok` and exit status cover the entire scan.

```python
from mtx_bundle_check.core import audit, InputError

report = audit("exported", max_entries=10_000_000)
print(report["ok"], report["counts"])
```

## Limits and alternatives

The inputs must be stable local snapshots. This checks the declared bundle
structure, not biological quality, sample identity or reference annotation.
A same-length **permutation or substitution of sidecar labels can pass**: detecting
that requires provenance or a trusted reference. It cannot establish that two
structurally compatible files belong to the same experiment. No repair, sorting,
summation, HDF5/AnnData reading, symmetric/complex/pattern matrices, archive
extraction or analysis is performed.

[Scanpy read_10x_mtx](https://scanpy.scverse.org/en/stable/generated/scanpy.read_10x_mtx.html)
and Seurat are preferable for actual analysis. SciPy `mmread` is a general sparse
matrix loader. This project adds a strict count-profile gate and cross-file checks
without requiring an analysis stack. Format references:
[10x MEX output](https://www.10xgenomics.com/support/software/cell-ranger/latest/analysis/outputs/cr-outputs-mex-matrices)
and [NIST Matrix Market](https://math.nist.gov/MatrixMarket/formats.html).

## Development

`python -m unittest discover -s tests -v` exercises plain/gzip input, exact integer
semantics, dimensions, nonadjacent duplicates, invalid values, malformed and
truncated files, cleanup, resource limits and CLI outcomes. CI runs Linux,
Windows and macOS on Python 3.11/3.13. See [CONTRIBUTING](CONTRIBUTING.md) and
[SECURITY](SECURITY.md). All implementation and fixtures are original MIT code;
only the Python standard library is used at runtime.
