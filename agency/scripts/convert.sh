#!/usr/bin/env bash
# The Agency — convert agent profiles into the format a coding assistant reads.
#
#   convert.sh claude  engineering/frontend-developer.md           # to stdout
#   convert.sh cursor  design/*.md -o ./.cursor/rules              # to files
#   convert.sh prompt  sales/deal-closer.md | pbcopy               # paste anywhere
#
# Targets
#   claude  Claude Code subagent   -> <name>.md   (name, description, tools)
#   cursor  Cursor project rule    -> <name>.mdc  (agent-requested: description, no globs)
#   prompt  Plain system prompt    -> <name>.md   (no frontmatter; ChatGPT, Gemini, API calls)
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

[ $# -ge 1 ] || usage 1
case "$1" in -h|--help) usage 0 ;; esac
target="$1"; shift
case "$target" in claude|cursor|prompt) ;; *) die "unknown target '$target' (claude | cursor | prompt)";; esac

out=""; files=()
while [ $# -gt 0 ]; do
  case "$1" in
    -o|--out) [ $# -ge 2 ] || die "$1 needs a directory"; out="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    -*) die "unknown option '$1'" ;;
    *) files+=("$1"); shift ;;
  esac
done
[ ${#files[@]} -gt 0 ] || die "no profile files given"
[ -n "$out" ] || [ ${#files[@]} -eq 1 ] || die "converting several files needs -o DIR"

render() {
  local file="$1" name title division description tools rel
  fm_has_block "$file" || die "$file has no frontmatter block"
  name="$(fm_get "$file" name)"; title="$(fm_get "$file" title)"
  division="$(fm_get "$file" division)"; description="$(fm_get "$file" description)"
  tools="$(fm_get "$file" tools)"
  [ -n "$name" ] && [ -n "$description" ] || die "$file is missing name or description (run lint.sh)"
  rel="$division/$name.md"

  case "$target" in
    claude)
      printf -- '---\nname: %s\ndescription: %s\n' "$name" "$(yaml_quote "$description")"
      [ -z "$tools" ] || printf 'tools: %s\n' "$tools"
      printf -- '---\n' ;;
    cursor)
      printf -- '---\ndescription: %s\nglobs:\nalwaysApply: false\n---\n' "$(yaml_quote "$description")" ;;
  esac
  printf '<!-- %s from %s. Edit the source profile, then re-run install. -->\n\n' "$AGENCY_MARKER" "$rel"
  printf 'You are **%s**, a specialist in the %s division of The Agency. ' "$title" "$(division_label "$division")"
  printf 'The profile below defines who you are, how you work and what "done" means. Stay in this role; '
  printf 'when a request belongs to another specialist, say which one and why.\n\n'
  fm_body "$file"
}

ext() { [ "$target" = cursor ] && echo mdc || echo md; }

if [ -z "$out" ]; then
  render "${files[0]}"
  exit 0
fi

mkdir -p "$out"
for f in "${files[@]}"; do
  [ -f "$f" ] || die "no such file: $f"
  name="$(fm_get "$f" name)"
  [ -n "$name" ] || die "$f has no name"
  render "$f" > "$out/$name.$(ext)"
  printf 'wrote %s\n' "$out/$name.$(ext)"
done
