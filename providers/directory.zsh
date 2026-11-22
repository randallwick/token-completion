# A scoped source of live, immediate child directory names.
TOKEN_PROVIDER_ID=directory
typeset -g token_provider_directory_version=1

token_provider_directory_candidates() {
  emulate -L zsh
  (( $# == 2 )) || return 2
  local fragment=$1 root=$2 name
  [[ -d $root ]] || return 0
  local -a names=("$root"/*(N-/:t))
  (( $#names )) || return 0
  if (( ${names[(Ie)$fragment]} )); then
    token_completion_offer "${(V)fragment}" "$root" "${(q)fragment}"
    return
  fi
  while IFS= read -r -d '' name; do
    token_completion_offer "${(V)name}" "$root" "${(q)name}"
  done < <(printf '%s\0' "${names[@]}" | token_completion_filter "$fragment")
}
