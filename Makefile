.PHONY: test lint run-dashboard run-scenario query-telemetry

test:
	uv run pytest

lint:
	uv run ruff check .

run-dashboard:
	uv run python -m simulation.dashboard_runner $(ARGS)

run-scenario:
	uv run python -m simulation.runner $(ARGS)

query-telemetry:
	uv run python scripts/query_telemetry.py $(ARGS)
