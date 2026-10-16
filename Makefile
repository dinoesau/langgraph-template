.PHONY: install dev test lint

install:
	uv sync

dev:
	uv run langgraph dev

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .
