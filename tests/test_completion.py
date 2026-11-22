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
                self.assertIn("shell dotenv catalog", check.stdout)
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
        screen = term.until(b'token ')
        screen += term.until(b'Enter  insert')
        if b'[catalog]' not in screen:
            screen += term.until(b'[catalog]')
        self.assertNotIn(b'secret one', screen)
        term.send(b"\x1b")
        term.until(b"\x1b[?1049l")
        self.assertEqual(term.probe(), "printf $LAB_EN")
        term.send(b'\t')
        term.until(b'token ')
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
        term.until(b'token ')
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
