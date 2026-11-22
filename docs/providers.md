# Provider sources and pins

Put `TOKEN_COMPLETION_PROVIDERS` in your zsh configuration **before** sourcing
the plugin. This example selects both built-ins, an optional provider from
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
| `shell`, `dotenv` | Built into the completion plugin. |
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
