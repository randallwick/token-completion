"""Provider and real-ZLE tests. Run with Python 3.11+, zsh, fzf and Git."""
import os
from pathlib import Path
import pty
import select
import shlex
import signal
import subprocess
import tempfile
import termios
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "source " + shlex.quote(str(ROOT / "token-completion.plugin.zsh"))
PROVIDER = r"""
TOKEN_PROVIDER_ID=catalog
typeset -g token_provider_catalog_version=1
token_provider_catalog_prepare() { print called >> "$HOME/prepared"; }
token_provider_catalog_query() {
  token_completion_emit LAB_ENV catalog "${(qqqq):-literal ; \$(never)}"
  token_completion_emit TWINS same first
  token_completion_emit TWINS same second
}
"""


class Terminal:
    def __init__(self, home, config, env, cwd):
        (home / ".zshrc").write_text(config)
        self.home = home
        self.pending = b""
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(cwd)
            os.execvpe("zsh", ["zsh", "-di"], env)
        termios.tcsetwinsize(self.fd, (24, 112))
        self.until(b"READY>")

    def send(self, data):
        os.write(self.fd, data)

    def until(self, needle):
        end = time.monotonic() + 8
        while needle not in self.pending:
            timeout = end - time.monotonic()
            if timeout <= 0 or not select.select([self.fd], [], [], timeout)[0]:
                raise AssertionError(f"missing {needle!r} in {self.pending[-1200:]!r}")
            self.pending += os.read(self.fd, 65536)
        index = self.pending.index(needle) + len(needle)
        observed, self.pending = self.pending[:index], self.pending[index:]
        return observed

    def probe(self):
        path = self.home / "buffer"
        path.unlink(missing_ok=True)
        self.send(b"\x18")
        end = time.monotonic() + 6
        while not path.exists() and time.monotonic() < end:
            time.sleep(.01)
        if not path.exists():
            if select.select([self.fd], [], [], 0)[0]:
                self.pending += os.read(self.fd, 65536)
            raise AssertionError(f"ZLE probe timed out: {self.pending[-1200:]!r}")
        return path.read_text()

    def close(self):
        os.kill(self.pid, signal.SIGKILL)
        os.waitpid(self.pid, 0)
        os.close(self.fd)


