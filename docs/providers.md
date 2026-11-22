# Provider sources and pins

Put `TOKEN_COMPLETION_PROVIDERS` in your zsh configuration **before** sourcing
the plugin. This example selects two built-ins, an optional provider from
another loaded plugin, one file you edit locally and one provider tracked from
GitHub. Replace the example path and repository with your own.

```zsh
typeset -ga TOKEN_COMPLETION_PROVIDERS=(
  shell
  dotenv
  team_tokens
  "$HOME/.config/zsh/providers/my-colors.zsh"
  'github:YOUR-NAME/providers@stable:cloud/regions.zsh'
)
```

| Entry | How it becomes available |
| --- | --- |
| `shell`, `dotenv` | Built-in shell parameter and dotenv key providers. |
| `ini`, `toml`, `json`, `yaml` | Built-in document providers. Configure their file arrays or run `token-completion files`. |
| `team_tokens` | A loaded zsh function implements the named provider ABI. Either plugin may load first. |
| Absolute file path | The plugin sources your local file. Use this form during provider development. |
| `github:OWNER/REPO@REF:FILE` | The manager locks a commit and materializes its verified files under your XDG cache. |

Leaving the array unset enables the built-ins and available named providers.
An explicitly empty array disables all providers. Provider names must be
unique across the configured entries. A provider returning an identical token
name to a different provider is normal: select the source you want in the picker.

## Install and update GitHub providers

```zsh
token-completion providers list
token-completion providers install
token-completion providers update
```

**List** shows the selected sources in order. Named sources can be waiting for
another plugin to load. GitHub entries show their locked commit or a recovery
hint. The command uses local data and works offline.

**Install** resolves previously unpinned GitHub entries. If a lock exists, it
uses that exact commit, even when the configured branch has advanced.
Deleting the provider cache and running `install` reconstructs the locked code.
Keep the lockfile if you want reproducible installations on other machines.

**Update** re-resolves configured refs. A changed ref displays its previous and
new commit. Use a release tag or a full 40-digit GitHub commit SHA to select a
specific version; use a branch to follow a channel. Both lightweight and
annotated tags work. If a branch and a tag share a name, choose a commit SHA.

Both commands validate the entire selected set before replacing the lock. A
failed update exits nonzero and retains the previous pins. Running either
command after removing an entry from the array drops its pin. Older cached
objects can be cleared by deleting the provider cache. The shell running the
command loads the selected versions on success. Other open shells retain their
loaded versions until you start a fresh shell.

## Files and verification

| Location | Contents |
| --- | --- |
| `${TOKEN_COMPLETION_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/token-completion}/providers.lock` | A portable JSON file with format version, GitHub references, provider IDs, full commits and Git tree IDs. |
| `${XDG_CACHE_HOME:-$HOME/.cache}/token-completion/providers/` | Verified source snapshots addressed by repository and commit. |

The manager fetches over HTTPS during explicit install and update. The provider
file must resolve to a regular file inside the selected commit. Relative
symlinks with targets inside the same snapshot are supported. Git submodules
and links escaping the tree require a different provider distribution.

Before sourcing the pinned code the plugin checks the cached files against the
locked Git tree. This covers file contents, executable bits, symbolic links,
and extra files. A missing or edited snapshot disables that source until
`providers install` repairs it. Other providers stay available. A pin identifies
the exact code that runs. Configure providers from authors you trust: their
zsh functions run with your shell's privileges.

## A minimal provider

Save this as `~/.config/zsh/providers/my-colors.zsh` and add its absolute path
to the provider array. Then type `print -r -- $BRAND_C` and press Tab. The
editable command will contain the literal color name as a single argument.

```zsh
TOKEN_PROVIDER_ID=my_colors
typeset -g token_provider_my_colors_version=1

token_provider_my_colors_query() {
  emulate -L zsh
  local color='deep blue'
  token_completion_emit BRAND_COLOR colors "${(qqqq)color}"
}
```

