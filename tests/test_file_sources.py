"""Standalone document providers, data-only parsing and replay-time authorization."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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
