#!/usr/bin/env bash
# The Agency — check every profile before it ships to an assistant or the app.
#
#   lint.sh            check all profiles
#   lint.sh FILE...    check specific profiles
#
# Exits 1 on any error, so it can gate CI or a pre-commit hook.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

REQUIRED_KEYS="name title division description tagline skills works_with tools"
REQUIRED_SECTIONS="Mission|Personality|Use me for|How I work|Deliverables|Definition of done|Never|Handoffs"

if [ $# -gt 0 ]; then files=("$@"); else mapfile -t files < <(agency_profiles); fi
[ ${#files[@]} -gt 0 ] || die "no profiles found under $AGENCY_ROOT"

# works_with may point at any profile in the agency, not just the files being linted
names=" "
while IFS= read -r f; do names="$names$(fm_get "$f" name) "; done < <(agency_profiles)

errors=0
err() { printf '  %s: %s\n' "$1" "$2"; errors=$((errors + 1)); }

for f in "${files[@]}"; do
  rel="${f#"$AGENCY_ROOT"/}"
  if ! fm_has_block "$f"; then err "$rel" "missing frontmatter block (--- ... ---)"; continue; fi

  for k in $REQUIRED_KEYS; do
    [ -n "$(fm_get "$f" "$k")" ] || err "$rel" "missing frontmatter key '$k'"
  done

  name="$(fm_get "$f" name)"; division="$(fm_get "$f" division)"
  base="$(basename "$f" .md)"; folder="$(basename "$(dirname "$f")")"
  [ "$name" = "$base" ] || err "$rel" "name '$name' must match the filename '$base'"
  printf '%s' "$name" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$' || err "$rel" "name must be kebab-case"
  case " $AGENCY_DIVISIONS " in
    *" $division "*) [ "$division" = "$folder" ] || err "$rel" "division '$division' must match folder '$folder'" ;;
    *) err "$rel" "division '$division' is not one of: $AGENCY_DIVISIONS" ;;
  esac

  desc="$(fm_get "$f" description)"
  [ ${#desc} -ge 60 ] && [ ${#desc} -le 320 ] || err "$rel" "description should be 60-320 characters (is ${#desc})"

  for w in $(fm_list "$(fm_get "$f" works_with)"); do
    case "$names" in *" $w "*) ;; *) err "$rel" "works_with names unknown agent '$w'" ;; esac
    [ "$w" != "$name" ] || err "$rel" "works_with lists itself"
  done

  for v in voice_directness voice_depth voice_risk; do
    val="$(fm_get "$f" "$v")"
    [ -z "$val" ] && continue
    printf '%s' "$val" | grep -Eq '^([1-9]|10)$' || err "$rel" "$v must be an integer 1-10"
  done

  body="$(fm_body "$f")"
  printf '%s\n' "$body" | grep -Eq "^# " || err "$rel" "body needs a '# Title' heading"
  IFS='|' read -r -a secs <<< "$REQUIRED_SECTIONS"
  for s in "${secs[@]}"; do
    printf '%s\n' "$body" | grep -Fxq "## $s" || err "$rel" "missing section '## $s'"
  done
done

if [ $errors -gt 0 ]; then
  printf '%d problem(s) in %d profile(s).\n' "$errors" "${#files[@]}" >&2
  exit 1
fi
printf 'All %d profiles pass.\n' "${#files[@]}"
