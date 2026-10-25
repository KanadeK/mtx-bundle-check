import contextlib
import gzip
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mtx_bundle_check.cli import main
from mtx_bundle_check.core import InputError, audit


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()

    def write(self, name, text, compressed=False):
        path = self.bundle / (name + (".gz" if compressed else ""))
        raw = text.encode("utf-8") if isinstance(text, str) else text
        path.write_bytes(gzip.compress(raw) if compressed else raw)
        return path

    def fixture(self, records="1 1 2\n2 2 3\n", shape="2 2 2", field="integer", compressed=False):
        self.write(
            "matrix.mtx",
            f"%%MatrixMarket matrix coordinate {field} general\n% synthetic\n{shape}\n{records}",
            compressed,
        )
        self.write(
            "features.tsv", "g1\tGene A\tGene Expression\ng2\tGene B\tGene Expression\n", compressed
        )
        self.write("barcodes.tsv", "bc1\nbc2\n", compressed)

    def check(self, **kwargs):
        return audit(self.bundle, tmpdir=self.scratch, **kwargs)

    def test_plain_clean(self):
        self.fixture()
        result = self.check()
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["matrix"],
            {
                "rows": 2,
                "columns": 2,
                "declared_entries": 2,
                "actual_entries": 2,
                "unique_in_bounds_coordinates": 2,
            },
        )

    def test_gzip_clean_and_temp_cleanup(self):
        self.fixture(compressed=True)
        self.assertTrue(self.check()["ok"])
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_real_integral_counts(self):
        self.fixture("1 1 2.0\n2 2 3e0\n", field="real")
        self.assertTrue(self.check()["ok"])

    def test_duplicate_nonadjacent_coordinates(self):
        self.fixture("1 1 2\n2 2 3\n1 1 5\n", shape="2 2 3")
        result = self.check()
        self.assertEqual(result["counts"], {"duplicate_coordinate": 1})
        self.assertEqual(result["findings"][0]["first_line"], 4)
        self.assertEqual(result["findings"][0]["line"], 6)

    def test_truncated_matrix_count(self):
        self.fixture("1 1 2\n")
        self.assertEqual(self.check()["counts"], {"entry_count_mismatch": 1})

    def test_trailing_extra_record(self):
        self.fixture("1 1 2\n2 2 3\n1 2 1\n")
        self.assertEqual(self.check()["counts"], {"entry_count_mismatch": 1})

    def test_out_of_bounds_and_zero_index(self):
        self.fixture("0 1 2\n3 2 3\n")
        self.assertEqual(self.check()["counts"], {"index_out_of_bounds": 2})

    def test_negative_count(self):
        self.fixture("1 1 -1\n2 2 3\n")
        self.assertEqual(self.check()["counts"], {"count_out_of_range": 1})

    def test_fractional_count(self):
        self.fixture("1 1 1.25\n2 2 3\n", field="real")
        self.assertEqual(self.check()["counts"], {"fractional_count": 1})

    def test_explicit_zero(self):
        self.fixture("1 1 0\n2 2 3\n")
        self.assertEqual(self.check()["counts"], {"explicit_zero": 1})

    def test_huge_exponent_without_allocating_expanded_number(self):
        self.fixture("1 1 1e999999\n2 2 3\n", field="real")
        self.assertEqual(self.check()["counts"], {"count_out_of_range": 1})

    def test_exact_large_integer_no_float_rounding(self):
        self.fixture("1 1 9007199254740993\n2 2 9223372036854775807\n")
        self.assertTrue(self.check()["ok"])

    def test_fractional_beyond_float_precision(self):
        self.fixture("1 1 9007199254740993.1\n2 2 3\n", field="real")
        self.assertEqual(self.check()["counts"], {"fractional_count": 1})

    def test_invalid_numeric_syntax(self):
        for token in ["nan", "inf", "1_000", "1.5", "x"]:
            with self.subTest(token=token), self.assertRaises(InputError):
                self.fixture(f"1 1 {token}\n2 2 3\n")
                self.check()

    def test_unsupported_matrix_type(self):
        self.fixture(field="complex")
        with self.assertRaisesRegex(InputError, "supported"):
            self.check()

    def test_wrong_record_width(self):
        self.fixture("1 1 2 extra\n2 2 3\n")
        with self.assertRaises(InputError):
            self.check()

    def test_negative_dimensions(self):
        self.fixture(shape="-1 2 2")
        with self.assertRaises(InputError):
            self.check()

    def test_missing_dimensions(self):
        self.fixture()
        self.write(
            "matrix.mtx", "%%MatrixMarket matrix coordinate integer general\n% only comment\n"
        )
        with self.assertRaises(InputError):
            self.check()

    def test_duplicate_feature_id_not_symbol(self):
        self.fixture()
        self.write("features.tsv", "g1\tSame\tGene Expression\ng2\tSame\tGene Expression\n")
        self.assertTrue(self.check()["ok"])
        self.write("features.tsv", "g1\tA\tGene Expression\ng1\tB\tAntibody Capture\n")
        self.assertEqual(self.check()["counts"], {"duplicate_identifier": 1})

    def test_duplicate_barcodes_and_privacy(self):
        self.fixture()
        self.write("barcodes.tsv", "private-barcode\nprivate-barcode\n")
        result = self.check()
        self.assertEqual(result["counts"], {"duplicate_identifier": 1})
        self.assertNotIn("private-barcode", json.dumps(result))

    def test_feature_dimension_mismatch(self):
        self.fixture()
        self.write("features.tsv", "g1\tA\tGene Expression\n")
        self.assertEqual(self.check()["counts"], {"feature_count_mismatch": 1})

    def test_barcode_dimension_mismatch(self):
        self.fixture()
        self.write("barcodes.tsv", "bc1\n")
        self.assertEqual(self.check()["counts"], {"barcode_count_mismatch": 1})

    def test_legacy_genes(self):
        self.fixture()
        (self.bundle / "features.tsv").unlink()
        self.write("genes.tsv", "g1\tA\ng2\tB\n")
        self.assertTrue(self.check()["ok"])

    def test_ambiguous_inputs_rejected(self):
        self.fixture()
        self.write("matrix.mtx", "anything", compressed=True)
        with self.assertRaisesRegex(InputError, "exactly one"):
            self.check()

    def test_malformed_sidecars(self):
        self.fixture()
        for value in ["bc1\textra\n", "\n", "  \n"]:
            with self.subTest(value=value), self.assertRaises(InputError):
                self.write("barcodes.tsv", value)
                self.check()

    def test_identifier_whitespace_not_silently_trimmed(self):
        self.fixture()
        self.write("barcodes.tsv", "bc1\n bc1\n")
        self.assertEqual(self.check()["counts"], {"identifier_whitespace": 1})

    def test_utf8_bom_and_crlf(self):
        self.fixture()
        self.write("barcodes.tsv", "\ufeff样本1\r\n样本2\r\n")
        self.assertTrue(self.check()["ok"])

    def test_bad_encoding_and_nul(self):
        self.fixture()
        for value in [b"\xff\n", b"bc\x00\n"]:
            with self.subTest(value=value), self.assertRaises(InputError):
                self.write("barcodes.tsv", value)
                self.check()

    def test_truncated_gzip_detected_even_if_payload_complete(self):
        self.fixture(compressed=True)
        path = self.bundle / "barcodes.tsv.gz"
        path.write_bytes(path.read_bytes()[:-4])
        with self.assertRaises(InputError):
            self.check()
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_gzip_crc_corruption(self):
        self.fixture(compressed=True)
        path = self.bundle / "matrix.mtx.gz"
        value = bytearray(path.read_bytes())
        value[-8] ^= 1
        path.write_bytes(value)
        with self.assertRaises(InputError):
            self.check()

    def test_byte_line_and_entry_caps_fail_closed(self):
        self.fixture()
        for kwargs in [
            {"max_bytes": 10},
            {"max_line_bytes": 10},
            {"max_entries": 1},
            {"max_labels": 1},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(InputError):
                self.check(**kwargs)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_findings_cap_preserves_failure(self):
        self.fixture("0 1 -1\n2 2 0\n")
        result = self.check(max_findings=0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["finding_count"], 3)
        self.assertEqual(result["findings_omitted"], 3)

    def test_empty_barcode_axis_is_supported(self):
        self.fixture("", shape="2 0 0")
        self.write("barcodes.tsv", "")
        self.assertTrue(self.check()["ok"])

    def test_prefix_and_no_path_traversal(self):
        self.fixture()
        for path in self.bundle.iterdir():
            path.rename(path.with_name("sample_" + path.name))
        self.assertTrue(self.check(prefix="sample_")["ok"])
        with self.assertRaises(InputError):
            self.check(prefix="../")

    def test_readonly_content(self):
        self.fixture()
        before = {p.name: p.read_bytes() for p in self.bundle.iterdir()}
        self.check()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.bundle.iterdir()})

    def test_cli_end_to_end(self):
        self.fixture()
        proc = subprocess.run(
            [sys.executable, "-m", "mtx_bundle_check", str(self.bundle), "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ok"])
        self.write("barcodes.tsv", "bc1\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = main([str(self.bundle), "--json"])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.getvalue())["ok"])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = main([str(self.root / "missing"), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out.getvalue()))


if __name__ == "__main__":
    unittest.main()
