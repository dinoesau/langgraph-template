#!/usr/bin/env bash
# Compile every ```mermaid block in repo docs to catch syntax errors locally.
# Scans docs plus README plus AGENTS, and the wiki checkout when present.
# Fails on the first block that does not compile. Needs npx with network once.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

files=()
while IFS= read -r f; do
  files+=("$f")
done < <(find "${ROOT}/docs" "${ROOT}/README.md" "${ROOT}/AGENTS.md" -name '*.md' 2>/dev/null)
if [ -d "${ROOT}/../langgraph-template.wiki" ]; then
  while IFS= read -r f; do
    files+=("$f")
  done < <(find "${ROOT}/../langgraph-template.wiki" -name '*.md' -not -path '*/.git/*' 2>/dev/null)
fi

awk -v tmpdir="${TMP}" '
  FNR==1 { in_block=0 }
  /^```mermaid[[:space:]]*$/ { in_block=1; n++; out=tmpdir "/block-" n ".mmd"; next }
  in_block && /^```[[:space:]]*$/ { in_block=0; close(out); next }
  in_block { print > out }
' "${files[@]}"

shopt -s nullglob
blocks=("${TMP}"/block-*.mmd)
if [ "${#blocks[@]}" -eq 0 ]; then
  echo "check-mermaid: no mermaid blocks found"
  exit 0
fi

echo "check-mermaid: compiling ${#blocks[@]} blocks"
for b in "${blocks[@]}"; do
  npx -y @mermaid-js/mermaid-cli@11 -i "$b" -o "${b%.mmd}.svg" -q
done
echo "check-mermaid: all blocks compile"