class Completion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = {**os.environ, "TERM": "xterm-256color", "HOME": str(self.home),
                    "ZDOTDIR": str(self.home), "XDG_CONFIG_HOME": str(self.home/"config"),
                    "XDG_CACHE_HOME": str(self.home/"cache")}
        self.file = self.home/"catalog.zsh"
        self.file.write_text(PROVIDER)
        self.one = self.home/"one"
        self.two = self.home/"two"
        self.one.mkdir()
        self.two.mkdir()
        (self.one/".env").write_text("LAB_ENV='secret one'\nEMPTY=\n")
        (self.two/".env").write_text("LAB_ENV='secret two'\n")
        self.config = """
bindkey -e
fallback() { LBUFFER+=':fallback'; }
zle -N fallback
bindkey '^I' fallback
LAB_ENV='secret shell value'
SOLO_SHELL='value with spaces'
TOKEN_COMPLETION_PROMPT='Look up › '
TOKEN_COMPLETION_PROVIDERS=(shell dotenv "$HOME/catalog.zsh")
""" + SOURCE + """
HISTFILE=$HOME/history
HISTSIZE=1000
SAVEHIST=1000
setopt inc_append_history interactivecomments
PROBE_WIDGET() { print -rn -- "$BUFFER" > "$HOME/probe.tmp"; command mv "$HOME/probe.tmp" "$HOME/buffer"; }
zle -N PROBE_WIDGET
bindkey '^X' PROBE_WIDGET
PS1='READY> '
"""

    def zsh(self, script):
        return subprocess.run(["zsh", "-dfc", script], cwd=self.one, env=self.env,
                              text=True, capture_output=True, timeout=12)

    def test_provider_lifecycle(self):
        for order in (0, 1):
            with self.subTest(order=order):
                load = f"source {shlex.quote(str(self.file))}"
                setup = f"{load}\n{SOURCE}" if order == 0 else f"{SOURCE}\n{load}"
                check = self.zsh(setup + """
test_query() {
  local -a _tc_ids _tc_insertions _tc_rows
  _tc_active_providers
  print -r -- "$_tc_ids"
  token_provider_catalog_query LAB
  print -rl -- "${_tc_rows[@]}"
}
test_query; test_query
TOKEN_COMPLETION_PROVIDERS=()
typeset -a _tc_ids
_tc_active_providers
print -r -- "empty:$#_tc_ids"
""")
                self.assertEqual(check.returncode, 0, check.stderr)
                self.assertTrue({"shell", "dotenv", "catalog"} <= set(check.stdout.splitlines()[0].split()))
                self.assertIn("2\tTWINS\t[same]", check.stdout)
                self.assertIn("3\tTWINS\t[same]", check.stdout)
                self.assertIn("empty:0", check.stdout)
                self.assertEqual((self.home/"prepared").read_text(), "called\n")
                (self.home/"prepared").unlink()

    def test_files_are_optional_and_duplicates_preserve_loaded_provider(self):
        second = self.home/"collision.zsh"
        second.write_text(PROVIDER.replace("first", "changed"))
        script = 'TOKEN_COMPLETION_PROVIDERS=("$HOME/catalog.zsh" "$HOME/collision.zsh" "$HOME/missing")\n' + SOURCE + r"""
typeset -a _tc_ids _tc_insertions _tc_rows
_tc_active_providers
token_provider_catalog_query ''
print -rl -- "${_tc_ids[@]}" "${_tc_insertions[@]}" "${(v)_tc_file_errors[@]}"
token_completion_emit TOKEN $'source\e\t\n' 'never in display'
print -r -- "$_tc_rows[-1]"
"""
        check = self.zsh(script)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("\nfirst\n", check.stdout)
        self.assertNotIn("changed", check.stdout)
        self.assertIn("duplicate provider ID: catalog", check.stdout)
        self.assertIn("unavailable or incompatible", check.stdout)
        self.assertIn("[source???]", check.stdout)
        self.assertNotIn("never in display", check.stdout)

    def test_move_local_provider_in_same_shell(self):
        second = self.home/"moved.zsh"
        second.write_text(PROVIDER.replace("first", "replacement"))
        script = 'TOKEN_COMPLETION_PROVIDERS=("$HOME/catalog.zsh")\n' + SOURCE + """
TOKEN_COMPLETION_PROVIDERS=("$HOME/moved.zsh")
token-completion providers install || exit
typeset -a _tc_ids _tc_insertions _tc_rows
_tc_active_providers
token_provider_catalog_query LAB
print -rl -- "${_tc_ids[@]}" "${_tc_insertions[@]}"
"""
        check = self.zsh(script)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("\ncatalog\n", check.stdout)
        self.assertIn("\nreplacement\n", check.stdout)
        self.assertNotIn("\nfirst\n", check.stdout)

    def test_dotenv_literals_and_diagnostics(self):
        (self.one/".env").write_text("export FIELD='$(touch wrong) # literal' # x\nEMPTY= # comment\n")
        check = self.zsh(SOURCE + "\ntoken-completion dotenv FIELD")
        self.assertEqual(check.stdout, "$(touch wrong) # literal")
        self.assertFalse((self.one/"wrong").exists())
        check = self.zsh(SOURCE + "\ntoken-completion dotenv EMPTY")
        self.assertEqual((check.stdout, check.stderr, check.returncode), ("","",0))
        check = self.zsh(SOURCE + '\nprint -r -- "[$(token-completion dotenv ABSENT)]"')
        self.assertEqual(check.stdout, "[]\n")
        self.assertIn("ABSENT", check.stderr)
        (self.one/".env").unlink()
        self.assertIn(".env lookup", self.zsh(SOURCE+"\ntoken-completion dotenv FIELD").stderr)

    def test_standalone_file_providers_list_and_registration(self):
        check = self.zsh(SOURCE + """
token-completion providers list || exit 1
token-completion files --ini './local prefs.ini' --toml ./services.toml --json ./app.json --yaml ./deploy.yml --json ./extra.json || exit 2
for var in TOKEN_COMPLETION_INI_FILES TOKEN_COMPLETION_TOML_FILES TOKEN_COMPLETION_JSON_FILES TOKEN_COMPLETION_YAML_FILES; do
  print -rl -- "${(@P)var}"
done
""")
        self.assertEqual(check.returncode, 0, check.stderr)
        for fmt in ("ini", "toml", "json", "yaml"):
            self.assertRegex(check.stdout, rf"(?m)^{fmt}\s+loaded")
        for file in ("./local prefs.ini", "./services.toml", "./app.json", "./deploy.yml", "./extra.json"):
            self.assertIn(file, check.stdout.splitlines())

    def test_register_files_detects_once_and_publishes_atomically(self):
        (self.one / "settings.JSON").write_text('{"region": "source value"}')
        (self.one / "mislabeled.toml").write_text('{"region": "override value"}')
        wrapper = self.home / "parser"
        wrapper.write_text('#!/bin/sh\n[ "$2" != detect ] || echo detect >> "$HOME/detections"\nexec python3 "$@"\n')
        wrapper.chmod(0o755)
        result = self.zsh(SOURCE + """
_tc_python=$HOME/parser
TOKEN_COMPLETION_PROVIDERS=(json)
token-completion files -f settings.JSON --format json --file mislabeled.toml || exit 11
token-completion files --format toml -f something && exit 12
token-completion files --json first.json -f missing.toml && exit 13
token-completion files --json first.json --format && exit 14
token-completion files --format json && exit 15
token-completion files --file && exit 16
token-completion files --json first.json --ini other.ini && exit 17
token_completion_offer() { print -r -- "$3"; }
token_provider_json_query reg
token_provider_json_query reg
print -rl -- CATALOG "${TOKEN_COMPLETION_JSON_FILES[@]}"
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split("CATALOG\n")[1].splitlines(), ["settings.JSON", "mislabeled.toml"])
        self.assertIn("lookup json leaf-values mislabeled.toml", result.stdout)
        self.assertEqual((self.home / "detections").read_text().splitlines(), ["detect", "detect"])
        self.assertNotIn("source value", result.stdout + result.stderr)

    def test_token_query_cannot_register_an_unimplemented_document_parser(self):
        check = self.zsh(SOURCE + """
typeset -g token_provider_xml_version=1 token_provider_xml_file_format=1
token_provider_xml_query(){ return 0; }
TOKEN_COMPLETION_PROVIDERS=(xml json)
token-completion files --json keep.json || exit 2
token-completion files --json first.json --format xml -f /dev/null && exit 3
token-completion files --xml /dev/null && exit 4
print -r -- "${TOKEN_COMPLETION_JSON_FILES[@]}"
""")
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(check.stdout, "keep.json\n")
        self.assertIn("xml", check.stderr)

    def test_manifest_inference_cache_and_recovery(self):
        (self.one/"settings").write_text('[deploy]\nzone=123456789\n')
        (self.two/"settings").write_text('{"deploy":{"zone":true}}')
        (self.two/"one.json").symlink_to("settings")
        (self.two/"two.yaml").symlink_to("settings")
        wrapper = self.home / "parser"
        wrapper.write_text('#!/bin/sh\n[ "$2" != detect ] || echo detect >> "$HOME/detections"\nexec python3 "$@"\n')
        wrapper.chmod(0o755)
        result = self.zsh(SOURCE + """
_tc_python=$HOME/parser
TOKEN_COMPLETION_PROVIDERS=(json ini toml yaml)
token_completion_offer() { print -r -- "$3"; }
token_completion_file dep.z auto leaf-values ./settings
token_completion_file dep.z auto leaf-values ./settings
print -r -- '{"deploy":{"zone":"west"}}' > settings
token_completion_file dep.z auto leaf-values ./settings
token_completion_file dep.z auto leaf-values ./settings
cd ../two
token_completion_file dep.z auto leaf-values ./settings
token_completion_file dep.z auto leaf-values ./one.json
token_completion_file dep.z auto leaf-values ./two.yaml
TOKEN_COMPLETION_PROVIDERS=(yaml)
token_completion_file dep.z auto leaf-values ./settings
token_completion_file dep.z auto leaf-values ./settings
token_completion_file dep auto keys ./settings
token-completion lookup json leaf-values ./settings deploy.zone && exit 18
true
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count("ambiguous"), 1, result.stderr)
        self.assertIn("ini", result.stderr)
        self.assertIn("toml", result.stderr)
        self.assertNotIn("123456789", result.stderr)
        self.assertEqual(result.stdout.count("lookup json leaf-values ./settings"), 3)
        self.assertIn("lookup json leaf-values ./one.json", result.stdout)
        self.assertIn("lookup yaml leaf-values ./two.yaml", result.stdout)
        self.assertEqual(result.stdout.count("lookup yaml leaf-values ./settings"), 2)
        self.assertIn("lookup yaml keys ./settings", result.stdout)
        self.assertEqual(len((self.home/"detections").read_text().splitlines()), 8)
        self.assertIn("disabled", result.stderr)

    def test_each_builtin_document_provider_emits_from_its_file_array(self):
        documents = {
            "ini": "[deployment]\nzone=DO_NOT_DISPLAY\n",
            "toml": '[deployment]\nzone="DO_NOT_DISPLAY"\n',
            "json": '{"deployment":{"zone":"DO_NOT_DISPLAY"}}',
            "yaml": "deployment: {zone: DO_NOT_DISPLAY}\n",
        }
        for fmt, doc in documents.items():
            with self.subTest(format=fmt):
                (self.one/"document").write_text(doc)
                result = self.zsh(SOURCE + f"""
TOKEN_COMPLETION_{fmt.upper()}_FILES=(./document)
token_completion_offer() {{ printf '%s\\0' "$@"; }}
token_provider_{fmt}_query depl.z
""")
                self.assertEqual(result.returncode, 0, result.stderr)
                display, label, insertion, end = result.stdout.split("\0")
                self.assertEqual((display, label, end), ("deployment.zone", "./document", ""))
                self.assertIn("token-completion lookup", insertion)
                self.assertNotIn("DO_NOT_DISPLAY", result.stdout)

    def test_standalone_document_paths_in_zle(self):
        import json
        for directory, zone in ((self.one, "east coast"), (self.two, "north zone")):
            (directory/"settings.json").write_text(json.dumps({"deployment": {"us.east": zone}}))
        # Select the built-in JSON provider alone in a disposable shell.
        config = self.config.replace('TOKEN_COMPLETION_PROVIDERS=(shell dotenv "$HOME/catalog.zsh")',
                                     'TOKEN_COMPLETION_PROVIDERS=(json)')
        config += '\ntoken-completion files --json ./settings.json\n'
        term = Terminal(self.home, config, self.env, self.one)
        self.addCleanup(term.close)
        term.send(b'print -r -- $dep\t')
        self.assertEqual(term.probe(), "print -r -- $deployment.")
        term.send(b'us\t')
        saved = term.probe()
        self.assertIn("token-completion lookup", saved)
        self.assertIn("deployment.us%2Eeast", saved)
        self.assertNotIn("east coast", saved)
        self.assertNotIn("shell-overrides", saved)
        term.send(b'\r')
        term.until(b'east coast')
        term.until(b'READY>')
        self.assertIn(saved, (self.home/"history").read_text())
        term.send(f'cd ../two; {saved}\r'.encode())
        term.until(b'north zone')
        term.until(b'READY>')
        term.send(b'print -r -- $depl.us\t')
        self.assertEqual(term.probe(), saved)

    def test_scoped_context_in_zle(self):
        # A provider supplies a branch insertion on one Tab, a leaf on the next.
        config = self.config + """
token_provider_catalog_context() {
  local fragment=$1 cmd=$2 opt=$3 form=$4
  shift 4
  [[ $cmd == deploy && $opt == --value && $1 == get ]] || return 0
  token_completion_scope
  case $fragment in
    '') token_completion_offer region cfg '$region.' ;;
    region.) token_completion_offer region.zone cfg "'west'" ;;
  esac
}
"""
        term = Terminal(self.home, config, self.env, self.one)
        self.addCleanup(term.close)
        for prefix in ("echo ignored | deploy get --value=", "deploy get --value ",
                       "echo ignored &! deploy get --value=", "deploy >|out get --value=",
                       "deploy 2>>!out get --value=", "deploy &>>|out get --value="):
            term.send((prefix + "$").encode() + b"\t")
            self.assertEqual(term.probe(), prefix + "$region.")
            term.send(b"\t")
            self.assertEqual(term.probe(), prefix + "'west'")
            term.send(b"\x15")
        for prefix in ("deploy get -- --value=", "deploy get | echo --value=", "deploy other --value ",
                       "deploy get &! echo --value=", "deploy get &| echo --value=",
                       "deploy {fd}>out get --value=", "deploy get 2<<EOF --value="):
            term.send((prefix + "$region.").encode() + b"\t")
            self.assertEqual(term.probe(), prefix + "$region.:fallback")
            term.send(b"\x15")

    @unittest.skipUnless(os.environ.get("SHELL_OVERRIDES_TEST_PLUGIN"), "set SHELL_OVERRIDES_TEST_PLUGIN for integration")
    def test_manifest_source_in_zle(self):
        manifests = self.home / "manifests"
        manifests.mkdir()
        (manifests / "cli.yaml").write_text("""version: 1
commands:
  probe:
    - prefix: []
      completions:
        - option: --profile
          markerless: {empty: all}
          source: {file: ./credentials, select: section-names}
        - option: --account
          markerless: {empty: native}
          source: {file: ./credentials, select: section-names}
        - position: 1
          markerless: {empty: native}
          source: {file: ./settings.json, select: leaf-values}
        - option: --value
          source: {file: ./settings.json, select: leaf-values}
""")
        exe = self.home / "probe"
        exe.write_text("#!/bin/sh\nprintf '<%s>\\n' \"$@\"\n")
        exe.chmod(0o700)
        for directory, value in [(self.one, "first value"), (self.two, "second value")]:
            (directory / "settings.json").write_text('{"deploy":{"us.east":"' + value + '"}}')
            (directory / "credentials").write_text("[work-read-dev]\nsecret = NEVER_DISPLAY\n[work-read-stage]\nsecret = NEVER_DISPLAY\n")
        config = self.config + f"""
path=("$HOME" $path)
SHELL_OVERRIDES_MANIFEST_DIR="$HOME/manifests"
TOKEN_COMPLETION_PROVIDERS=(shell_overrides ini json)
source {shlex.quote(os.environ["SHELL_OVERRIDES_TEST_PLUGIN"])}
"""
        term = Terminal(self.home, config, self.env, self.one)
        self.addCleanup(term.close)
        for prefix in ("probe --profile ", "probe --profile="):
            for query in ("", "wo"):
                term.send((prefix + query).encode() + b"\t")
                menu = term.until(b"work-read-stage")
                self.assertNotIn(b"NEVER_DISPLAY", menu)
                term.send(b"stage")
                term.until(b"1/")
                term.send(b"\r")
                term.until(b"\x1b[?1049l")
                self.assertNotIn("lookup", term.probe())
                term.send(b"\r")
                term.until(b"work-read-stage>")
                term.until(b"READY>")

        # An unclaimed bare argument and the positional declaration's native
        # empty policy both delegate to the saved widget. Dollar tokens still
        # reach the existing guarded provider.
        for line in ("probe ", "probe --account ", "probe --account=", "probe other dep", "echo --profile=wo"):
            term.send(line.encode() + b"\t")
            self.assertEqual(term.probe(), line + ":fallback")
            term.send(b"\x15")

        term.send(b"probe dep.us\t")
        literal = term.probe()
        self.assertNotIn("lookup", literal)
        term.send(b"\r")
        term.until(b"<first value>")
        term.until(b"READY>")
        term.send(f"cd ../two; {literal}\r".encode())
        term.until(b"<first value>")
        term.until(b"READY>")
        term.send(b"cd ../one\r")
        term.until(b"READY>")
        term.send(b'probe --profile=$wo\t')
        menu = term.until(b'work-read-stage')
        self.assertNotIn(b'NEVER_DISPLAY', menu)
        term.send(b'stage')
        term.until(b'1/')
        term.send(b'\r')
        term.until(b'\x1b[?1049l')
        self.assertIn("lookup", term.probe())
        term.send(b'\r')
        term.until(b'<--profile=work-read-stage>')
        term.until(b'READY>')
        term.send(b'probe --value $dep.us\t')
        saved = term.probe()
        self.assertIn('deploy.us%2Eeast', saved)
        term.send(b'\r')
        term.until(b'<first value>')
        term.until(b'READY>')
        term.send(f'cd ../two; {saved}\r'.encode())
        term.until(b'<second value>')

    def test_argument_context_in_zle(self):
        config = self.config + """
token_provider_catalog_context() { token_completion_offer legacy source WRONG; }
token_provider_catalog_argument_context() {
  local mode=$1 fragment=$2 cmd=$3 opt=$4 form=$5
  shift 5
  [[ $cmd == deploy ]] || return
  case "$form:$opt" in
    equals:--region|separate:--region) token_completion_scope ;;
    positional:) [[ $# == 0 || $1 == --verbose || $1 == -- ]] || return; token_completion_scope ;;
    *) return ;;
  esac
  [[ $fragment == (east|''|--east|élément:un/deux) ]] || return
  token_completion_offer zone file "${mode}_zone"
}
"""
        term = Terminal(self.home, config, self.env, self.one)
        self.addCleanup(term.close)
        for typed, expected in (
            ("deploy --region=", "deploy --region=literal_zone"),
            ("deploy --region --east", "deploy --region literal_zone"),
            ("deploy --verbose --east", "deploy --verbose --east:fallback"),
            ("deploy élément:un/deux", "deploy literal_zone"),
            ("deploy --region east", "deploy --region literal_zone"),
            ("deploy --region ", "deploy --region literal_zone"),
            ("deploy east", "deploy literal_zone"),
            ("deploy -- --east", "deploy -- literal_zone"),
            ("deploy --verbose east", "deploy --verbose literal_zone"),
            ("deploy ", "deploy literal_zone"),
            ("deploy $east", "deploy token_zone"),
            ("deploy --region=$east", "deploy --region=token_zone"),
            ("deploy --region=W", "deploy --region=W:fallback"),
            ("deploy --reg", "deploy --reg:fallback"),
            ("deploy OTHER", "deploy OTHER:fallback"),
            ("deploy >east", "deploy >east:fallback"),
            ("deploy # ", "deploy # :fallback"),
            ("deploy \'east", "deploy \'east:fallback"),
            ("echo SOLO_SHE", "echo SOLO_SHE:fallback"),
            ("echo --unknown=$NO_MATCH_ZZZ", "echo --unknown=$NO_MATCH_ZZZ:fallback"),
        ):
            with self.subTest(typed=typed):
                term.send(typed.encode() + b"\t")
                self.assertEqual(term.probe(), expected)
                term.send(b"\x15")
        term.send(b"deploy east tail" + b"\x1b[D" * 5 + b"\t")
        self.assertEqual(term.probe(), "deploy literal_zone tail")

    def test_picker_tab_and_backtab_cycle(self):
        term = Terminal(self.home, self.config, self.env, self.one)
        self.addCleanup(term.close)
        for keys, insertion in ((b"\x1b[Z", "second"), (b"\t\t", "first")):
            term.send(b"print $TWINS\t")
            term.until(b"Enter  insert")
            term.send(keys + b"\r")
            term.until(b"\x1b[?1049l")
            self.assertEqual(term.probe(), "print " + insertion)
            term.send(b"\x15")

    def test_native_directory_components(self):
        (self.one / "home/randall/projects").mkdir(parents=True)
        config = self.config.replace("bindkey '^I' fallback", "autoload -Uz compinit; compinit -D")
        term = Terminal(self.home, config, self.env, self.one)
        self.addCleanup(term.close)
        term.send(b'cd h/r/p\t')
        self.assertEqual(term.probe().rstrip("/ "), "cd home/randall/projects")

    def test_picker_options(self):
        capture = self.home / "capture-fzf"
        capture.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
        capture.chmod(0o755)
        for value in (None, "", "Filter $tokens > "):
            with self.subTest(value=value):
                setting = ("unset TOKEN_COMPLETION_PROMPT" if value is None
                           else "TOKEN_COMPLETION_PROMPT=" + shlex.quote(value))
                run = self.zsh(SOURCE + "\n" + setting + '\n_tc_fzf="$HOME/capture-fzf"\n_tc_pick')
                self.assertEqual(run.returncode, 0, run.stderr)
                args = run.stdout.splitlines()
                self.assertIn("--prompt=" + ("› " if value is None else value), args)
                self.assertIn("--no-border", args)
                self.assertFalse(any(arg.startswith("--border-label") for arg in args))

    def test_zle(self):
        term = Terminal(self.home, self.config, self.env, self.one)
        self.addCleanup(term.close)
        for typed in ("echo >$SOLO", "echo 2>$SOLO", "false || $SOLO", "x=1 $SOLO", "time $SOLO",
                      "command -p $SOLO", "echo # $SOLO", "echo $SOLO # $SOLO", "env $SOLO",
                      "print '$SOLO", 'print "$SOLO', "echo \\$SOLO", "echo ${SOLO", "echo $NO_MATCH_ZZZ"):
            term.send(typed.encode()+b"\t")
            self.assertEqual(term.probe(), typed+":fallback")
            term.send(b"\x15")
        term.send(b'printf --flag=$SOLO_SHE\t')
        self.assertEqual(term.probe(), 'printf --flag="$SOLO_SHELL"')
        term.send(b'\x15print --flag=$SOLO_SHE tail\x1b[D\x1b[D\x1b[D\x1b[D\x1b[D\t')
        self.assertEqual(term.probe(), 'print --flag="$SOLO_SHELL" tail')
        term.send(b'!')
        self.assertEqual(term.probe(), 'print --flag="$SOLO_SHELL"! tail')
        term.send(b'\x05\x15printf $LAB_EN\t')
        screen = term.until(b'Look up ')
        screen += term.until(b'Enter  insert')
        if b'[catalog]' not in screen:
            screen += term.until(b'[catalog]')
        self.assertNotIn(b'secret one', screen)
        term.send(b"\x1b")
        term.until(b"\x1b[?1049l")
        self.assertEqual(term.probe(), "printf $LAB_EN")
        term.send(b'\t')
        term.until(b'Look up ')
        term.send(b'.env')
        term.until(b'1/')
        term.send(b'\r')
        term.until(b'\x1b[?1049l')
        line = term.probe()
        self.assertEqual(line, 'printf "$(token-completion dotenv LAB_ENV)"')
        term.send(b'\r')
        term.until(b'secret one')
        term.until(b'READY>')
        self.assertIn(line, (self.home/"history").read_text())
        term.send(f'cd ../two; {line}\r'.encode())
        term.until(b'secret two')
        term.until(b'READY>')
        term.send(b'print $TWINS\t')
        term.until(b'Look up ')
        term.send(b'\x1b[B\r')
        term.until(b"\x1b[?1049l")
        self.assertEqual(term.probe(), 'print second')

    def test_keymaps_and_resourcing(self):
        config = """
bindkey -e
mark_fallback() { LBUFFER+=':chain'; }
zle -N mark_fallback
bindkey -M emacs '^I' mark_fallback
bindkey -M viins '^I' backward-char
""" + SOURCE + """
token_parent_wrapper() { zle token-completion-emacs; }
zle -N token_parent_wrapper
bindkey '^I' token_parent_wrapper
""" + SOURCE + """
print -r -- "$_tc_fallbacks[token-completion-emacs] $_tc_fallbacks[token-completion-viins]" > "$HOME/keys"
bindkey '^I' >> "$HOME/keys"
probe_widget() { print -rn -- "$BUFFER" > "$HOME/buffer"; }
zle -N probe_widget
bindkey '^X' probe_widget
PS1='READY> '
"""
        term = Terminal(self.home, config, self.env, self.one)
        self.addCleanup(term.close)
        self.assertEqual((self.home/"keys").read_text().splitlines(),
                         ["mark_fallback backward-char", '"^I" token_parent_wrapper'])

        term.send(b"print ordinary\t")
        self.assertEqual(term.probe(), "print ordinary:chain")

    def test_help_plain_when_piped(self):
        check = self.zsh(SOURCE + "\ntoken-completion --help | cat")
        self.assertIn("providers install", check.stdout)
        self.assertNotIn("\x1b", check.stdout)
        self.assertEqual(check.stderr, "")


if __name__ == "__main__":
    unittest.main()
