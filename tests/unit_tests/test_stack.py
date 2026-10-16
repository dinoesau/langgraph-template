"""S8 full stack plus CI seam: compose shape, env stages, CI gates.

Covers the three acceptance signals without a live deploy:
- compose brings app plus store plus cache plus metrics plus dashboards together
- per-stage env files load with compose hostnames differing from localhost dev
- CI runs lint plus unit plus journeys green before merge, secrets stay out of images
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text())


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def test_compose_defines_full_stack() -> None:
    payload = _load_yaml(ROOT / "docker-compose.yml")
    assert isinstance(payload, dict)
    services = payload.get("services")
    assert isinstance(services, dict)
    for name in ("app", "postgres", "valkey", "prometheus", "grafana"):
        assert name in services, f"compose missing service {name}"

    app = services["app"]
    assert "build" in app
    ports = [str(item) for item in app.get("ports", [])]
    assert any("8000" in item for item in ports)
    depends = app.get("depends_on", [])
    if isinstance(depends, dict):
        depends = list(depends.keys())
    assert "postgres" in depends
    assert "valkey" in depends

    assert "pgvector" in str(services["postgres"].get("image", ""))
    assert "valkey" in str(services["valkey"].get("image", "")).lower()
    assert "prom/prometheus" in str(services["prometheus"].get("image", ""))
    assert "grafana/grafana" in str(services["grafana"].get("image", ""))


def test_prometheus_scrapes_app_metrics() -> None:
    payload = _load_yaml(ROOT / "prometheus" / "prometheus.yml")
    assert isinstance(payload, dict)
    configs = payload.get("scrape_configs")
    assert isinstance(configs, list) and configs
    targets = [
        str(target)
        for config in configs
        for static in config.get("static_configs", [])
        for target in static.get("targets", [])
    ]
    assert any("app:8000" in target for target in targets)
    assert any(config.get("metrics_path") == "/metrics" for config in configs)


def test_grafana_dashboard_present() -> None:
    import json

    dashboard = json.loads((ROOT / "grafana" / "dashboards" / "latency-errors.json").read_text())
    assert dashboard["panels"]
    exprs = [
        str(target.get("expr", ""))
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    ]
    assert any("http_request" in expr or "llm_inference" in expr for expr in exprs)


def test_per_stage_env_files_load_and_hosts() -> None:
    example = _read_env_file(ROOT / ".env.example")
    assert "localhost" in example["POSTGRES_URI"]
    for stage in ("development", "staging", "production"):
        values = _read_env_file(ROOT / f".env.{stage}")
        assert values["APP_ENV"] == stage
        assert "postgres" in values["POSTGRES_URI"]
        assert "localhost" not in values["POSTGRES_URI"]
        assert values["VALKEY_HOST"] == "valkey"
        assert len(values["JWT_SECRET_KEY"]) >= 32


def test_dockerfile_keeps_secrets_out() -> None:
    text = (ROOT / "Dockerfile").read_text()
    assert "uv" in text
    assert "uvicorn" in text
    assert "src.api.app:app" in text
    for banned in ("COPY .env", "COPY --from", "OPENAI_API_KEY", "JWT_SECRET_KEY"):
        assert banned not in text
    ignore = (ROOT / ".dockerignore").read_text()
    assert ".env" in ignore


def test_ci_workflow_gates() -> None:
    payload = _load_yaml(ROOT / ".github" / "workflows" / "ci.yml")
    assert isinstance(payload, dict)
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "ruff" in text
    assert "mypy" in text
    assert "evals" in text
    assert "pytest" in text or "unit" in text
    assert "journeys" in text or "integration" in text
    assert "uv sync" in text or "setup-uv" in text


def test_make_shortcuts_follow_pattern() -> None:
    text = (ROOT / "Makefile").read_text()
    assert "docker-up:" in text
    assert "stack-up:" in text
    assert "docker compose" in text
