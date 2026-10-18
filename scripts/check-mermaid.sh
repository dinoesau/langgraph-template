#!/usr/bin/env bash
# Compile mermaid diagrams to catch syntax errors locally.
# Usage: check-mermaid.sh [path ...]
# Each path is a markdown file, a raw .mmd file, or a directory scanned
# recursively for markdown files. With no paths, checks the repo docs
# (docs plus README plus AGENTS) and the wiki checkout when present.
# Fails on the first diagram that does not compile. Needs npx with network once.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

md_files=()
mmd_files=()

add_target() {
  if [ -f "$1" ]; then
    case "$1" in
      *.mmd) mmd_files+=("$1") ;;
      *) md_files+=("$1") ;;
    esac
  elif [ -d "$1" ]; then
    while IFS= read -r f; do
      md_files+=("$f")
    done < <(find "$1" -name '*.md' -not -path '*/.git/*' -not -path '*/node_modules/*' 2>/dev/null)
  else
    echo "check-mermaid: not found: $1" >&2
    exit 2
  fi
}

if [ "$#" -gt 0 ]; then
  for target in "$@"; do
    add_target "$target"
  done
else
  for target in "${ROOT}/docs" "${ROOT}/README.md" "${ROOT}/AGENTS.md"; do
    [ -e "$target" ] && add_target "$target"
  done
  if [ -d "${ROOT}/../langgraph-template.wiki" ]; then
    add_target "${ROOT}/../langgraph-template.wiki"
  fi
fi

if [ "${#md_files[@]}" -gt 0 ]; then
  awk -v tmpdir="${TMP}" '
    FNR==1 { in_block=0 }
    /^```mermaid[[:space:]]*$/ { in_block=1; n++; out=tmpdir "/block-" n ".mmd"; next }
    in_block && /^```[[:space:]]*$/ { in_block=0; close(out); next }
    in_block { print > out }
  ' "${md_files[@]}"
fi

shopt -s nullglob
blocks=()
for b in "${TMP}"/block-*.mmd; do
  [ -e "$b" ] && blocks+=("$b")
done
if [ "${#mmd_files[@]}" -gt 0 ]; then
  for m in "${mmd_files[@]}"; do
    blocks+=("$m")
  done
fi
if [ "${#blocks[@]}" -eq 0 ]; then
  echo "check-mermaid: no mermaid diagrams found"
  exit 0
fi

echo "check-mermaid: compiling ${#blocks[@]} diagrams"
for b in "${blocks[@]}"; do
  npx -y @mermaid-js/mermaid-cli@11 -i "$b" -o "${TMP}/out-$(basename "$b" .mmd).svg" -q
done
echo "check-mermaid: all diagrams compile"
