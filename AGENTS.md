# AGENTS.md - langgraph-base

## Docs (keep them current, no exceptions)

- The `## Documentation` table in `README.md` is the index of all docs.
- Never add a doc without adding its row there.
- Never change code, config, or layout without updating the matching doc in the same diff.
- New valid words go to `.codespellignore`, never silence codespell inline.
- `Makefile` help text must describe what each target actually runs.
- Never leave `langgraph.json` graph pointers and docs pointing at different files.

## Agent skills

### Issue tracker

Issues live as GitHub issues via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context with root `GLOSSARY.md` and `docs/adr/`. See `docs/agents/domain.md`.
