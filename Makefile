.PHONY: gate lint typecheck test install install-dev build-web test-web-visual bench-semantic bench-semantic-ann eval eval-generate eval-answerability soak clean

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

# Build the frontend SPA and emit it to seam/_web/ (included in the wheel).
# Run this before uv build / uv publish to ensure the latest UI ships.
# Release ritual: make build-web → uv build → uv publish
build-web:
	cd web && npm ci && npm run build

# Optional browser QA for the Explorer Topology/3D surface. This builds the SPA,
# starts a loopback Seam server against a deterministic fixture, and checks
# canvas pixel invariants in Chromium desktop/mobile.
test-web-visual:
	cd web && npm ci && npx playwright install chromium && npm run test:visual

# Semantic recall benchmark — requires [semantic] extra + a one-time model download.
# Prerequisites: pip install 'seam-mcp[semantic]'  &&  seam init --semantic
# NOT part of `make gate` (needs fastembed + network for first run).
bench-semantic:
	uv run python benchmarks/semantic_recall.py

# Deterministic offline recall@K + MRR harness over the eval fixture repo.
# Prints per-query recall and aggregate recall@10 / MRR. Fully offline, no LLM.
# The same queries run in `make gate` (via `make test`) — this target just prints the
# numbers in a human-readable format WITHOUT running lint/typecheck.
eval:
	uv run python -m tests.eval.eval_report

# Regenerate tests/eval/golden.json from the current fixture + live index.
# Run this after changing fixture files to update the SHA-stamp and expected symbols.
eval-generate:
	uv run python tests/eval/gen_golden.py

# Optional deterministic answerability benchmark over the eval fixture.
# It measures whether current Seam outputs answer natural-language agent questions
# with enough facts, evidence, caveats, and low output cost. Kept out of gate while
# the scenario set evolves.
eval-answerability:
	uv run python -m tests.eval.answerability_report

# Run the vitest unit suite for the npm shim (pkg/npm/lib/invocation.test.js).
# Node-gated: silently skipped if node is not on PATH.
# NOT part of `make gate` (like no-egress / bench-semantic — Node is not guaranteed
# in the Python CI environment).
test-npm:
	@command -v node >/dev/null 2>&1 || { echo "test-npm: node not found, skipping"; exit 0; }
	# `npm ci` first so the target is self-contained on a clean checkout / CI —
	# node_modules is gitignored, so `npm test` alone fails with "vitest: not found".
	# `npm ci` uses the committed package-lock.json (reproducible, no lockfile drift).
	cd pkg/npm && npm ci --silent && npm test

# ANN scale benchmark — brute-force vs sqlite-vec KNN latency + recall@K on synthetic embeddings.
# Prerequisites: pip install 'seam-code[semantic-ann]'  (adds sqlite_vec + numpy)
# Runs on SYNTHETIC data — no existing index, no model download needed.
# NOT part of `make gate` (mirrors bench-semantic / soak — local/optional-CI only).
# Pass --sizes to measure larger scales: make bench-semantic-ann ARGS="--sizes 10000 50000 100000"
bench-semantic-ann:
	uv run python benchmarks/semantic_ann_scale.py $(ARGS)

# Sustained mixed read-load soak against the current index (P5.5).
# Surfaces leaks / slow-query paths locally. Run with diagnostics on to also
# capture an NDJSON trace: `SEAM_DIAGNOSTICS=1 make soak`.
# Requires an existing index (`seam init` first). NOT part of `make gate`.
soak:
	uv run python benchmarks/soak.py --iterations 200

# Remove build artifacts
clean:
	rm -rf dist/ build/ .pytest_cache/ .mypy_cache/ .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
