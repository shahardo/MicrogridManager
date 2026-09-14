.PHONY: test lint run-dashboard run-scenario query-telemetry forecast-report dispatch-report

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

forecast-report:
	uv run python -m simulation.forecast_report $(ARGS)

dispatch-report:
	uv run python -m simulation.dispatch_report $(ARGS)