| Member | Required | Meaning |
| --- | --- | --- |
| `TOKEN_PROVIDER_ID` | File providers | Identifier declared by the source file, using letters, digits and underscores and beginning with a letter or underscore. |
| `token_provider_ID_version` | Yes | Set this global parameter to `1`. Replace `ID` with the identifier above. |
| `token_provider_ID_query` | Yes | Called with the typed fragment as its first argument. Call `token_completion_emit KEY LABEL INSERTION` for each candidate. |
| `token_provider_ID_prepare` | No | Optional hook for starting inexpensive preparation when the provider becomes available. Return promptly; queries must also work cold. |

Keep top-level file code limited to declaring the ID, ABI and functions. The
GitHub manager sources the file in a separate zsh process to check its ABI before
committing a pin. Provider source files stay read-only in normal use: put
generated data in a separate cache. A query runs in the shell where completion
was requested and can inspect the current working directory. Supply a concise
label and an already shell-quoted insertion. For example, the colors provider
uses zsh's `${(qqqq)color}` to quote arbitrary literal data. Separate labels and
insertions let providers display a key while inserting a lookup expression.

## Command and option context

Implement `token_provider_ID_argument_context MODE FRAGMENT COMMAND OPTION FORM [ARG ...]`
to offer candidates at specific arguments. The widget supplies `literal` for
unmarked text and `token` for a fragment beginning with `$`. The fragment
excludes the marker. An empty fragment is meaningful in either mode.

| Form | Option and preceding arguments |
| --- | --- |
| `equals` | `--profile=wo` supplies `--profile` and the argv before that option. |
| `separate` | `--profile wo` supplies `--profile` and the argv before that option. The provider decides whether the preceding word takes a value. |
| `positional` | The option is empty. Preceding arguments count from the command, excluding redirections. The first argument has no preceding arguments. |

The provider decides whether it owns this position and what to do with an empty
fragment. For a match, call `token_completion_scope` and then offer candidates.
For an undeclared position, return without claiming it. If a declared option
requests native completion, claim it and offer zero rows. An unclaimed
`separate` form can be offered to the same provider again as `positional`,
with the flag added back to the preceding argv. This lets a provider distinguish
a flag that consumes a value from a standalone switch. Unmarked text never triggers a global token search. The
widget saves the original argument span so insertion preserves an adjacent
`--option=` and text after the cursor.

For a command that takes one project directory name, register an argument
context hook in your provider. This example accepts a bare name in the
first argument and opens the picker on an empty argument:

```zsh
token_provider_projects_argument_context() {
  [[ $1 == literal && $3 == project && $5 == positional && $# == 5 ]] || return 0
  token_completion_scope
  token_completion_directory "$2" "${PROJECT_ROOT:-$HOME/projects}"
}
```

`token_completion_directory FRAGMENT ROOT` reads the immediate visible
directories under the supplied root on every request. Symlinks to directories
participate. An exact, case-sensitive basename wins, otherwise matching uses
the same ranked, case-insensitive fuzzy filter as shell variables. The picker
inserts the basename as one quoted shell argument and escapes control
characters for display. The caller supplies the resolved root and owns the
position and empty-fragment policy. Use
`token_completion_directory_matches FRAGMENT ROOT` to read the same ordered
basenames as NUL-delimited records from another shell function.

Providers using the earlier option-only interface can keep
`token_provider_<id>_context FRAGMENT COMMAND OPTION FORM [ARG ...]`.
The widget uses it when the provider has no argument-context hook, at `--option=$fragment` and
`--option $fragment` positions (also short options). `FORM` is `equals` or
`separate`. The fragment excludes the dollar sign and can be empty. Additional
arguments are the preceding words of the active command with shell quoting
removed, excluding the active option and redirections. The widget never
executes substitutions to discover context.

For a matching declaration call `token_completion_scope` to suppress generic
token queries for this position. Use `token_completion_offer DISPLAY SOURCE
INSERTION` for each already-matched candidate. The provider owns path matching;
the shared picker handles collisions, including identical display names. A
branch can insert a refined `$path.` for the next Tab. A leaf can insert a
quoted deferred lookup. All insertions leave the line editable. With no
candidates the widget delegates to the existing Tab binding. Returning from
the hook without claiming scope leaves ordinary providers available.

