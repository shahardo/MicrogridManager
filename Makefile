.PHONY: test lint run-dashboard run-scenario query-telemetry

test:
	uv run pytest

lint:
	uv run ruff check .

run-dashboard:
	@echo "Not implemented yet — see docs/phase-1-dev-plan.md (M4/M9)"

run-scenario:
	uv run python -m simulation.runner

query-telemetry:
	uv run python scripts/query_telemetry.py $(ARGS)
