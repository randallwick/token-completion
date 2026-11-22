# Source after your usual completion initialization.
typeset -g _tc_root=${${(%):-%x}:A:h}
typeset -g _tc_python=python3 _tc_fzf=fzf
typeset -gA _tc_file_ids _tc_file_paths _tc_file_sources _tc_file_errors _tc_prepared _tc_fallbacks

# Reusable filter: one unmodified NUL-terminated result per matching input.
token_completion_filter() {
  FZF_DEFAULT_OPTS='' FZF_DEFAULT_OPTS_FILE='' command "$_tc_fzf" --read0 --print0 --ignore-case --filter "$@"
}

token_completion_emit() {
  emulate -L zsh
  setopt extendedglob
  (( $# == 3 && ${+_tc_insertions} )) || return 1
  [[ $1 == [A-Za-z_][A-Za-z0-9_]# ]] || return 1
  # Only printable provenance reaches the terminal. The insertion never leaves
  # this shell; the hidden ordinal identifies even identical display rows.
  local _tc_label=${2//[[:cntrl:]]/?}
  _tc_insertions+=("$3")
  _tc_rows+=("${#_tc_insertions}"$'\t'"$1"$'\t'"[$_tc_label]")
}

typeset -g token_provider_shell_version=1 token_provider_dotenv_version=1
token_provider_shell_query() {
  emulate -L zsh
  local _tc_key
  for _tc_key in "${_tc_parameter_names[@]}"; do
    [[ $_tc_key == _tc_* ]] && continue
    token_completion_emit "$_tc_key" shell '"$'${_tc_key}'"'
  done
  return 0
}

token_provider_dotenv_query() {
  emulate -L zsh
  [[ -r .env ]] || return 0
  local _tc_key
  while IFS= read -r _tc_key; do
    token_completion_emit "$_tc_key" .env '"$(token-completion dotenv '${_tc_key}')"'
  done < <(command "$_tc_python" "$_tc_root/dotenv.py" keys)
}

_tc_valid_provider() {
  emulate -L zsh
  setopt extendedglob
  [[ $1 == [a-zA-Z_][a-zA-Z_0-9]# ]] || return 1
  local _tc_version=token_provider_${1}_version
  [[ ${(P)_tc_version} == 1 ]] && (( $+functions[token_provider_${1}_query] ))
}

_tc_discover() {
  emulate -L zsh
  local _tc_fn _tc_id
  for _tc_fn in ${(ok)functions[(I)token_provider_*_query]}; do
    _tc_id=${${_tc_fn#token_provider_}%_query}
    _tc_valid_provider "$_tc_id" && _tc_available+=("$_tc_id")
  done
}

_tc_entries_for_shell() {
  emulate -L zsh
  if (( ${+TOKEN_COMPLETION_PROVIDERS} )); then
    _tc_entries=("${TOKEN_COMPLETION_PROVIDERS[@]}")
  else
    local -a _tc_available
    _tc_discover
    _tc_entries=(shell dotenv "${(@)_tc_available:#(shell|dotenv)}")
  fi
}

_tc_manager() {
  emulate -L zsh
  local -a _tc_available _tc_options _tc_owned=("${(@v)_tc_file_ids}")
  local _tc_id
  _tc_discover
  for _tc_id in "${_tc_available[@]}"; do
    _tc_options+=(--available "$_tc_id")
    (( ${_tc_owned[(Ie)$_tc_id]} )) || _tc_options+=(--reserved "$_tc_id")
  done
  if [[ $1 == list ]]; then
    local _tc_entry
    for _tc_entry in "${(@k)_tc_file_errors}"; do
      _tc_options+=(--unavailable "$_tc_entry" "${_tc_file_errors[$_tc_entry]}")
    done
  fi
  TOKEN_COMPLETION_CONFIG_DIR=${TOKEN_COMPLETION_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/token-completion} \
    XDG_CACHE_HOME=${XDG_CACHE_HOME:-$HOME/.cache} \
    command "$_tc_python" "$_tc_root/providers.py" "$1" "${_tc_options[@]}" -- "${_tc_entries[@]}"
}

_tc_inspect_file() (
  emulate -L zsh
  # Verify declarations in isolation before allowing a file to replace the
  # implementation of an already-loaded provider in the interactive shell.
  local TOKEN_PROVIDER_ID
  local _tc_fn _tc_var
  for _tc_fn in ${(k)functions[(I)token_provider_*]}; do unfunction "$_tc_fn"; done
  for _tc_var in ${(k)parameters[(I)token_provider_*_version]}; do unset "$_tc_var"; done
  source "$1" >/dev/null && _tc_valid_provider "$TOKEN_PROVIDER_ID" || return 1
  print -r -- "$TOKEN_PROVIDER_ID"
)

_tc_forget_file() {
  emulate -L zsh
  local _tc_id=${_tc_file_ids[$1]}
  unfunction "token_provider_${_tc_id}_query" 2>/dev/null
  unfunction "token_provider_${_tc_id}_prepare" 2>/dev/null
  unset "token_provider_${_tc_id}_version"
  unset "_tc_prepared[$_tc_id]" "_tc_file_ids[$1]" "_tc_file_paths[$1]" "_tc_file_sources[$1]"
}

_tc_load_files() {
  emulate -L zsh
  local -a _tc_entries
  _tc_entries_for_shell
  local -A _tc_resolved _tc_sources
  zmodload zsh/mapfile
  local _tc_entry _tc_path _tc_id _tc_previous TOKEN_PROVIDER_ID
  if (( ${_tc_entries[(I)github:*]} )); then
    while IFS= read -r -d '' _tc_entry && IFS= read -r -d '' _tc_path; do
      _tc_resolved[$_tc_entry]=$_tc_path
    done < <(_tc_manager resolve)
  fi
  for _tc_entry in "${_tc_entries[@]}"; do
    if [[ $_tc_entry == /* && -r $_tc_entry && -f $_tc_entry ]]; then
      _tc_resolved[$_tc_entry]=$_tc_entry
      _tc_sources[$_tc_entry]=${mapfile[$_tc_entry]}
    fi
  done
  # Release all stale IDs together. Two updated providers can exchange names
  # or move between local and remote entries in one configuration update.
  for _tc_entry in "${(@k)_tc_file_ids}"; do
    if [[ ${_tc_file_paths[$_tc_entry]} != "${_tc_resolved[$_tc_entry]}" ||
          ${_tc_file_sources[$_tc_entry]} != "${_tc_sources[$_tc_entry]}" ]]; then
      _tc_forget_file "$_tc_entry"
    fi
  done
  _tc_file_errors=()
  for _tc_entry in "${_tc_entries[@]}"; do
    [[ $_tc_entry == (/*|github:*) ]] || continue
    (( ${+_tc_file_ids[$_tc_entry]} )) && continue
    _tc_path=${_tc_resolved[$_tc_entry]}
    [[ -n $_tc_path || $_tc_entry == /* ]] || continue
    if [[ ! -f $_tc_path ]] || ! _tc_id=$(_tc_inspect_file "$_tc_path" 2>/dev/null); then
      _tc_file_errors[$_tc_entry]="unavailable or incompatible provider"
      continue
    fi
    if _tc_valid_provider "$_tc_id" || (( ${_tc_entries[(Ie)$_tc_id]} )); then
      _tc_file_errors[$_tc_entry]="duplicate provider ID: $_tc_id"
      continue
    fi
    TOKEN_PROVIDER_ID=''
    if source "$_tc_path" >/dev/null && [[ $TOKEN_PROVIDER_ID == "$_tc_id" ]] && _tc_valid_provider "$_tc_id"; then
      _tc_file_ids[$_tc_entry]=$_tc_id
      _tc_file_paths[$_tc_entry]=$_tc_path
      _tc_file_sources[$_tc_entry]=${_tc_sources[$_tc_entry]}
    else
      _tc_file_errors[$_tc_entry]="provider failed to load"
    fi
  done
}

_tc_active_providers() {
  emulate -L zsh
  local -a _tc_entries
  local _tc_entry _tc_id _tc_prepare
  _tc_entries_for_shell
  for _tc_entry in "${_tc_entries[@]}"; do
    _tc_id=${_tc_file_ids[$_tc_entry]:-$_tc_entry}
    _tc_valid_provider "$_tc_id" || continue
    (( ${_tc_ids[(Ie)$_tc_id]} )) && continue
    _tc_ids+=("$_tc_id")
    _tc_prepare=token_provider_${_tc_id}_prepare
    if (( $+functions[$_tc_prepare] && ! ${+_tc_prepared[$_tc_id]} )); then
      _tc_prepared[$_tc_id]=1
      "$_tc_prepare" >/dev/null
    fi
  done
  return 0
}

_tc_help() {
  emulate -L zsh
  local _tc_heading='token-completion'
  [[ -t 1 && -z ${NO_COLOR:-} && ${TERM:-dumb} != dumb ]] && _tc_heading=$'\e[1;36mtoken-completion\e[0m'
  print -r -- "$_tc_heading   Complete a token at the cursor."
  print -r -- '  providers list       Show configured sources and their pins'
  print -r -- '  providers install    Materialize missing providers at locked revisions'
  print -r -- '  providers update     Resolve refs and publish their new pins'
  print -r -- '  dotenv KEY           Read a literal from .env in this directory'
}

token-completion() {
  emulate -L zsh
  case "$1:$2:$#" in
    ::0|help::1|--help::1|-h::1) _tc_help ;;
    dotenv:*:2) command "$_tc_python" "$_tc_root/dotenv.py" get "$2" ;;
    providers:(list|install|update):2)
      local -a _tc_entries _tc_ids
      local _tc_entry
      _tc_entries_for_shell
      _tc_manager "$2" || return $?
      if [[ $2 != list ]]; then
        _tc_load_files
        _tc_active_providers
      fi
      ;;
    *) _tc_help >&2; return 2 ;;
  esac
}

# Extract a plain token only when it occupies an argument in a simple command.
# The lexer retains quote/escape spelling; skip full shell grammars we can't
# classify rather than claiming an ordinary completion position.
_tc_fragment() {
  emulate -L zsh
  setopt extendedglob
  local -a _tc_words
  if [[ $1 == on ]]; then
    _tc_words=(${(Z:c:)LBUFFER})
  else
    _tc_words=(${(z)LBUFFER})
  fi
  local _tc_word=${_tc_words[-1]} _tc_previous
  [[ $#_tc_words -ge 2 && $LBUFFER == *"$_tc_word" && $RBUFFER != [^[:space:]]* ]] || return 1
  [[ $_tc_word == '$'[a-zA-Z_][a-zA-Z_0-9]# ||
     $_tc_word == --[a-zA-Z0-9_-]##='$'[a-zA-Z_][a-zA-Z_0-9]# ]] || return 1
  local -i _tc_expect_command=1 _tc_redirect=0
  for _tc_previous in "${(@)_tc_words[1,-2]}"; do
    if (( _tc_redirect )); then
      _tc_redirect=0
    else
      case $_tc_previous in
        (';'|'&'|'|'|'||'|'&&'|'|&'|$'\n'|'('|')'|'{'|'}') _tc_expect_command=1 ;;
        ([0-9]#('<'|'>'|'>>'|'<<<'|'<>'|'<&'|'>&'|'>>!'|'>!'|'>&!'|'&>'|'&>>')) _tc_redirect=1 ;;
        ('<<'|'<<-'|'for'|'case'|'[['|'((') return 1 ;;
        (*) if (( _tc_expect_command )); then
              case $_tc_previous in
                ([a-zA-Z_][a-zA-Z_0-9]#=*|command|exec|builtin|noglob|nocorrect|time|env|if|then|elif|else|while|until|do|'!') ;;
                (-*) return 1 ;;
                (*) _tc_expect_command=0 ;;
              esac
            fi ;;
      esac
    fi
  done
  (( _tc_expect_command || _tc_redirect )) && return 1
  _tc_query=${_tc_word##*\$}
}

_tc_pick() {
  emulate -L zsh
  local _tc_default_prompt='› '
  local -a _tc_style=(--ansi --color=pointer:cyan,prompt:cyan,hl:yellow,hl+:yellow)
  local _tc_dim=$'\e[2m' _tc_reset=$'\e[0m'
  if [[ -n ${NO_COLOR:-} || ${TERM:-dumb} == dumb ]]; then
    _tc_style=(--color=bw)
    _tc_dim='' _tc_reset=''
  fi
  local _tc_row _tc_key
  local -a _tc_fields _tc_lines
  local -i _tc_width=0
  for _tc_row in "$@"; do
    _tc_fields=("${(@ps:\t:)_tc_row}")
    _tc_key=$_tc_fields[2]
    (( ${#_tc_key} > _tc_width )) && _tc_width=${#_tc_key}
  done
  for _tc_row in "$@"; do
    _tc_fields=("${(@ps:\t:)_tc_row}")
    _tc_key=$_tc_fields[2]
    _tc_lines+=("$_tc_fields[1]"$'\t'"${(r:_tc_width:)_tc_key}   $_tc_dim$_tc_fields[3]$_tc_reset")
  done
  # Do not inherit user accept/execute/preview bindings in an insertion widget.
  printf '%s\0' "${_tc_lines[@]}" | FZF_DEFAULT_OPTS='' FZF_DEFAULT_OPTS_FILE='' command "$_tc_fzf" \
    --read0 --print0 --delimiter=$'\t' --with-nth=2.. --no-multi --layout=reverse --no-border \
    --prompt="${TOKEN_COMPLETION_PROMPT-$_tc_default_prompt}" --pointer='▸' \
    --header='Enter  insert     Esc  cancel' "${_tc_style[@]}"
}

_tc_complete() {
  emulate -L zsh
  local -a _tc_parameter_names=("${(@k)parameters}")
  local _tc_query _tc_id _tc_row _tc_filter_result
  local -a _tc_ids _tc_rows _tc_matches _tc_insertions
  if _tc_fragment "$2"; then
    _tc_active_providers
    for _tc_id in "${_tc_ids[@]}"; do
      "token_provider_${_tc_id}_query" "$_tc_query" >/dev/null
    done
    if (( $#_tc_rows )); then
      while IFS= read -r -d '' _tc_row; do
        _tc_matches+=("$_tc_row")
      done < <(printf '%s\0' "${_tc_rows[@]}" | token_completion_filter "$_tc_query" --delimiter=$'\t' --nth=2)
    fi
  fi
  case $#_tc_matches in
    0) zle "${_tc_fallbacks[$1]}"; return ;;
    1) _tc_row=$_tc_matches[1] ;;
    *) _tc_filter_result=$(_tc_pick "${_tc_matches[@]}") || { zle -R; return; }
       _tc_row=${_tc_filter_result%$'\0'} ;;
  esac
  _tc_id=${_tc_row%%$'\t'*}
  [[ $_tc_id == <1-> && $_tc_id -le $#_tc_insertions ]] || return 1
  LBUFFER="${LBUFFER%\$"$_tc_query"}${_tc_insertions[_tc_id]}"
  zle -R
}

# Calling a widget through another ZLE widget preserves the outer WIDGET name.
# Keep the saved binding explicit so fallback works through either path.
_tc_emacs() { _tc_complete token-completion-emacs "$options[interactivecomments]"; }
_tc_viins() { _tc_complete token-completion-viins "$options[interactivecomments]"; }
_tc_main() { _tc_complete token-completion-main "$options[interactivecomments]"; }

_tc_init() {
  emulate -L zsh
  local -a _tc_ids _tc_binding
  local _tc_map _tc_widget
  _tc_load_files
  _tc_active_providers
  [[ -o interactive ]] || return 0
  (( ${+_tc_keys_installed} )) && return 0
  typeset -g _tc_keys_installed=1
  for _tc_map in emacs viins main; do
    _tc_widget=token-completion-${_tc_map}
    (( ${+_tc_fallbacks[$_tc_widget]} )) && continue
    _tc_binding=(${(z)$(bindkey -M "$_tc_map" '^I')})
    [[ ${_tc_binding[-1]} == token-completion-* ]] && continue
    (( ${+widgets[${_tc_binding[-1]}]} )) || continue
    _tc_fallbacks[$_tc_widget]=${_tc_binding[-1]}
    zle -N "$_tc_widget" "_tc_$_tc_map"
    bindkey -M "$_tc_map" '^I' "$_tc_widget"
  done
}

_tc_init