### Delegate to a document provider

Choose the helper that matches the requested insertion. Both show the same names and provenance in the picker.

| Helper | What happens on selection |
| --- | --- |
| `token_completion_file_literal` | Resolve the chosen leaf, run its guard if present, and quote the result as a single shell word. History stores the literal. |
| `token_completion_file` | Insert a quoted lookup expression. Replay reads the recorded path, and zsh trims trailing newlines. |

Both helpers allow another Tab to descend from a selected container. The
literal helper inserts a bare path prefix and the deferred helper keeps its
`$` marker. Canceling the picker performs no value lookup.

For example, a deferred source uses the following signature.

```zsh
token_completion_file [--exact] "$fragment" "$format" "$select" "$file" [GUARD CMD OPTION [DECLARED_FORMAT]]
```

The shared loader implements the four built-in file formats. A third-party
query or context provider can call this API to use any of those enabled parsers.

The first four arguments are the typed fragment without `$`, format name,
selection policy and source file. Pass `auto` as the format to select an enabled
document parser using the selection policy, filename and, when necessary, the
document contents. A saved lookup records the selected parser ID. For example,
`section-names` requires the INI provider even for an extensionless file.
The guard, command and option are optional as a group. The helper emits matching
rows through `token_completion_offer`. A hook that needs exclusive candidates
calls `token_completion_scope` first.

| Selection | Result |
| --- | --- |
| `leaf-values` | Navigate maps and arrays one level at a time. A leaf lookup returns the scalar value. This is the policy used by the four built-in document providers. |
| `keys` | Offer existing top-level map keys. A lookup returns the selected key name. |
| `section-names` | INI only. Offer literal section names. A lookup can return the section name but cannot descend into a section. |

INI option names preserve case; interpolation is disabled. JSON and YAML use
their parsers’ supported encodings, INI accepts UTF-8 with an optional BOM,
and TOML uses UTF-8. Supported map keys are nonempty strings without NUL.
Direct `lookup` output preserves all whitespace. Zsh removes trailing newlines
from a value captured by ordinary command substitution. Boolean
and null leaves use JSON spellings; date and time values use ISO format.
Lookups diagnose unsupported scalars or missing paths without printing source
values. Provider code can reuse this helper with explicitly configured files
even if that format's standalone file array is empty.

By default, a deferred file completion inserts a command such as
`"$(token-completion lookup json leaf-values ./settings.json region)"`.
Two opt-ins change its behavior independently:

- Supply the leading `--exact` argument to preserve trailing newlines in the
  command argument. The generated expression uses `lookup-exact`, which adds a
  final dot on success so zsh can strip the dot after capturing the value.
- Supply a zsh function as `GUARD` with its command and option to check the
  live declaration before each lookup. Previously generated guarded lookups
  continue to call their guard.

With a guard, the underlying lookup takes these arguments:

```text
token-completion lookup FORMAT SELECT FILE PATH GUARD CMD OPTION [DECLARED_FORMAT]
```

At execution, `GUARD CMD OPTION FORMAT SELECT FILE` must succeed before the
plugin opens the file. A removed guard or rejected declaration fails the lookup.
If the contextual hook also supplied `DECLARED_FORMAT`, the guard receives
`CMD OPTION DECLARED_FORMAT FORMAT SELECT FILE`. This lets it distinguish an
inferred declaration (`auto`) from an explicit declaration on replay.
The manifest provider checks its live declarations. Token-completion handles
parsing, component matching, display and replay.

The context hook is additive. Existing version-1 providers can keep emitting
flat candidates through `token_completion_emit`. To exercise the cross-plugin
PTY test, set `SHELL_OVERRIDES_TEST_PLUGIN` to the external plugin's absolute
entry point and put a PyYAML-capable Python on `PATH`.
