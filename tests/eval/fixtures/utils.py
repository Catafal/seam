"""Utility functions — ordinary calls and imports.

Used by the recall regression harness to test:
  - seam_search LOCATE coverage for simple functions
  - seam_trace ordinary call path (e.g. format_result → render_output)
  - seam_context callers coverage
"""


def parse_config(path: str) -> dict:
    """Parse a JSON config file and return its contents as a dict."""
    import json
    with open(path) as f:
        return json.load(f)


def format_result(value: object) -> str:
    """Format a result value as a human-readable string."""
    return f"Result: {value!r}"


def render_output(value: object) -> str:
    """Render the value using format_result and add a newline."""
    return format_result(value) + "\n"


def compute_checksum(data: bytes) -> str:
    """Compute a hex SHA-256 checksum of data."""
    import hashlib
    return hashlib.sha256(data).hexdigest()


def log_result(label: str, value: object) -> None:
    """Log a labelled result to stdout."""
    print(f"[{label}] {format_result(value)}")
