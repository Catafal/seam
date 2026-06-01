.PHONY: gate lint typecheck test install install-dev clean

# Gate — must pass before every commit (no exceptions)
gate: lint typecheck test

lint:
	uv run ruff check seam/ tests/

typecheck:
	uv run mypy seam/ --ignore-missing-imports

test:
	uv run pytest tests/ --tb=short -q

# Install production dependencies
install:
	uv sync

# Install all dependencies including dev
install-dev:
	uv sync --dev

# Run the dev MCP server (requires seam init first)
dev:
	uv run seam start

# Format code (not part of gate — run manually)
fmt:
	uv run ruff format seam/ tests/
	uv run ruff check seam/ tests/ --fix

# Remove build artifacts
clean:
	rm -rf dist/ build/ .pytest_cache/ .mypy_cache/ .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
