# token-completion

Type `$REG` in a zsh command and press **Tab**. A single match drops straight into the line. If shell variables, project-local settings, or other providers offer several matches, a compact picker opens below the command and shows who supplied each one. Type to filter. **Tab** moves down and **Shift-Tab** moves up, wrapping at either end. **Enter** inserts the highlighted match into the editable line. **Escape** restores the original line.

![Below the shell command, the picker shows the same key from shell and .env, with Tab and Shift-Tab to move, Enter to insert and Escape to cancel.](docs/picker.png)

Your command stays editable. The picker displays names and sources only. Your usual completion widget still handles filenames, unsupported quoting, command positions, and tokens with no match.

## Try it

From a checkout, start a disposable shell and source the plugin:

```zsh
zsh -f
source ./token-completion.plugin.zsh
DEMO_REGION='North America'
```

Now **type** `print -r -- $DEMO_REG` and press Tab. The resulting line is `print -r -- "$DEMO_REGION"`. Press Enter to execute it. You can also type `print --region=$DEMO_REG` and complete just the value to produce `print --region="$DEMO_REGION"`.

To try a collision, stay in that shell and create a scratch directory:

```zsh
cd "$(mktemp -d)"
print -r -- 'DEMO_REGION="South Pacific"' > .env
```

Complete `print -r -- $DEMO_REG` again and pick `[.env]`. The editable result is `print -r -- "$(token-completion dotenv DEMO_REGION)"`. A history replay reads the `.env` file in the directory where the replay runs.

## Dependencies

For a source installation, put the commands you need on `PATH`. The Nix package supplies the helper executables. The table lists versions verified on Linux. Optional parser entries report tested versions; earlier compatible releases may work. The Nix package includes PyYAML and supplies tomli when its Python needs it.

| Dependency | Minimum verified version | When you need it |
| --- | --- | --- |
| zsh | 5.9 | The Tab widget runs in interactive zsh with ZLE enabled. |
| fzf | 0.52.1 | Filters NUL-delimited candidates and displays the picker when several match. |
| Python 3 | 3.10.15 (3.10 series) | Reads dotenv, INI and JSON files, manages provider pins and verifies local snapshots. Python 3.11+ also reads TOML using its standard-library `tomllib`. |
| PyYAML (optional) | 6.0.1 tested on Python 3.11.9; 6.0.3 tested on Python 3.10.15 | Reads YAML documents with its safe loader. For a source install, use `python3 -m pip install PyYAML` in the interpreter environment on `PATH`. |
| tomli (optional) | 2.4.0 tested on Python 3.10.15 | Reads TOML on Python 3.10. Use `python3 -m pip install tomli` there. Python 3.11+ supplies its own TOML reader. |
| Git | 2.44.1 | Clones a source installation and fetches GitHub providers during explicit install or update. Completion from a verified cache works offline. |
| Python 3 for tests | 3.11.9 (3.11 series) | The terminal tests use `termios.tcsetwinsize` to size a disposable PTY. |

## Installation and updates

A default clone follows `stable`, the latest tested release. Follow the
`development` branch to try merged changes before release, or select an
immutable version tag such as `v0.2.0` to stay on one release. A Nix input
stays at its locked revision until you update it. Contributors target
`development`. [Contribution and release guide](CONTRIBUTING.md)

Use `stable` for released changes or `development` for ongoing development. Version tags such as `v0.1.0` select a fixed release. After an update, open a fresh interactive shell and run `token-completion providers list` to verify loading.

### Manual

```zsh
plugin_dir="${XDG_DATA_HOME:-$HOME/.local/share}/zsh/token-completion"
git clone --branch stable https://github.com/randallwick/token-completion.git "$plugin_dir"
```

Add this after completion initialization in `.zshrc`:

```zsh
source "${XDG_DATA_HOME:-$HOME/.local/share}/zsh/token-completion/token-completion.plugin.zsh"
```

Replace `stable` in the clone command to choose another channel or a version tag. Update a channel checkout with `git -C "$plugin_dir" pull --ff-only`. In a fixed checkout, fetch tags and explicitly select the desired release.

### Oh My Zsh

```zsh
plugin_dir="${ZSH_CUSTOM:-${ZSH:-$HOME/.oh-my-zsh}/custom}/plugins/token-completion"
git clone --branch stable https://github.com/randallwick/token-completion.git "$plugin_dir"
```

