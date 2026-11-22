# Source after your usual completion initialization.
typeset -g _tc_root=${${(%):-%x}:A:h}
typeset -g _tc_python=python3 _tc_fzf=fzf
typeset -gA _tc_detected_formats _tc_detection_errors
typeset -gA _tc_file_ids _tc_file_paths _tc_file_sources _tc_file_errors _tc_prepared _tc_fallbacks

# Reusable filter: one unmodified NUL-terminated result per matching input.
token_completion_filter() {
  FZF_DEFAULT_OPTS='' FZF_DEFAULT_OPTS_FILE='' command "$_tc_fzf" --read0 --print0 --ignore-case --filter "$@"
}

# Emit ranked immediate child directory names as NUL-delimited records.
token_completion_directory_matches() {
  emulate -L zsh
  (( $# == 2 )) || return 2
  local fragment=$1 root=$2
  [[ -n $root && -d $root ]] || return 1
  local -a directories=("$root"/*(N-/:t))
  if (( ${directories[(Ie)$fragment]} )); then
    printf '%s\0' "$fragment"
    return
  fi
  (( $#directories )) || return 1
  printf '%s\0' "${directories[@]}" | token_completion_filter "$fragment"
}

# The provider owns the command/argument scope and supplies the resolved root.
token_completion_directory() {
  emulate -L zsh
  (( $# == 2 && ${+_tc_context_rows} )) || return 1
  local basename
  while IFS= read -r -d '' basename; do
    token_completion_offer "${(V)basename}" "$2" "${(q)basename}"
  done < <(token_completion_directory_matches "$1" "$2")
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

# This registry connects the built-in document readers to their source arrays.
# A third-party token query cannot advertise a reader the document engine lacks.
typeset -gA _tc_file_readers=(
  [ini]=TOKEN_COMPLETION_INI_FILES   [toml]=TOKEN_COMPLETION_TOML_FILES
  [json]=TOKEN_COMPLETION_JSON_FILES [yaml]=TOKEN_COMPLETION_YAML_FILES
)

_tc_file_formats() {
  emulate -L zsh
  local -a _tc_entries
  local _tc_entry _tc_id
  _tc_entries_for_shell
  _tc_formats=()
  for _tc_entry in "${_tc_entries[@]}"; do
    _tc_id=${_tc_file_ids[$_tc_entry]:-$_tc_entry}
    _tc_valid_provider "$_tc_id" || continue
    (( ${+_tc_file_readers[$_tc_id]} )) || continue
    (( ${_tc_formats[(Ie)$_tc_id]} )) || _tc_formats+=("$_tc_id")
  done
}

_tc_file_diagnostic() {
  emulate -L zsh
  [[ $2 == "${_tc_detection_errors[$1]}" ]] && return 0
  _tc_detection_errors[$1]=$2
  if zle >/dev/null 2>&1; then zle -M "$2"; else print -ru2 -- "$2"; fi
}

# A manifest can refer to the same relative filename in different projects.
# Keep only successful parser selections, scoped to physical path and parsers.
_tc_manifest_format() {
  emulate -L zsh
  setopt extendedglob
  local _tc_policy=$1 _tc_doc=$2 _tc_path=$2 _tc_result
  case $_tc_path in
    ('~/'*) _tc_path=$HOME/${_tc_path#\~/} ;;
    ('~'[a-zA-Z0-9_-]##'/'*)
      local _tc_tilde=${_tc_path%%/*} _tc_home
      # Only expand the validated username, never the file's glob characters.
      _tc_home=$(print -r -- ${~_tc_tilde} 2>/dev/null) && _tc_path=$_tc_home/${_tc_path#*/} ;;
  esac
  local -a _tc_identity=("${_tc_path:a}" "${_tc_path:A}" "$_tc_policy" "${(@o)_tc_formats}")
  local _tc_key=${(j: :)${(@q)_tc_identity}}
  if (( ${+_tc_detected_formats[$_tc_key]} )); then
    _tc_format=${_tc_detected_formats[$_tc_key]}
    return 0
  fi
  if ! _tc_result=$(command "$_tc_python" "$_tc_root/file_source.py" detect "$_tc_policy" "$_tc_doc" "${_tc_formats[@]}" 2>&1); then
    _tc_file_diagnostic "$_tc_key" "$_tc_result"
    return 1
  fi
  (( ${_tc_formats[(Ie)$_tc_result]} )) || return 1
  unset "_tc_detection_errors[$_tc_key]"
  _tc_detected_formats[$_tc_key]=$_tc_result
  _tc_format=$_tc_result
}

# Deferred rows carry an expression; literal rows resolve only after selection.
token_completion_file() { _tc_file_candidates deferred "$@"; }
token_completion_file_literal() { _tc_file_candidates literal "$@"; }

_tc_file_candidates() {
  emulate -L zsh
  local _tc_mode=$1
  shift
  local -i _tc_exact=0
  # A document fragment can itself be "--exact". The third argument in
  # the original call is a selection policy; with the flag it is a format.
  if [[ $_tc_mode == deferred && $1 == --exact && $3 != (keys|leaf-values|section-names) ]]; then
    _tc_exact=1
    shift
  fi
  (( $# == 4 || $# == 7 || $# == 8 )) || return 2
  [[ $_tc_mode != literal ]] || (( ${+_tc_literal_fields} )) || return 1
  local _tc_fragment_text=$1 _tc_format=$2 _tc_selection=$3 _tc_file=$4
  shift 4
  # A leading dash in a bare path would be parsed as an option on the next
  # Tab. Keep its editable spelling distinct from the canonical document path.
  if [[ $_tc_mode == literal && $_tc_fragment_text == %2[Dd]* ]]; then
    _tc_fragment_text=-${_tc_fragment_text[4,-1]}
  fi
  local _tc_kind _tc_display _tc_node _tc_insertion
  local -a _tc_lookup _tc_formats
  _tc_file_formats
  if [[ $_tc_format == auto ]]; then
    _tc_manifest_format "$_tc_selection" "$_tc_file" || return 1
  else
    local _tc_key="explicit ${(q)_tc_selection} ${(q)_tc_file} ${(q)_tc_format}"
    if (( ! ${_tc_formats[(Ie)$_tc_format]} )); then
      _tc_file_diagnostic "$_tc_key" "token-completion: ${(q)_tc_file}: parser ${(q)_tc_format} is unavailable or disabled"
      return 1
    fi
    unset "_tc_detection_errors[$_tc_key]"
  fi
  while IFS= read -r -d '' _tc_kind && IFS= read -r -d '' _tc_display && IFS= read -r -d '' _tc_node; do
    if [[ $_tc_kind == branch ]]; then
      _tc_insertion=$_tc_node
      if [[ $_tc_mode == deferred ]]; then
        _tc_insertion='$'${_tc_node}
      elif [[ $_tc_node == -* ]]; then
        _tc_insertion=%2D${_tc_node#-}
      fi
    else
      _tc_lookup=("$_tc_format" "$_tc_selection" "$_tc_file" "$_tc_node" "$@")
      if [[ $_tc_mode == literal ]]; then
        token_completion_offer "$_tc_display" "$_tc_file" '' || continue
        # Executable arguments are NUL-free. Keep exact argv out of fzf and the
        # editable line, keyed by the opaque candidate ID until acceptance.
        _tc_literal_fields[${#_tc_insertions}]=${(pj:\0:)_tc_lookup}
        continue
      fi
      if (( _tc_exact )); then
        _tc_insertion='"${$(token-completion lookup-exact '"${(j: :)${(@q)_tc_lookup}}"')%.}"'
      else
        _tc_insertion='"$(token-completion lookup '"${(j: :)${(@q)_tc_lookup}}"')"'
      fi
    fi
    token_completion_offer "$_tc_display" "$_tc_file" "$_tc_insertion"
  done < <(command "$_tc_python" "$_tc_root/file_source.py" list "$_tc_format" "$_tc_selection" "$_tc_file" "$_tc_fragment_text")
}

_tc_accept_insertion() {
  emulate -L zsh
  if (( ! ${+_tc_literal_fields[$1]} )); then
    _tc_insertion=${_tc_insertions[$1]}
    return 0
  fi
  local -a _tc_lookup=("${(@0)_tc_literal_fields[$1]}")
  local _tc_value
  _tc_value=$(token-completion lookup-exact "${_tc_lookup[@]}") || return $?
  _tc_value=${_tc_value%.}
  _tc_insertion=${(q)_tc_value}
}

_tc_document_query() {
  emulate -L zsh
  local _tc_format=$1 _tc_fragment_text=$2 _tc_file
  local _tc_setting=${_tc_file_readers[$_tc_format]}
  (( ${+parameters[$_tc_setting]} )) || return 0
  local -aU _tc_documents=("${(@P)_tc_setting}")
  for _tc_file in "${_tc_documents[@]}"; do
    token_completion_file "$_tc_fragment_text" "$_tc_format" leaf-values "$_tc_file"
  done
}

typeset -g token_provider_ini_version=1 token_provider_toml_version=1
typeset -g token_provider_json_version=1 token_provider_yaml_version=1
token_provider_ini_query() { _tc_document_query ini "$1"; }
token_provider_toml_query() { _tc_document_query toml "$1"; }
token_provider_json_query() { _tc_document_query json "$1"; }
token_provider_yaml_query() { _tc_document_query yaml "$1"; }

_tc_document_lookup() {
  emulate -L zsh
  setopt extendedglob
  local -a _tc_formats _tc_guard_args
  _tc_file_formats
  if (( ! ${_tc_formats[(Ie)$1]} )); then
    print -ru2 -- "token-completion: document parser ${(q)1} is unavailable or disabled"
    return 1
  fi
  if (( $# == 7 || $# == 8 )); then
    _tc_guard_args=("$6" "$7")
    (( $# == 8 )) && _tc_guard_args+=("$8")
    _tc_guard_args+=("$1" "$2" "$3")
    if [[ $5 != [a-zA-Z_][a-zA-Z0-9_-]# ]] || (( ! ${+functions[$5]} )) ||
        ! "$5" "${_tc_guard_args[@]}" > /dev/null; then
      print -ru2 -- 'token-completion: lookup is no longer authorized by its provider'
      return 1
    fi
  elif (( $# != 4 )); then
    print -ru2 -- 'usage: token-completion lookup FORMAT SELECT FILE PATH [GUARD COMMAND OPTION [DECLARED_FORMAT]]'
    return 2
  fi
  command "$_tc_python" "$_tc_root/file_source.py" get "$1" "$2" "$3" "$4"
}

_tc_add_files() {
  emulate -L zsh
  local -a _tc_formats _tc_paths _tc_destinations
  local _tc_pending _tc_format _tc_file _tc_dest _tc_attr _tc_flag
  local -i _tc_i
  _tc_file_formats
  while (( $# )); do
    _tc_flag=$1
    shift
    case $_tc_flag in
      --format)
        if [[ -n $_tc_pending || -z $1 || $1 == -* ]]; then
          print -ru2 -- 'token-completion: --format takes one parser for the next -f/--file'
          return 2
        fi
        _tc_pending=$1; shift
        if (( ! ${_tc_formats[(Ie)$_tc_pending]} )); then
          print -ru2 -- "token-completion: document parser ${(q)_tc_pending} is unavailable or disabled"
          return 2
        fi
        continue ;;
      -f|--file)
        _tc_format=$_tc_pending
        _tc_pending='' ;;
      --*)
        # Legacy --json FILE, --ini FILE, etc., use the same registry.
        _tc_format=${_tc_flag#--}
        if [[ -n $_tc_pending ]]; then
          print -ru2 -- 'token-completion: follow --format FORMAT with -f/--file FILE'
          return 2
        fi
        if (( ! ${_tc_formats[(Ie)$_tc_format]} )); then
          print -ru2 -- "token-completion: document parser ${(q)_tc_format} is unavailable or disabled"
          return 2
        fi ;;
      *) print -ru2 -- "token-completion: unknown file option ${(q)_tc_flag}"; return 2 ;;
    esac
    if [[ -z $1 ]]; then
      print -ru2 -- "token-completion: ${(q)_tc_flag} requires a file path"
      return 2
    fi
    _tc_file=$1; shift
    if [[ -z $_tc_format ]]; then
      _tc_format=$(command "$_tc_python" "$_tc_root/file_source.py" detect leaf-values "$_tc_file" "${_tc_formats[@]}") || return 1
      (( ${_tc_formats[(Ie)$_tc_format]} )) || return 1
    fi
    _tc_dest=${_tc_file_readers[$_tc_format]}
    _tc_attr=${parameters[$_tc_dest]}
    if [[ -n $_tc_attr && ( $_tc_attr != array* || $_tc_attr == *readonly* ) ]]; then
      print -ru2 -- "token-completion: $_tc_dest must be a writable array"
      return 2
    fi
    _tc_destinations+=("$_tc_dest")
    _tc_paths+=("$_tc_file")
  done
  if [[ -n $_tc_pending ]] || (( ! $#_tc_paths )); then
    print -ru2 -- 'usage: token-completion files [--format FORMAT] -f FILE [-f FILE ...]'
    return 2
  fi
  # All options, detections and destination types have been checked.
  for (( _tc_i=1; _tc_i <= $#_tc_paths; _tc_i++ )); do
    _tc_dest=${_tc_destinations[_tc_i]}
    if (( ! ${+parameters[$_tc_dest]} )); then typeset -ga "$_tc_dest"; fi
    set -A "$_tc_dest" "${(@P)_tc_dest}" "${_tc_paths[_tc_i]}"
  done
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
  unfunction "token_provider_${_tc_id}_context" 2>/dev/null
  unfunction "token_provider_${_tc_id}_argument_context" 2>/dev/null
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
  print -r -- '  files -f FILE        Register a document and detect its parser (repeatable)'
  print -r -- '    --file FILE        Long form of -f'
  print -r -- '    --format FORMAT    Use this enabled parser for the next file'
  print -r -- '    --json FILE        Shorthand for --format json -f FILE (also ini, toml, yaml)'
  print -r -- '  lookup FORMAT SELECT FILE PATH [GUARD COMMAND OPTION [DECLARED_FORMAT]]'
  print -r -- '                       Read one document key or leaf at execution time'
  print -r -- '  lookup-exact …       Append a dot to successful output to preserve final newlines in zsh'
  print -r -- '  dotenv KEY           Read a literal from .env in this directory'
}

token-completion() {
  emulate -L zsh
  case "$1:$2:$#" in
    ::0|help::1|--help::1|-h::1) _tc_help ;;
    files:*) shift; _tc_add_files "$@" ;;
    lookup:*) shift; _tc_document_lookup "$@" ;;
    lookup-exact:*) shift; _tc_document_lookup "$@" && builtin print -rn -- . ;;
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
  local _tc_input=$LBUFFER
  local -i _tc_empty=0
  if [[ $_tc_input == *[[:space:]] ]]; then
    # A sentinel lets the lexer distinguish a new argument from escaped
    # whitespace, an open quote, or the rest of an interactive comment.
    _tc_input+=TC_EMPTY
    _tc_empty=1
  fi
  if [[ $1 == on ]]; then
    _tc_words=(${(Z:c:)_tc_input})
  else
    _tc_words=(${(z)_tc_input})
  fi
  # The lexer loses adjacency around a named descriptor ({fd}>file). Delegate
  # that grammar instead of treating the descriptor name as an argument.
  [[ $LBUFFER == *'{'[a-zA-Z_][a-zA-Z0-9_]#'}'[\<\>]* ]] && return 1
  local _tc_word=${_tc_words[-1]} _tc_previous
  [[ $#_tc_words -ge 2 && $_tc_input == *"$_tc_word" && $RBUFFER != [^[:space:]]* ]] || return 1
  if (( _tc_empty )); then
    [[ $_tc_word == TC_EMPTY ]] || return 1
    _tc_word=''
  fi
  local -i _tc_expect_command=1 _tc_redirect=0
  for _tc_previous in "${(@)_tc_words[1,-2]}"; do
    if (( _tc_redirect )); then
      _tc_redirect=0
    else
      case $_tc_previous in
        (';'|'&'|'&|'|'|'|'||'|'&&'|'|&'|$'\n'|'('|')'|'{'|'}') _tc_expect_command=1; _tc_command=''; _tc_args=() ;;
        # zsh tokenizes >! as >|, and &>> as >>&. A numeric descriptor
        # stays attached to the operator. Its following word is never argv.
        ([0-9]#('<'|'<<<'|'<>'|'<&'|'>'|'>|'|'>>'|'>>|'|'>&'|'&>'|'&>|'|'>>&'|'>>&|')) _tc_redirect=1 ;;
        ([0-9]#('<<'|'<<-')|'for'|'case'|'[['|'((') return 1 ;;
        (*) if (( _tc_expect_command )); then
              case $_tc_previous in
                ([a-zA-Z_][a-zA-Z_0-9]#=*|command|exec|builtin|noglob|nocorrect|time|env|if|then|elif|else|while|until|do|'!') ;;
                (-*) return 1 ;;
                (*) _tc_expect_command=0; _tc_command=${(Q)_tc_previous} ;;
              esac
            else
              _tc_args+=("${(Q)_tc_previous}")
            fi ;;
      esac
    fi
  done
  (( _tc_expect_command || _tc_redirect )) && return 1
  local _tc_raw=$_tc_word
  _tc_form=positional
  if (( ! ${_tc_args[(Ie)--]} )); then
    if [[ $_tc_word == -*=* ]]; then
      _tc_option=${_tc_word%%=*}
      [[ $_tc_option == -[A-Za-z0-9-][A-Za-z0-9_-]# && $_tc_option != -- ]] || return 1
      _tc_raw=${_tc_word#*=}
      _tc_form=equals
    elif [[ ${_tc_args[-1]} == -[A-Za-z0-9-][A-Za-z0-9_-]# ]]; then
      _tc_option=${_tc_args[-1]}
      _tc_form=separate
      _tc_args[-1]=()
    fi
    # Option names belong to the shell's normal command completion.
    [[ $_tc_form != positional || $_tc_raw != -* ]] || return 1
  fi
  if [[ $_tc_raw == '$'* ]]; then
    _tc_mode=token
    _tc_query=${_tc_raw#\$}
    [[ $_tc_query == [A-Za-z0-9_.%-]# ]] || return 1
  else
    _tc_mode=literal
    _tc_query=$_tc_raw
    [[ $_tc_query == [[:alnum:]@_.,:/%+=-]# ]] || return 1
  fi
  _tc_suffix_length=${#_tc_raw}
}

_tc_pick() {
  emulate -L zsh
  # Reserve room for the command and recent output, even in a split pane.
  local -i _tc_lines_available=${LINES:-24} _tc_height=10
  (( _tc_lines_available > 0 && _tc_lines_available < 20 )) && _tc_height=$(( (_tc_lines_available + 1) / 2 ))
  local -a _tc_guide=(--header='Tab/Shift-Tab  move     Enter  insert     Esc  cancel')
  if (( _tc_height < 5 )); then
    _tc_guide=(--info=hidden --no-separator)
  fi
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
    --read0 --print0 --delimiter=$'\t' --with-nth=2.. --no-multi --height="~$_tc_height" --layout=reverse --no-border \
    --prompt="${TOKEN_COMPLETION_PROMPT-$_tc_default_prompt}" --pointer='▸' \
    --bind=tab:down,btab:up --cycle \
    "${_tc_guide[@]}" "${_tc_style[@]}"
}

# A contextual provider already matched each component. Keep its display row
# separate from generic fuzzy matches, while sharing their opaque insertion IDs.
token_completion_scope() {
  (( ${+_tc_scoped} )) || return 1
  _tc_scoped=1
}

token_completion_offer() {
  emulate -L zsh
  (( $# == 3 && ${+_tc_context_rows} )) || return 1
  [[ -n $1 && $1 != *[[:cntrl:]]* ]] || return 1
  _tc_insertions+=("$3")
  _tc_context_rows+=("${#_tc_insertions}"$'\t'"$1"$'\t'"[${2//[[:cntrl:]]/?}]")
}

_tc_complete() {
  emulate -L zsh
  setopt extendedglob
  local -a _tc_parameter_names=("${(@k)parameters}")
  local _tc_query _tc_id _tc_row _tc_filter_result
  local -i _tc_scoped=0 _tc_any_scoped=0 _tc_before _tc_suffix_length
  local -A _tc_literal_fields
  local _tc_mode _tc_insertion
  local _tc_command _tc_option _tc_form _tc_hook
  local -a _tc_ids _tc_rows _tc_matches _tc_insertions _tc_context_rows _tc_args
  if _tc_fragment "$2"; then
    _tc_active_providers
    for _tc_id in "${_tc_ids[@]}"; do
      _tc_scoped=0
      _tc_hook=token_provider_${_tc_id}_argument_context
      if (( ${+functions[$_tc_hook]} )); then
        _tc_before=$#_tc_context_rows
        "$_tc_hook" "$_tc_mode" "$_tc_query" "$_tc_command" "$_tc_option" "$_tc_form" "${_tc_args[@]}" >/dev/null
        # The word preceding this argument could be a standalone flag. Let
        # the provider interpret it as argv if it did not claim an option.
        if [[ $_tc_form == separate && ( $_tc_mode == token || $_tc_query != -* ) ]] && (( ! _tc_scoped && $#_tc_context_rows == _tc_before )); then
          "$_tc_hook" "$_tc_mode" "$_tc_query" "$_tc_command" '' positional "${_tc_args[@]}" "$_tc_option" >/dev/null
        fi
      elif [[ $_tc_mode == token && $_tc_form != positional ]]; then
        _tc_hook=token_provider_${_tc_id}_context
        if (( ${+functions[$_tc_hook]} )); then
          "$_tc_hook" "$_tc_query" "$_tc_command" "$_tc_option" "$_tc_form" "${_tc_args[@]}" >/dev/null
        fi
      fi
      (( _tc_scoped )) && _tc_any_scoped=1
    done
    if [[ $_tc_mode == token ]] && (( ! _tc_any_scoped )); then
      for _tc_id in "${_tc_ids[@]}"; do
        # File providers accept an empty root or a dotted document path.
        # Third-party v1 query functions keep their flat identifier contract.
        if (( ${+_tc_file_readers[$_tc_id]} )) || [[ $_tc_query == [A-Za-z_][A-Za-z_0-9]# ]]; then
          "token_provider_${_tc_id}_query" "$_tc_query" >/dev/null
        fi
      done
    fi
    if (( $#_tc_rows )); then
      while IFS= read -r -d '' _tc_row; do
        _tc_matches+=("$_tc_row")
      done < <(printf '%s\0' "${_tc_rows[@]}" | token_completion_filter "$_tc_query" --delimiter=$'\t' --nth=2)
    fi
  fi
  _tc_matches+=("${_tc_context_rows[@]}")
  case $#_tc_matches in
    0) zle "${_tc_fallbacks[$1]}"; return ;;
    1) _tc_row=$_tc_matches[1] ;;
    *) # Anchor the picker below the entire command when text beyond the
       # editing cursor wraps onto later rows.
       local -i _tc_saved_cursor=$CURSOR
       CURSOR=${#BUFFER}
       zle -R
       _tc_filter_result=$(_tc_pick "${_tc_matches[@]}")
       local -i _tc_pick_status=$?
       # fzf cleared its rows. Repaint from its anchor before moving the
       # editor back to the completion position.
       zle redisplay
       CURSOR=$_tc_saved_cursor
       zle -R
       (( _tc_pick_status == 0 )) || return 0
       _tc_row=${_tc_filter_result%$'\0'} ;;
  esac
  _tc_id=${_tc_row%%$'\t'*}
  [[ $_tc_id == <1-> && $_tc_id -le $#_tc_insertions ]] || return 1
  _tc_accept_insertion "$_tc_id" || { zle -R; return; }
  LBUFFER="${LBUFFER[1,${#LBUFFER}-_tc_suffix_length]}$_tc_insertion"
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
