"""File completion keeps leaf values out of candidate rows until acceptance."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_completion import SOURCE, Terminal


class LiteralFiles(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name)
        self.env = dict(os.environ, HOME=str(self.home), ZDOTDIR=str(self.home),
                        XDG_CONFIG_HOME=str(self.home/"config"), XDG_CACHE_HOME=str(self.home/"cache"),
                        TERM="xterm-256color")
        self.doc = self.home / "file spaces ' and $HOME.json"
        self.env["DOC"] = str(self.doc)
        self.guard_log = self.home / "guard-log"
        self.fixture = """
TOKEN_COMPLETION_PROVIDERS=(json ini)
""" + SOURCE + r"""
typeset -a _tc_insertions _tc_context_rows
typeset -A _tc_literal_fields
typeset _tc_insertion
permit() {
  print -rn -- "$1|$2|$3|$4|$5|$6" >> "$HOME/guard-log"
  [[ -e "$HOME/allow" ]]
}
"""
        (self.home/"allow").touch()

    def run_zsh(self, script):
        return subprocess.run(["zsh", "-dfc", self.fixture + "\n" + script],
                              cwd=self.home, env=self.env, capture_output=True, timeout=10)

    def test_selected_leaf_argv_and_exact_quoting(self):
        for value in ("", "\n\n", ".", "literal $HOME; $(touch unintended) ' \\ *\t\n\n", "é.\n"):
            with self.subTest(value=value):
                self.guard_log.unlink(missing_ok=True)
                self.doc.write_text(json.dumps({"a.b": value}))
                result = self.run_zsh(r"""
