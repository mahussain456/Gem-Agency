#!/usr/bin/env bash
# The Agency — install agent profiles into Claude Code or Cursor.
#
#   install.sh claude                          all agents -> ~/.claude/agents (every project)
#   install.sh claude --project                all agents -> ./.claude/agents (this repo only)
#   install.sh cursor                          all agents -> ./.cursor/rules
#   install.sh claude --division design        one division
#   install.sh cursor --agent deal-closer      one agent (repeatable)
#   install.sh claude --dry-run                show what would change, change nothing
#   install.sh claude --uninstall              remove only files this script installed
#   install.sh --list                          show the roster
#
# Options
#   --project | --global   where to install for claude (default: global). Cursor is always project.
#   --dir PATH             install somewhere else entirely
#   --force                overwrite files that were NOT installed by this script
set -euo pipefail
here="$(dirname "${BASH_SOURCE[0]}")"
. "$here/lib.sh"

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

target=""; scope=""; dir=""; dry=0; uninstall=0; force=0; list=0
divisions=(); agents=()
while [ $# -gt 0 ]; do
  case "$1" in
    claude|cursor) target="$1"; shift ;;
    --project) scope=project; shift ;;
    --global) scope=global; shift ;;
    --dir) [ $# -ge 2 ] || die "--dir needs a path"; dir="$2"; shift 2 ;;
    --division) [ $# -ge 2 ] || die "--division needs a name"; divisions+=("$2"); shift 2 ;;
    --agent) [ $# -ge 2 ] || die "--agent needs a name"; agents+=("$2"); shift 2 ;;
    --dry-run) dry=1; shift ;;
    --uninstall) uninstall=1; shift ;;
    --force) force=1; shift ;;
    --list) list=1; shift ;;
    -h|--help) usage 0 ;;
    *) die "unknown argument '$1' (try --help)" ;;
  esac
done

# ---- select profiles ----
selected=()
while IFS= read -r f; do
  division="$(fm_get "$f" division)"; name="$(fm_get "$f" name)"
  if [ ${#divisions[@]} -gt 0 ]; then
    keep=0; for d in "${divisions[@]}"; do [ "$d" = "$division" ] && keep=1; done
    [ $keep -eq 1 ] || continue
  fi
  if [ ${#agents[@]} -gt 0 ]; then
    keep=0; for a in "${agents[@]}"; do [ "$a" = "$name" ] && keep=1; done
    [ $keep -eq 1 ] || continue
  fi
  selected+=("$f")
done < <(agency_profiles)

for d in ${divisions[@]+"${divisions[@]}"}; do
  case " $AGENCY_DIVISIONS " in *" $d "*) ;; *) die "unknown division '$d' ($AGENCY_DIVISIONS)";; esac
done
for a in ${agents[@]+"${agents[@]}"}; do
  found=0; for f in ${selected[@]+"${selected[@]}"}; do [ "$(fm_get "$f" name)" = "$a" ] && found=1; done
  [ $found -eq 1 ] || die "no agent named '$a' (see --list)"
done

if [ $list -eq 1 ]; then
  printf '%-12s %-24s %s\n' DIVISION AGENT TAGLINE
  for f in ${selected[@]+"${selected[@]}"}; do
    printf '%-12s %-24s %s\n' "$(fm_get "$f" division)" "$(fm_get "$f" name)" "$(fm_get "$f" tagline)"
  done
  exit 0
fi

[ -n "$target" ] || die "choose a target: claude or cursor (try --help)"
[ ${#selected[@]} -gt 0 ] || die "nothing selected"

# ---- resolve destination ----
if [ -z "$dir" ]; then
  case "$target" in
    claude) if [ "$scope" = project ]; then dir="$PWD/.claude/agents"; else dir="$HOME/.claude/agents"; fi ;;
    cursor) [ "$scope" != global ] || die "Cursor has no global rules folder; use --project or --dir"
            dir="$PWD/.cursor/rules" ;;
  esac
fi
ext=md; [ "$target" = cursor ] && ext=mdc

ours() { [ -f "$1" ] && grep -q "$AGENCY_MARKER" "$1"; }

installed=0; skipped=0; removed=0; unchanged=0
[ $dry -eq 1 ] && printf '(dry run: nothing will be written)\n'
printf '%s %s agent(s) %s %s\n' "$([ $uninstall -eq 1 ] && echo Removing || echo Installing)" \
  "${#selected[@]}" "$([ $uninstall -eq 1 ] && echo from || echo into)" "$dir"

[ $dry -eq 1 ] || [ $uninstall -eq 1 ] || mkdir -p "$dir"
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT

for f in "${selected[@]}"; do
  name="$(fm_get "$f" name)"; dest="$dir/$name.$ext"
  if [ $uninstall -eq 1 ]; then
    if ours "$dest"; then
      [ $dry -eq 1 ] || rm -f "$dest"
      printf '  - %s\n' "$name"; removed=$((removed + 1))
    elif [ -f "$dest" ]; then
      printf '  ! %s kept (not installed by this script)\n' "$name"; skipped=$((skipped + 1))
    fi
    continue
  fi
  if [ -f "$dest" ] && ! ours "$dest" && [ $force -eq 0 ]; then
    printf '  ! %s skipped: %s exists and was not installed by this script (use --force)\n' "$name" "$dest"
    skipped=$((skipped + 1)); continue
  fi
  bash "$here/convert.sh" "$target" "$f" > "$tmp"
  if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then
    unchanged=$((unchanged + 1)); continue
  fi
  [ $dry -eq 1 ] || cp "$tmp" "$dest"
  printf '  + %s\n' "$name"; installed=$((installed + 1))
done

if [ $uninstall -eq 1 ]; then
  printf 'Done: %d removed, %d kept.\n' "$removed" "$skipped"
else
  printf 'Done: %d installed or updated, %d unchanged, %d skipped.\n' "$installed" "$unchanged" "$skipped"
  if [ $dry -eq 0 ] && [ $installed -gt 0 ]; then
    case "$target" in
      claude) printf 'Restart Claude Code, then run /agents to see them, or ask: "use the frontend-developer agent to ..."\n' ;;
      cursor) printf 'In Cursor chat, mention a rule with @%s or let the agent pick it from its description.\n' "$(fm_get "${selected[0]}" name)" ;;
    esac
  fi
fi
[ $skipped -eq 0 ] || [ $uninstall -eq 1 ] || exit 2
