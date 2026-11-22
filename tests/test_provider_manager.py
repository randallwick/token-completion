"""End-to-end provider locking with disposable Git repositories and no network."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import sys
import tempfile
import unittest

MANAGER = Path(__file__).resolve().parents[1] / "providers.py"
ENTRY = "github:sample/tools@stable:providers/demo.zsh"
PROVIDER = """TOKEN_PROVIDER_ID=demo
typeset -g token_provider_demo_version=1
token_provider_demo_query() { token_completion_emit REGION demo "'west coast'"; }
"""


class ProviderManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.repo = self.home/"upstream"
        self.repo.mkdir()
        self.env = {**os.environ, "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home/"config"),
                    "XDG_CACHE_HOME": str(self.home/"cache"), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_ALLOW_PROTOCOL": "file", "GIT_CONFIG_GLOBAL": str(self.home/"gitconfig"),
                    "GIT_AUTHOR_NAME": "Test Fixture", "GIT_AUTHOR_EMAIL": "tests@example.invalid",
                    "GIT_COMMITTER_NAME": "Test Fixture", "GIT_COMMITTER_EMAIL": "tests@example.invalid",
                    "TOKEN_COMPLETION_CONFIG_DIR": str(self.home/"user-config")}
        self.git("init", "-q", "-b", "stable")
        self.git("config", "--global", f"url.{self.repo.as_uri()}.insteadOf", "https://github.com/sample/tools.git")
        self.provider = self.repo/"providers/demo.zsh"
        self.provider.parent.mkdir()
        self.provider.write_text(PROVIDER)
        (self.repo/"support").write_bytes(b"hello\x00world")
        (self.repo/"support-link").symlink_to("support")
        (self.repo/".gitattributes").write_text("support export-ignore\n")
        self.initial = self.commit()
        self.lock = self.home/"user-config/providers.lock"

    def git(self, *args):
        return subprocess.check_output(["git", *args], env=self.env, cwd=self.repo, text=True).strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "test update")
        return self.git("rev-parse", "HEAD")

    def invoke(self, action, *entries, ok=True, flags=()):
        result = subprocess.run([sys.executable, str(MANAGER), action, *flags, "--", *entries],
                                env=self.env, capture_output=True, timeout=20)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def show_pins(self):
        return json.loads(self.lock.read_bytes())["providers"]

    def resolved(self, *entries):
        fields = self.invoke("resolve", *entries).stdout.split(b"\0")
        self.assertEqual(fields.pop(), b"")
        return {os.fsdecode(entry): Path(os.fsdecode(file)) for entry, file in zip(fields[::2], fields[1::2])}

    def test_cli_and_portable_lock(self):
        before = self.invoke("list", "shell", ENTRY, flags=("--available", "shell")).stdout.decode()
        self.assertIn("loaded", before)
        self.assertIn("unpinned", before)
        self.assertNotIn("\033", before)
        output = self.invoke("install", "shell", ENTRY).stdout.decode()
        self.assertIn(self.initial[:12], output)
        self.assertNotIn(str(self.home), self.lock.read_text())
        self.assertEqual(self.show_pins()[ENTRY]["provider"], "demo")
        file = self.resolved(ENTRY)[ENTRY]
        self.assertEqual(file.read_text(), PROVIDER)
        self.assertEqual((file.parents[1]/"support").read_bytes(), b"hello\x00world")
        self.assertIn(self.initial[:12], self.invoke("list", ENTRY).stdout.decode())
        self.assertIn("No providers selected", self.invoke("list").stdout.decode())
        self.assertEqual(self.invoke("install", ENTRY).stdout.decode().split()[0], "pinned")

    def test_no_network_except_install_or_update_and_rebuild_after_cache_clear(self):
        self.invoke("install", ENTRY)
        original = self.lock.read_bytes()
        self.provider.write_text(PROVIDER+"# revision 2\n")
        second = self.commit()
        offline = self.env.copy()
        offline["GIT_ALLOW_PROTOCOL"] = ""
        self.env, online = offline, self.env
        self.assertIn(ENTRY, self.resolved(ENTRY))
        self.invoke("install", ENTRY)
        self.invoke("update", ENTRY, ok=False)
        self.assertEqual(self.lock.read_bytes(), original)
        shutil.rmtree(self.home/"cache")
        self.assertEqual(self.resolved(ENTRY), {})
        self.assertIn("providers install", self.invoke("list", ENTRY).stdout.decode())
        self.env = online
        self.invoke("install", ENTRY)
        self.assertEqual(self.resolved(ENTRY)[ENTRY].read_text(), PROVIDER)
        self.assertEqual(self.lock.read_bytes(), original)
        result = self.invoke("update", ENTRY).stdout.decode()
        self.assertIn(f"{self.initial[:12]} → {second[:12]}", result)
        self.assertEqual(self.show_pins()[ENTRY]["commit"], second)

    def test_ref_resolution(self):
        self.git("tag", "-a", "release", "-m", "release")
        self.git("tag", "light")
        for ref in [self.initial, "light", "release"]:
            with self.subTest(ref=ref):
                entry = ENTRY.replace("@stable:", f"@{ref}:")
                self.invoke("install", entry)
                self.assertEqual(self.show_pins()[entry]["commit"], self.initial)
        self.git("tag", "stable")
        self.assertIn(b"both a branch and a tag", self.invoke("update", ENTRY, ok=False).stderr)

    def test_drift_and_repair(self):
        self.invoke("install", ENTRY)
        file = self.resolved(ENTRY)[ENTRY]
        root = file.parents[1]
        for case in ["extra", "symlink", "contents", "executable", "entry"]:
            with self.subTest(case=case):
                if case == "extra":
                    (root/"extra").write_text("unexpected")
                elif case == "symlink":
                    (root/"support-link").unlink()
                    (root/"support-link").symlink_to(self.home)
                elif case == "contents":
                    (root/"support").write_text("edited")
                elif case == "executable":
                    (root/"support").chmod(0o755)
                else:
                    file.write_text(PROVIDER.replace("west", "east"))
                self.assertEqual(self.resolved(ENTRY), {})
                self.assertIn("unavailable", self.invoke("list", ENTRY).stdout.decode())
                self.invoke("install", ENTRY)
                self.assertEqual(self.resolved(ENTRY)[ENTRY].read_text(), PROVIDER)

    def test_atomic_failed_multi_provider_update(self):
        other = self.repo/"other.zsh"
        other.write_text(PROVIDER.replace("demo", "other"))
        self.commit()
        second_entry = ENTRY.replace("providers/demo.zsh", "other.zsh")
        self.invoke("install", ENTRY, second_entry)
        original = self.lock.read_bytes()
        self.provider.write_text(PROVIDER+"# new\n")
        other.write_text("TOKEN_PROVIDER_ID=other\n")
        self.commit()
        self.assertIn(b"interface version", self.invoke("update", ENTRY, second_entry, ok=False).stderr)
        self.assertEqual(self.lock.read_bytes(), original)
        self.assertEqual(self.resolved(ENTRY, second_entry)[ENTRY].read_text(), PROVIDER)

    def test_zsh_entrypoint_installs_updates_and_uses_provider_offline(self):
        plugin = MANAGER.with_name("token-completion.plugin.zsh")
        script = r'''typeset -ga TOKEN_COMPLETION_PROVIDERS=(shell "$2")
source "$1" || exit 10
token-completion providers install || exit 11
token-completion providers list || exit 12
(( ${+functions[token_provider_demo_query]} )) || exit 13
token_completion_emit() { print -r -- "candidate $1 $2 $3"; }
token_provider_demo_query R
'''
        proc = subprocess.run(["zsh", "-dfc", script, "test", str(plugin), ENTRY], env=self.env,
                              text=True, capture_output=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("candidate REGION demo 'west coast'", proc.stdout)
        self.assertIn(self.initial[:12], proc.stdout)
        self.assertIn("loaded", proc.stdout)
        self.provider.write_text(PROVIDER.replace("REGION", "CLUSTER"))
        self.commit()
        update = script.replace("providers install", "providers update")
        proc = subprocess.run(["zsh", "-dfc", update, "test", str(plugin), ENTRY], env=self.env,
                              text=True, capture_output=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("candidate CLUSTER", proc.stdout)
        self.env["GIT_ALLOW_PROTOCOL"] = ""
        proc = subprocess.run(["zsh", "-dfc", script, "test", str(plugin), ENTRY], env=self.env,
                              text=True, capture_output=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("candidate CLUSTER", proc.stdout)

    def test_invalid_selection_and_collisions(self):
        self.invoke("install", ENTRY)
        lock = self.lock.read_bytes()
        for bad in [ENTRY.replace("providers/demo.zsh", "../x"),
                    ENTRY.replace("@stable:", "@--wrong:"), ENTRY.replace("providers/demo.zsh", "/x"),
                    "github:sample/tools@stable:missing", "github:sample/tools@stable:providers",
                    "relative/path.zsh"]:
            with self.subTest(entry=bad):
                self.invoke("install", bad, ok=False)
                self.assertEqual(self.lock.read_bytes(), lock)
        for second in [ENTRY, "demo", str(self.provider)]:
            self.assertIn(b"more than once", self.invoke("install", ENTRY, second, ok=False).stderr)
        (self.repo/"outside").symlink_to("../../outside")
        self.commit()
        self.invoke("update", ENTRY, ok=False)
        self.assertEqual(self.lock.read_bytes(), lock)
        self.assertIn(ENTRY, self.resolved(ENTRY))

    def test_update_cannot_displace_a_known_registered_provider(self):
        self.invoke("install", ENTRY)
        lock = self.lock.read_bytes()
        for reserved in ["shell", "dotenv", "external"]:
            with self.subTest(provider=reserved):
                self.provider.write_text(PROVIDER.replace("demo", reserved))
                self.commit()
                result = self.invoke("update", ENTRY, ok=False, flags=("--reserved", "external"))
                self.assertIn(b"already registered", result.stderr)
                self.assertEqual(self.lock.read_bytes(), lock)
                self.assertEqual(self.resolved(ENTRY)[ENTRY].read_text(), PROVIDER)

    def test_zsh_list_and_transaction_with_conflicting_local_or_remote_id(self):
        self.invoke("install", ENTRY)
        lock = self.lock.read_bytes()
        # The remote and local providers both claim an externally loaded ID.
        local = self.home/"local.zsh"
        local.write_text(PROVIDER)
        plugin = MANAGER.with_name("token-completion.plugin.zsh")
        for entry in [ENTRY, str(local)]:
            with self.subTest(entry=entry):
                script = r'''typeset -ga TOKEN_COMPLETION_PROVIDERS=("$2")
typeset -g token_provider_demo_version=1
token_provider_demo_query() { print fixture; }
source "$1" || exit 21
token-completion providers list || exit 22
token-completion providers update && exit 23
token_provider_demo_query
'''
                proc = subprocess.run(["zsh", "-dfc", script, "test", str(plugin), entry], env=self.env,
                                      text=True, capture_output=True, timeout=20)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("unavailable", proc.stdout)
                self.assertIn("duplicate provider ID", proc.stdout)
                self.assertTrue(proc.stdout.rstrip().endswith("fixture"))
                self.assertEqual(self.lock.read_bytes(), lock)

    def test_concurrent_installs_and_malformed_lock(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.invoke("install", ENTRY), range(8)))
        self.assertTrue(all(r.returncode == 0 for r in results))
        self.assertIn(ENTRY, self.resolved(ENTRY))
        self.lock.write_text('{"version":1,"providers":{')
        self.assertEqual(self.invoke("resolve", ENTRY, ok=False).stdout, b"")
        self.assertIn(b"cannot read", self.invoke("list", ENTRY, ok=False).stderr)
        self.invoke("update", ENTRY, ok=False)
        self.assertEqual(self.lock.read_text(), '{"version":1,"providers":{')

    def test_cli_presentation(self):
        for plain in [False, True]:
            master, slave = pty.openpty()
            env = dict(self.env, TERM="xterm-256color")
            env.pop("NO_COLOR", None)
            if plain:
                env["NO_COLOR"] = "1"
            try:
                subprocess.run([sys.executable, str(MANAGER), "list", "--available", "shell", "--", "shell"],
                               env=env, stdout=slave, check=True)
                out = os.read(master, 8192)
                self.assertEqual(b"\x1b[" in out, not plain)
            finally:
                os.close(master)
                os.close(slave)
        self.assertNotIn(b"\x1b", self.invoke("list", "bad\x1b[31m").stdout)


if __name__ == "__main__":
    unittest.main()
