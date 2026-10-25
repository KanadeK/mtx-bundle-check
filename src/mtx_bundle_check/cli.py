import argparse
import json
import sys

from . import __version__
from .core import InputError, audit


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stream-check a Matrix Market raw count bundle.")
    parser.add_argument("directory")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--tmpdir", help="parent directory for the disposable disk index")
    parser.add_argument("--max-entries", type=int, default=50_000_000)
    parser.add_argument("--max-labels", type=int, default=10_000_000)
    parser.add_argument("--max-bytes", type=int, default=4 * 1024**3)
    parser.add_argument("--max-line-bytes", type=int, default=1024**2)
    parser.add_argument("--max-findings", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    try:
        report = audit(
            args.directory,
            prefix=args.prefix,
            tmpdir=args.tmpdir,
            max_entries=args.max_entries,
            max_labels=args.max_labels,
            max_bytes=args.max_bytes,
            max_line_bytes=args.max_line_bytes,
            max_findings=args.max_findings,
        )
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=True))
        else:
            print(
                f"{'PASS' if report['ok'] else 'FAIL'}: {report['matrix']['rows']} features x "
                f"{report['matrix']['columns']} barcodes; {report['finding_count']} findings"
            )
            for item in report["findings"]:
                print(json.dumps(item, ensure_ascii=True))
            if report["findings_omitted"]:
                print(f"{report['findings_omitted']} more findings omitted")
        return 0 if report["ok"] else 1
    except (InputError, OSError) as exc:
        if args.json:
            print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}))
        else:
            print(f"Input error: {exc}", file=sys.stderr)
        return 2
