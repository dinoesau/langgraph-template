.DEFAULT_GOAL := all
.PHONY: all install dev test tests integration_tests lint lint_package lint_tests format spell_check spell_fix check_mermaid db-upgrade db-revision db-downgrade db-history help

TEST_FILE ?= tests/unit_tests/

all: help

install:
	uv sync

dev:
	uv run langgraph dev

test tests:
	uv run pytest $(TEST_FILE)

integration_tests:
	uv run pytest tests/integration_tests

lint: lint_package lint_tests

lint_package:
	uv run ruff check src/
	uv run ruff format --check src/
	uv run ruff check --select I src/
	uv run mypy --strict src/

lint_tests:
	uv run ruff check tests/
	uv run ruff format --check tests/
	uv run ruff check --select I tests/
	uv run mypy --strict tests/

format:
	uv run ruff format src/ tests/
	uv run ruff check --select I --fix src/ tests/

spell_check:
	uv run codespell --toml pyproject.toml

spell_fix:
	uv run codespell --toml pyproject.toml --write-changes

check_mermaid:
	bash scripts/check-mermaid.sh $(PATHS)

db-upgrade:
	uv run alembic upgrade head

db-revision:
	uv run alembic revision --autogenerate -m "$(MSG)"

db-downgrade:
	uv run alembic downgrade -1

db-history:
	uv run alembic history

help:
	@echo "Targets:"
	@echo "  all               - default, muestra esta ayuda"
	@echo "  install           - instala dependencias con uv sync"
	@echo "  dev               - inicia LangGraph dev server"
	@echo "  test / tests      - unit tests en \$$(TEST_FILE) (default: $(TEST_FILE))"
	@echo "  integration_tests - pytest sobre tests/integration_tests"
	@echo "  lint              - lint_package + lint_tests (ruff + mypy --strict)"
	@echo "  lint_package      - ruff + mypy --strict sobre src/"
	@echo "  lint_tests        - ruff + mypy --strict sobre tests/"
	@echo "  format            - ruff format + fix de imports (reglas I)"
	@echo "  spell_check       - codespell (solo reporta)"
	@echo "  spell_fix         - codespell con --write-changes"
	@echo "  check_mermaid [PATHS=...] - compila diagramas mermaid dados o los del repo (necesita npx)"
	@echo "  db-upgrade      - aplica migraciones con alembic upgrade head"
	@echo "  db-revision MSG=... - genera migracion con autogenerate y mensaje MSG"
	@echo "  db-downgrade    - retrocede una migracion con alembic downgrade -1"
	@echo "  db-history      - muestra historial con alembic history"