Add `plugins+=(token-completion)` after your plugins array and before sourcing `oh-my-zsh.sh`. Custom plugin checkouts use the same channel or tag update commands as a manual install. [Oh My Zsh custom plugins](https://github.com/ohmyzsh/ohmyzsh/wiki/Customization)

### Antidote

Add `randallwick/token-completion branch:stable` to `.zsh_plugins.txt`, then use your usual `antidote load`. To advance channel checkouts, run `antidote update`. Use `branch:development` for the development channel, or `pin:<full-commit-SHA>` for the commit of a fixed release. [Antidote bundle options](https://antidote.sh/options) · [Antidote commands](https://antidote.sh/commands)

### NixOS or Home Manager

Add the plugin as a source input in your flake:

```nix
inputs.token-completion = {
  url = "github:randallwick/token-completion/stable";
  flake = false;
};
```

In a Home Manager module that receives `inputs`:

```nix
{ inputs, pkgs, ... }:
let
  plugin = pkgs.callPackage (inputs.token-completion + "/package.nix") {};
in {
  programs.zsh.enable = true;
  programs.zsh.plugins = [{
    name = "token-completion";
    src = plugin;
    file = "share/token-completion/token-completion.plugin.zsh";
  }];
}
```

For a NixOS configuration with Home Manager, the zsh plugin belongs in the user's Home Manager module. For system zsh without Home Manager, use the same package and add `source ${plugin}/share/token-completion/token-completion.plugin.zsh` to `programs.zsh.interactiveShellInit`. If other widgets wrap Tab, load this plugin after the widget it should use as its fallback and before any widget that should wrap it.

The input URL selects the branch or release tag. `flake.lock` records an exact revision. `nix flake update token-completion` advances the pin; a normal rebuild/activation makes that revision available to new shells. [Home Manager zsh plugin fields](https://nix-community.github.io/home-manager/options/home-manager/programs/zsh.html)

## Configuration reference

Set shell variables before sourcing the plugin in `.zshrc`. The provider array is a zsh array in the current shell; it need not be exported.

| Setting | Accepted value | Default |
| --- | --- | --- |
| `TOKEN_COMPLETION_PROVIDERS` | Ordered array of provider IDs, absolute local provider files or GitHub references | The six built-ins (`shell`, `dotenv`, `ini`, `toml`, `json`, `yaml`) and compatible providers defined in the shell. An explicitly empty array disables all providers. |
| `TOKEN_COMPLETION_INI_FILES` | Ordered zsh array of INI document paths | Empty. Add paths to offer their leaf values through the `ini` provider. |
| `TOKEN_COMPLETION_TOML_FILES` | Ordered zsh array of TOML document paths | Empty. Add paths to offer their leaf values through the `toml` provider. |
| `TOKEN_COMPLETION_JSON_FILES` | Ordered zsh array of JSON document paths | Empty. Add paths to offer their leaf values through the `json` provider. |
| `TOKEN_COMPLETION_YAML_FILES` | Ordered zsh array of YAML document paths | Empty. Add paths to offer their leaf values through the `yaml` provider. |
| `TOKEN_COMPLETION_CONFIG_DIR` | Directory containing `providers.lock` | `${XDG_CONFIG_HOME:-$HOME/.config}/token-completion` |
| `XDG_CACHE_HOME` | Base directory for downloaded provider checkouts | `$HOME/.cache`; the plugin uses the `token-completion` subdirectory |
| `TOKEN_COMPLETION_PROMPT` | Literal picker prompt, read each time the picker opens. Set to an empty string to hide it. | `› ` (chevron and space) |
| `NO_COLOR` | Set to a nonempty string to disable decorative color | Use the terminal theme with an accent on the prompt and selection. Piped CLI output is plain. |

**Use just the project-local source.** Put `TOKEN_COMPLETION_PROVIDERS=(dotenv)` before loading the plugin. An installed plugin that exports a compatible named provider can join the array by ID, in either load order. Its own settings and data remain with that plugin.

**Use a working tree.** Add a local file by its absolute path. This example selects the shell and a provider under development:

```zsh
TOKEN_COMPLETION_PROVIDERS=(shell "$HOME/src/colors/provider.zsh")
```

**Use a remote provider.** Select a repository, a ref and the provider file in that repository. Substitute the coordinates of a provider you trust:

```zsh
TOKEN_COMPLETION_PROVIDERS=(shell dotenv 'github:OWNER/REPO@stable:providers/colors.zsh')
```

Run `token-completion providers install` after loading the plugin. To advance the locked revision, run `token-completion providers update`. A fresh shell or Tab lookup reads installed code offline. The lockfile can be versioned and reused with a fresh cache. Provider code runs with your shell's privileges. [Provider installation and lockfile details](docs/providers.md)

### Commands

| Command | Behavior |
| --- | --- |
| `token-completion --help` | Show the command summary. |
| `token-completion providers list` | Show configured providers, status and pinned GitHub revisions. |
| `token-completion providers install` | Resolve new entries, reuse existing pins and restore missing cached sources. |
| `token-completion providers update` | Resolve configured refs again and report old and new pins. |
| `token-completion files [--format FORMAT] -f FILE ...` | Register documents in this shell. Repeat `-f` or `--file`. Use `--format` before a file to select its parser explicitly. |
| `token-completion lookup FORMAT SELECT FILE PATH [GUARD CMD OPTION [DECLARED_FORMAT]]` | Resolve an exact document path at execution time. An optional provider guard must approve the source before the lookup. |
| `token-completion dotenv KEY` | Print the current directory's literal value with no trailing newline. A missing key/file produces a diagnostic and a nonzero exit. An explicitly empty value succeeds quietly. |

### Dotenv grammar

The built-in dotenv provider reads `.env` on completion and again when an inserted lookup executes. It accepts single-line UTF-8 assignments, optional `export`, optional spaces around `=`, and single- or double-quoted literal values. A trailing comment follows a quoted value or starts with whitespace and `#` in an unquoted value. Dollar signs and backslashes are literal. Malformed lines are skipped, and the last assignment to a key wins. A missing lookup inside ordinary quoted command substitution still passes one empty argument to the surrounding command.

## Complete a path inside a document

The INI, TOML, JSON and YAML providers work directly in token-completion.
Each starts with an empty file list. In a scratch directory, try a JSON source:

```zsh
print -r -- '{"deployment":{"region":"eu-west-1"}}' > settings.json
token-completion files -f ./settings.json
```

Type `print -r -- $depl.reg` and press Tab. A unique prefix resolves each level:
the editable line now contains a quoted `token-completion lookup` for
`deployment.region`. Press Enter to print `eu-west-1`. Replaying that line in
another directory reads **that directory’s** `settings.json`. Paths beginning with
`~` expand at lookup; use an absolute path for a fixed location.

Register multiple files in one call. Here only the extensionless `./config`
gets an explicit format:

```zsh
token-completion files -f ./settings.json --format toml -f ./config --file ./deploy.yml
```

A recognized extension selects its enabled provider, including either `.yaml` or
`.yml` for YAML. A malformed file with a recognized extension produces a
diagnostic from that selection. For unfamiliar or missing extensions, the loader
reads the file once and compares the interpretations offered by enabled parsers
that support the requested selection. If several produce the same structure
and value types, the loader chooses the first in its fixed order (JSON, TOML,
INI, YAML). If interpretations differ, the diagnostic names the candidates. Use `--format FORMAT -f FILE` to choose one.
For example, an extensionless file containing `[deploy]` followed by
`region = "west"` preserves the quotes in INI and strips them in TOML.

Registration records the chosen provider. Repeated Tab presses and history
replay use that provider while reading fresh values from the source. Disabling
the provider prevents its saved lookups from running. Explicit spellings such
as `--json FILE` still work.

To keep the selection across shells, set the corresponding arrays in your zsh
configuration, for example:

```zsh
TOKEN_COMPLETION_JSON_FILES=(./settings.json ./config/overrides.json)
TOKEN_COMPLETION_TOML_FILES=(./pyproject.toml)
```

If you explicitly select providers, include each format you want, e.g.
`TOKEN_COMPLETION_PROVIDERS=(shell json toml)`. These file providers use
`leaf-values`. They read their documents when queried and show key paths with
source labels. Scalar contents appear only when the inserted lookup executes.

A dot separates levels and zero-based indices select array elements. A literal
dot inside a key is encoded as `%2E`; spaces appear as `%20`. On a branch
with several matching children the picker offers those children; choosing a
container refines the token for another Tab. Exact lookups preserve key case.
Missing keys and unreadable files produce a diagnostic and an empty argument
in the generated lookup. Present empty strings resolve quietly.

## Complete an argument with or without `$`

A provider can offer candidates for a particular command’s option or positional
argument. For example, a shell-overrides manifest can use the INI section names
in `~/.aws/credentials` for `aws --profile`. With markerless completion enabled
for that option, these interactions have different effects:

| Type, then press Tab | What choosing `work-read-dev` puts in the command |
| --- | --- |
| `aws --profile wo` or `aws --profile=wo` | The literal profile name, quoted as one argument. History keeps the selected name. |
| `aws --profile $wo` or `aws --profile=$wo` | A quoted lookup that reads the section name when the command runs or replays. |
| `aws --profile ` or `aws --profile=` | The manifest can offer all profile names here or delegate the empty value to native completion. |

The default deferred insertion uses ordinary zsh command substitution:

```zsh
aws --profile "$(token-completion lookup ini section-names \~/.aws/credentials work-read-dev)"
```

The manifest chooses what to offer on Tab. The saved command reads the recorded
file and key, including after a manifest edit. Zsh trims trailing newlines.
Providers can opt into a replay guard, exact newline capture, or both.

The picker displays document paths or names and their sources. Selecting a
literal leaf puts its value into the editable line and shell history. For
`section-names` the returned value is the section name itself. The INI entries
inside each profile stay out of the candidates and the command.

A provider can also match an argument by position, such as the first argument
to `deploy`. Bare text at undeclared positions uses the previous Tab binding.
[Manifest recipes](https://github.com/randallwick/shell-overrides#recipes) ·
[Contextual provider interface](docs/providers.md#command-and-option-context)

## Write a provider

Save this as `colors.zsh`, then add its absolute path to `TOKEN_COMPLETION_PROVIDERS`. The same functions can be exported by an existing zsh plugin; a user can select that provider by its ID.

```zsh
TOKEN_PROVIDER_ID=colors
typeset -g token_provider_colors_version=1

token_provider_colors_query() {
  emulate -L zsh
  local color
  for color in cerulean vermilion; do
    token_completion_emit "${(U)color}" palette "${(qqqq)color}"
  done
}
```

Type `print -- $CERU` and press Tab to insert a quoted literal. A provider can also insert a quoted parameter reference or a deferred lookup. The picker handles selection and zsh handles the inserted expression when the user executes the line.

| Interface | Contract |
| --- | --- |
| `TOKEN_PROVIDER_ID` | A file provider sets this to its shell-identifier ID while sourced. A provider already defined by name uses the identifier in its function names. |
| `token_provider_<id>_version` | Define as a global variable with the string value `1`. |
| `token_provider_<id>_query FRAGMENT` | Runs in the current shell. The fragment excludes the `$`. Emit zero or more candidates using the callback; the widget filters on the key. |
| `token_completion_emit KEY LABEL INSERTION` | Key is a shell identifier, label is human-readable provenance, insertion is the trusted zsh text to put into the buffer. Control characters in labels are sanitized. Different candidates may share a key and label. |
| `token_provider_<id>_prepare` | Optional function with no arguments. Return promptly. It runs once per enabled provider when available to completion. Late providers prepare at discovery; their query must work cold. |
| `token_completion_filter QUERY` | Filter NUL-delimited candidates from stdin using fzf. Matching records on stdout remain NUL-delimited. |

Keep provider files declarative at source time: installation may source them in a disposable shell to validate the interface. Put one-time setup in `prepare`. Use unique provider IDs; conflicting declarations receive a diagnostic from `providers list`. New shells pick up edits to local provider files.

## Development

From the checkout, run `python3 -m unittest discover -s tests -v` with zsh, Git, fzf and Python 3.11+ with PyYAML on `PATH`. Tests create disposable shell configurations, terminal sessions, provider repositories and caches. For interactive edits, start `zsh -f` in the checkout and source `./token-completion.plugin.zsh`; open a fresh disposable shell to pick up code changes.
