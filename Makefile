.PHONY: test lint run-dashboard run-scenario

test:
	uv run pytest

lint:
	uv run ruff check .

run-dashboard:
	@echo "Not implemented yet — see docs/phase-1-dev-plan.md (M4/M9)"

run-scenario:
	uv run python -m simulation.runner
