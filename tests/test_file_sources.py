"""Standalone document providers, data-only parsing and replay-time authorization."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "file_source.py"


class Sources(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)

    def fields(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode().split("\0")[:-1]

    def helper(self, action, fmt, policy, path):
        return subprocess.run(
            [sys.executable, str(HELPER), action, fmt, policy, str(self.home / "document"), path],
            capture_output=True,
        )

    def test_ini_sections_are_literal(self):
        (self.home / "document").write_text("[DEFAULT]\nregion=west\n[default]\nregion=east\n[work]\n")
        rows = self.fields(self.helper("list", "ini", "section-names", ""))
        self.assertEqual(rows[2::3], ["DEFAULT", "default", "work"])
        self.assertEqual(self.helper("get", "ini", "leaf-values", "DEFAULT.region").stdout, b"west")
        self.assertNotEqual(self.helper("get", "ini", "leaf-values", "work.region").returncode, 0)

    def test_formats_and_component_selection(self):
        for fmt, doc in {
            "ini": "[deploy]\nregion=west\n[dependency]\nregion=east\n",
            "toml": '[deploy]\nregion="west"\n[dependency]\nregion="east"\n',
            "json": '{"deploy":{"region":"west"},"dependency":{"region":"east"}}',
            "yaml": "deploy: {region: west}\ndependency: {region: east}",
        }.items():
            with self.subTest(fmt=fmt):
                (self.home / "document").write_text(doc)
                rows = self.fields(self.helper("list", fmt, "leaf-values", "de.re"))
                self.assertEqual(set(rows[2::3]), {"deploy.re", "dependency.re"})
                self.assertEqual(
                    self.fields(self.helper("list", fmt, "leaf-values", "depl.re")),
                    ["leaf", "deploy.region", "deploy.region"],
                )
                self.assertEqual(self.helper("get", fmt, "leaf-values", "deploy.region").stdout, b"west")
                self.assertEqual(self.helper("get", fmt, "keys", "deploy").stdout, b"deploy")
                self.assertNotEqual(self.helper("get", fmt, "keys", "deploy.region").returncode, 0)

    def test_keys_arrays_empty_missing_and_malformed(self):
        file = self.home / "document"
        file.write_text('{"a.b":[{"x y":""}]}')
        rows = self.fields(self.helper("list", "json", "leaf-values", "a%2Eb.0.x"))
        self.assertEqual(rows, ["leaf", "a%2Eb.0.x%20y", "a%2Eb.0.x%20y"])
        result = self.helper("get", "json", "leaf-values", rows[2])
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
        for bad in ["{}", "secret user text !"]:
            file.write_text(bad)
            result = self.helper("get", "json", "leaf-values", rows[2])
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertNotIn(b"secret user text", result.stderr)

    def detect(self, file, formats, select="leaf-values"):
        return subprocess.run([sys.executable, HELPER, "detect", select, file, *formats],
                              text=True, capture_output=True, timeout=5)

    def test_authoritative_extensions_and_selection_capabilities(self):
        providers = ("ini", "yaml", "json", "toml")
        file = self.home / "values.JSON"
        file.write_text('{"region":"private_value"}')
        self.assertEqual(self.detect(file, providers).stdout, "json\n")
        disabled = self.detect(file, ["yaml"])
        self.assertNotEqual(disabled.returncode, 0)
        self.assertIn("disabled", disabled.stderr)
        wrong_policy = self.detect(file, providers, "section-names")
        self.assertNotEqual(wrong_policy.returncode, 0)
        self.assertIn("does not support", wrong_policy.stderr)
        file.write_text("region: private_value")
        invalid = self.detect(file, providers)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertNotIn("private_value", invalid.stderr + invalid.stdout)
        self.assertIn("malformed JSON at line 1, column 1", invalid.stderr)
        self.assertNotIn("yaml", invalid.stderr)

        file.rename(self.home/"values.yml")
        self.assertEqual(self.detect(self.home/"values.yml", providers).stdout, "yaml\n")
        unknown = self.home / "credentials"
        unknown.write_text("[DEFAULT]\nregion=private_value\n")
        self.assertEqual(self.detect(unknown, providers, "section-names").stdout, "ini\n")
        self.assertFalse(self.detect(unknown, [], "section-names").stdout)

    def test_unknown_extension_compares_typed_interpretations(self):
        data = self.home / "values.unknown"
        data.write_text('{"dotted.key":{"enabled":true},"key-bytes":"private_value"}')
        # JSON is a subset of YAML here. Equivalent results resolve consistently
        # regardless of configuration order.
        for formats in (["yaml", "json"], ["json", "yaml", "toml", "ini"]):
            self.assertEqual(self.detect(data, formats).stdout, "json\n")
        data.write_text("[deploy]\nsecret=123456789\n")
        ambiguous = self.detect(data, ["ini", "toml", "yaml"])
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertEqual(ambiguous.stdout, "")
        self.assertIn("ambiguous", ambiguous.stderr)
        self.assertIn("ini", ambiguous.stderr)
        self.assertIn("toml", ambiguous.stderr)
        self.assertNotIn("123456789", ambiguous.stderr)
        data.write_text("[deploy]\n")
        self.assertEqual(self.detect(data, ["ini", "toml"]).stdout, "toml\n")
        data.write_text("simple scalar")
        scalar = self.detect(data, ["yaml"])
        self.assertNotEqual(scalar.returncode, 0)
        self.assertIn("root cannot be navigated", scalar.stderr)
        data.write_text("[1, 2, 3]")
        self.assertEqual(self.detect(data, ["yaml", "json"]).stdout, "json\n")
        self.assertNotEqual(self.detect(data, ["yaml", "json"], "keys").returncode, 0)

    def test_detect_reads_one_snapshot_and_handles_aliases(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("token_file_source", HELPER)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        file = self.home/"shared"
        with patch.object(helper, "read_document", return_value=b'{"a.b":[1,2]}') as reader:
            self.assertEqual(helper.detect("leaf-values", str(file), ["ini", "toml", "json", "yaml"]), "json")
        reader.assert_called_once_with(str(file))
        # Two parsers can advertise one suffix. Compare all eligible parses.
        other_ini = helper.DocumentParser(helper.parse_ini, (".toml",), helper.COMMON)
        with patch.dict(helper.PARSERS, ini=other_ini), patch.object(helper, "read_document", return_value=b"[s]\nx=1"):
            with self.assertRaisesRegex(helper.DetectionError, "ambiguous.*toml.*ini"):
                helper.detect("keys", "settings.toml", ["ini", "toml"])
        def missing_parser(_data):
            raise ModuleNotFoundError("missing", name="yaml")
        unavailable = helper.DocumentParser(missing_parser, (".yaml",), helper.COMMON)
        with patch.dict(helper.PARSERS, yaml=unavailable), patch.object(helper, "read_document", return_value=b"x"):
            with self.assertRaisesRegex(helper.DetectionError, "unavailable; missing module yaml"):
                helper.detect("leaf-values", "data.yaml", ["yaml", "ini"])
        for left, right in [(True, 1), (1, 1.0), ({"a.b": "v"}, {"a": {"b": "v"}}),
                            ({True: 1}, {1: 1}), ({"x": [0.0]}, {"x": [-0.0]})]:
            self.assertFalse(helper.same_document(left, right))
        self.assertTrue(helper.same_document({"mixed": {1: "numeric", "1": "text"}},
                                             {"mixed": {"1": "text", 1: "numeric"}}))
        left, right = [], []
        left.append(left)
        right.append(right)
        self.assertTrue(helper.same_document(left, right))
        file.write_text("node: &root [*root]\n")
        self.assertEqual(self.detect(file, ["json", "yaml"]).stdout, "yaml\n")

    def test_lookup_guard_runs_before_resolving_the_document(self):
        file = self.home/"document"
        file.write_text('{"private":"live value"}')
        result = subprocess.run(["zsh", "-dfc", """
source "$PLUGIN" || exit 20
accept() {
  [[ $1 == task && $2 == --setting && $3 == json && $4 == leaf-values && $5 == ./document ]]
}
token-completion lookup json leaf-values ./document private accept task --setting || exit 21
print
token-completion lookup json leaf-values ./document private accept other --setting && exit 22
token-completion lookup json leaf-values ./document private missing_guard task --setting && exit 23
exit 0
"""], cwd=self.home, env={**os.environ, "PLUGIN": str(ROOT/"token-completion.plugin.zsh")},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "live value\n")
        self.assertNotIn("live value", result.stderr)


if __name__ == "__main__":
    unittest.main()
