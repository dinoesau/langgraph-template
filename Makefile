.DEFAULT_GOAL := all
.PHONY: all install dev test tests integration_tests migrate migrate_history lint lint_package lint_tests format spell_check spell_fix docker-up stack-up stack-down stack-logs help

TEST_FILE ?= tests/unit_tests/
ENV_FILE ?= .env.development

all: help

install:
	uv sync

dev:
	uv run langgraph dev

test tests:
	uv run pytest $(TEST_FILE)

integration_tests:
	uv run pytest tests/integration_tests

migrate:
	uv run alembic upgrade head

migrate_history:
	uv run alembic history

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

docker-up:
	docker compose up -d postgres valkey

stack-up:
	docker compose --env-file $(ENV_FILE) up -d --build

stack-down:
	docker compose down

stack-logs:
	docker compose logs -f

help:
	@echo "Targets:"
	@echo "  all               - default, muestra esta ayuda"
	@echo "  install           - instala dependencias con uv sync"
	@echo "  dev               - inicia LangGraph dev server"
	@echo "  test / tests      - unit tests en \$$(TEST_FILE) (default: $(TEST_FILE))"
	@echo "  integration_tests - pytest sobre tests/integration_tests"
	@echo "  migrate           - aplica migraciones con alembic upgrade head"
	@echo "  migrate_history   - shows alembic migration history"
	@echo "  lint              - lint_package + lint_tests (ruff + mypy --strict)"
	@echo "  lint_package      - ruff + mypy --strict sobre src/"
	@echo "  lint_tests        - ruff + mypy --strict sobre tests/"
	@echo "  format            - ruff format + fix de imports (reglas I)"
	@echo "  spell_check       - codespell (solo reporta)"
	@echo "  spell_fix         - codespell con --write-changes"
	@echo "  docker-up         - inicia postgres y valkey con docker compose up -d"
	@echo "  stack-up          - levanta el stack completo con docker compose --env-file \$$(ENV_FILE) up -d --build"
	@echo "  stack-down        - detiene el stack con docker compose down"
	@echo "  stack-logs        - sigue los logs con docker compose logs -f"