token_completion_file_literal 'a%2E' json leaf-values "$DOC" permit deploy --opt auto
[[ $#_tc_insertions == 1 && -z $_tc_insertions[1] && ! -e "$HOME/guard-log" ]] || exit 10
_tc_accept_insertion 1 || exit 11
# Execute the editable word through zsh's parser, as Enter would.
eval "set -- $_tc_insertion"
(( $# == 1 )) || exit 12
print -rn -- "$1"
""")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.decode(), value)
                self.assertFalse((self.home/"unintended").exists())
                self.assertEqual(self.guard_log.read_text(), f"deploy|--opt|auto|json|leaf-values|{self.doc}")

    def test_branch_and_deferred_paths_do_not_resolve(self):
        self.doc.write_text('{"group":{"zone":"private"}}')
        result = self.run_zsh(r"""
token_completion_file_literal gro json leaf-values "$DOC" permit deploy --opt auto
token_completion_file gro json leaf-values "$DOC" permit deploy --opt auto
token_completion_file group.z json leaf-values "$DOC" permit deploy --opt auto
for i in 1 2 3; do
  _tc_accept_insertion $i || exit 10
  print -r -- "$_tc_insertion"
done
[[ ! -e "$HOME/guard-log" ]]
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        first, second, deferred = result.stdout.decode().splitlines()
        self.assertEqual((first, second), ("group.", "$group."))
        self.assertIn("token-completion lookup", deferred)
        self.assertNotIn("private", deferred)

    def test_bare_branch_starting_with_dash_can_be_completed_again(self):
        self.doc.write_text('{"-profiles":{"work":"west"},"%2Dprofiles":{"work":"other"}}')
        result = self.run_zsh(r"""
token_completion_file_literal '' json leaf-values "$DOC"
_tc_accept_insertion 1 || exit 1
print -r -- "$_tc_insertion"
token_completion_file_literal "${_tc_insertion}wo" json leaf-values "$DOC"
_tc_accept_insertion 3 || exit 2
print -r -- "$_tc_insertion"
token_completion_file_literal '%252Dprofiles.wo' json leaf-values "$DOC"
_tc_accept_insertion 4 || exit 3
print -r -- "$_tc_insertion"
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [b"%2Dprofiles.", b"west", b"other"])

    def test_opaque_row_selects_one_live_file_and_revocation_preserves_insertion(self):
        self.doc.write_text('{"zone":"first-private"}')
        (self.home/"other.json").write_text('{"zone":"second-private"}')
        result = self.run_zsh(r"""
token_completion_file_literal z json leaf-values "$DOC" permit deploy --opt auto
token_completion_file_literal z json leaf-values ./other.json permit deploy --opt auto
[[ ${(j: :)_tc_context_rows} != *private* && ! -e "$HOME/guard-log" ]] || exit 10
print -r -- '{"zone":"new value"}' > ./other.json
_tc_accept_insertion 2 || exit 11
eval "print -r -- $_tc_insertion"
rm "$HOME/allow"
_tc_insertion=UNCHANGED
_tc_accept_insertion 1 && exit 12
[[ $_tc_insertion == UNCHANGED ]] || exit 13
touch "$HOME/allow"
rm "$DOC"
_tc_accept_insertion 1 && exit 14
[[ $_tc_insertion == UNCHANGED ]]
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"new value\n")
        self.assertIn(b"no longer authorized", result.stderr)
        self.assertNotIn(b"first-private", result.stderr)
        self.assertNotIn(b"second-private", result.stderr)

    def test_names_only_policy_inserts_literal_name(self):
        file = self.home / "accounts"
        file.write_text("[work.qa +east]\nsecret=HIDDEN_VALUE\n")
        result = self.run_zsh(r"""
token_completion_file_literal work ini section-names ./accounts
_tc_accept_insertion 1 || exit 10
eval "set -- $_tc_insertion"
[[ $# == 1 ]] || exit 11
print -r -- "$1"
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"work.qa +east\n")

    def test_real_picker_cancel_revoke_accept_and_replay(self):
        (self.home/"lookup.json").write_text('{"regionA":"top secret A", "regionB":"top secret B"}')
        config = r"""
bindkey -e
fallback() { LBUFFER+=':fallback'; }
zle -N fallback
bindkey '^I' fallback
TOKEN_COMPLETION_PROVIDERS=(json scoped)
typeset -g token_provider_scoped_version=1
token_provider_scoped_query() { :; }
permit() { print -r -- "$@" >> "$HOME/guard-log"; [[ -e "$HOME/allow" ]]; }
token_provider_scoped_argument_context() {
  local mode=$1 fragment=$2 cmd=$3 opt=$4 form=$5
  [[ $cmd == runjob && $opt == --pick && $form == equals && $mode == literal ]] || return 0
  token_completion_scope
  token_completion_file_literal "$fragment" json leaf-values ./lookup.json permit "$cmd" "$opt" auto
}
runjob() {
  print -rn -- "$1" > "$HOME/argv"
  print LITERAL-PRINTED
}
""" + SOURCE + r"""
HISTFILE=$HOME/history
HISTSIZE=100
SAVEHIST=100
setopt inc_append_history
capture_buffer() { print -rn -- "$BUFFER" > "$HOME/probe.tmp"; command mv "$HOME/probe.tmp" "$HOME/buffer"; }
zle -N capture_buffer
bindkey '^X' capture_buffer
PS1='READY> '
"""
        term = Terminal(self.home, config, self.env, self.home)
        self.addCleanup(term.close)

        def picker():
            term.send(b'\t')
            screen = term.until(b'Enter  insert')
            if b'regionB' not in screen:
                screen += term.until(b'regionB')
            self.assertNotIn(b'top secret', screen)

        def accept_b():
            term.send(b'regionB')
            term.until(b'1/')
            term.send(b'\r')
            term.until(b'\x1b[?1049l')

        original = "runjob --pick=reg"
        term.send(original.encode())
        picker()
        self.assertFalse(self.guard_log.exists())
        term.send(b'\x1b')
        term.until(b'\x1b[?1049l')
        self.assertEqual(term.probe(), original)
        self.assertFalse(self.guard_log.exists())

        picker()
        (self.home/"allow").unlink()
        accept_b()
        self.assertEqual(term.probe(), original)
        self.assertTrue(self.guard_log.exists())
        self.guard_log.unlink()

        (self.home/"allow").touch()
        picker()
        self.assertFalse(self.guard_log.exists())
        accept_b()
        literal = term.probe()
        self.assertNotIn("lookup", literal)
        self.assertTrue(self.guard_log.exists())
        self.assertFalse((self.home/"argv").exists())
        term.send(b'\r')
        term.until(b'LITERAL-PRINTED')
        term.until(b'READY>')
        self.assertEqual((self.home/"argv").read_text(), "--pick=top secret B")
        self.assertIn(literal, (self.home/"history").read_text())
        (self.home/"empty-dir").mkdir()
        term.send(f'cd empty-dir; {literal}\r'.encode())
        term.until(b'LITERAL-PRINTED')
        term.until(b'READY>')
        self.assertEqual((self.home/"argv").read_text(), "--pick=top secret B")


if __name__ == "__main__":
    unittest.main()
