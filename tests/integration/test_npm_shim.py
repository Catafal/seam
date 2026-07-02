"""Integration smoke test for the pkg/npm shim.

WHY a Python test (not just vitest): the vitest suite tests the pure logic;
this test exercises the full bin.js → stub uvx chain to prove:
  (a) the exact argv that reaches uvx matches the pinned spec format, and
  (b) bin.js propagates the child's exit code faithfully.

Skip strategy: this test requires `node` on PATH. When node is absent (e.g. a
minimal CI image) it self-skips rather than failing, mirroring the fastembed
importorskip discipline so the Python gate stays green without Node.
"""

import json
import os
import shutil
import stat
import subprocess
import textwrap
import tomllib
from pathlib import Path

import pytest

# Skip the entire module when node is not available.
# WHY module-level skip: avoids importing subprocess etc. in environments where
# Node is absent and prevents individual test functions from each needing a guard.
if shutil.which("node") is None:
    pytest.skip("node not found — npm shim tests require Node.js", allow_module_level=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
_BIN = _ROOT / "pkg" / "npm" / "bin.js"
_NPM_PKG = _ROOT / "pkg" / "npm" / "package.json"
_PYPROJECT = _ROOT / "pyproject.toml"


def _read_npm_version() -> str:
    return json.loads(_NPM_PKG.read_text())["version"]


def _read_pyproject_version() -> str:
    return tomllib.loads(_PYPROJECT.read_text())["project"]["version"]


def _make_stub_uvx(tmp_path: Path, exit_code: int = 0) -> Path:
    """Write a tiny shell script that records its argv and exits with `exit_code`.

    WHY a shell script rather than a Python script: lighter, no interpreter
    startup path collision; the `which uvx` probe in invocation.js only checks
    existence via `which`, which works for any executable.

    The stub writes its argv (space-separated) to stdout so the test can capture
    it from the bin.js run (stdio:'inherit' in bin.js → callers capture subprocess
    stdout via PIPE).

    WHY stdout: bin.js uses stdio:'inherit', so the child's stdout/stderr both flow
    to the caller's streams. The test captures stdout via subprocess.PIPE.
    """
    stub = tmp_path / "uvx"
    # Write a POSIX shell stub that echoes its args and exits with the given code.
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            # Stub uvx — records args to stdout, exits with a controlled code.
            echo "$@"
            exit {exit_code}
            """
        )
    )
    # Make the stub executable (owner read+write+exec, group+other read+exec).
    stub.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return stub


def _run_bin(
    args: list[str],
    *,
    tmp_path: Path,
    stub_exit: int = 0,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run node bin.js with a stub uvx on PATH; return the CompletedProcess."""
    stub_dir = _make_stub_uvx(tmp_path, exit_code=stub_exit).parent

    env = {**os.environ, "PATH": f"{stub_dir}:{os.environ.get('PATH', '')}"}
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["node", str(_BIN), *args],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stub_uvx_receives_exact_args_for_simple_flag(tmp_path: Path) -> None:
    """The stub uvx receives `--from seam-code==<version> seam --version`.

    This is the canonical shape test: any extra arg or wrong order would fail.
    """
    version = _read_npm_version()
    result = _run_bin(["--version"], tmp_path=tmp_path)

    expected_args = f"--from seam-code=={version} seam --version"
    assert result.stdout.strip() == expected_args, (
        f"Expected uvx argv: {expected_args!r}\n"
        f"Got stdout:        {result.stdout.strip()!r}"
    )


def test_stub_uvx_receives_empty_args_when_no_user_args(tmp_path: Path) -> None:
    """With no user args, the stub receives exactly `--from seam-code==<ver> seam`."""
    version = _read_npm_version()
    result = _run_bin([], tmp_path=tmp_path)

    expected = f"--from seam-code=={version} seam"
    assert result.stdout.strip() == expected


def test_exit_code_propagation_zero(tmp_path: Path) -> None:
    """bin.js propagates exit code 0 from the stub."""
    result = _run_bin([], tmp_path=tmp_path, stub_exit=0)
    assert result.returncode == 0


def test_exit_code_propagation_nonzero(tmp_path: Path) -> None:
    """bin.js propagates non-zero exit codes from the stub exactly."""
    result = _run_bin([], tmp_path=tmp_path, stub_exit=42)
    assert result.returncode == 42


def test_seam_npm_from_override_replaces_spec(tmp_path: Path) -> None:
    """SEAM_NPM_FROM overrides the --from spec (e.g. for pre-release testing)."""
    result = _run_bin(
        ["init"],
        tmp_path=tmp_path,
        extra_env={"SEAM_NPM_FROM": "seam-code==0.5.0.dev1"},
    )
    # The spec in argv should use the override, not the default pinned version.
    assert "--from seam-code==0.5.0.dev1 seam init" in result.stdout.strip()


def test_seam_npm_uvx_override_uses_custom_runner(tmp_path: Path) -> None:
    """SEAM_NPM_UVX uses a custom path for uvx instead of the one found by which."""
    # Place the stub at a non-default name so the default PATH lookup would fail.
    stub = _make_stub_uvx(tmp_path, exit_code=0)
    custom_path = tmp_path / "custom-uvx"
    custom_path.write_bytes(stub.read_bytes())
    custom_path.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)

    env = {
        **os.environ,
        # Remove the stub directory from PATH so the default lookup fails.
        "PATH": os.environ.get("PATH", ""),
        "SEAM_NPM_UVX": str(custom_path),
    }

    version = _read_npm_version()
    result = subprocess.run(
        ["node", str(_BIN)],
        capture_output=True,
        text=True,
        env=env,
    )

    expected = f"--from seam-code=={version} seam"
    assert result.stdout.strip() == expected, result.stdout
    assert result.returncode == 0


def test_missing_uvx_exits_nonzero_with_guidance(tmp_path: Path) -> None:
    """When uvx is not on PATH and no override is set, bin.js exits 1 with guidance."""
    # Use an empty tmp_path as the sole PATH entry.  This guarantees neither
    # `which`/`where` NOR `uvx` are present — regardless of where node lives
    # on the host — so _defaultProbe() always returns false and resolveRunner
    # returns null.  We invoke node via its absolute path so it does not need
    # to be in PATH itself.
    #
    # WHY not use `PATH = node_dir`: if node lives in /usr/bin (which also
    # contains `which`), the probe could execute and the test would depend on
    # uvx being absent from /usr/bin — a host-configuration assumption that
    # makes the test non-deterministic across environments.
    node_path = shutil.which("node")
    assert node_path is not None, "node must be on PATH for this test to run"

    env = {**os.environ, "PATH": str(tmp_path)}
    # Also ensure no SEAM_NPM_UVX override leaks in from the parent env.
    env.pop("SEAM_NPM_UVX", None)

    result = subprocess.run(
        [node_path, str(_BIN)],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0, "Should exit non-zero when uvx is missing"
    # The guidance message must point to the uv installation docs.
    assert "https://docs.astral.sh/uv" in result.stderr, (
        f"Expected uv install URL in stderr. Got: {result.stderr!r}"
    )


def test_version_pin_matches_pyproject_version(tmp_path: Path) -> None:
    """The pinned seam-code version in argv exactly matches pyproject.toml version.

    WHY: reproducibility — the npm shim pins the exact same version it was
    released with. This test asserts the runtime behavior (not just the file
    content) to catch the case where someone edits package.json but does not
    update the pin logic, or vice versa.
    """
    pyproject_ver = _read_pyproject_version()
    result = _run_bin(["status"], tmp_path=tmp_path)

    expected_spec = f"seam-code=={pyproject_ver}"
    assert expected_spec in result.stdout, (
        f"Expected {expected_spec!r} in uvx argv. Got: {result.stdout.strip()!r}"
    )
