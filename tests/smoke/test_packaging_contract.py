"""
Packaging / dependency-declaration contract (Phase A, A3).

These tests exist because the project has previously worked in developer
environments while the canonical dependency declaration was incomplete or an
entry point was broken -- failures that only surface in a clean install.

They are deliberately cheap and import-only (no network, no DB, no server), so
they can run first in CI and fail fast with an obvious cause:

  - every first-party runtime package/module imports;
  - every declared console script loads and responds to --help;
  - the API application object imports by its canonical path;
  - every third-party module imported by first-party runtime code is either
    declared as a runtime dependency or is a *deliberately guarded* optional
    import (guarded = wrapped in try/except so a missing package degrades
    instead of crashing).

The last one is the real protection: it fails when someone adds an
`import somelib` to runtime code without declaring it, even if that package
happens to be installed on the developer's machine.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import pkgutil
import re
import subprocess
import sys
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[2]

# First-party runtime packages that must import cleanly in a clean environment.
FIRST_PARTY_PACKAGES = [
    "bartholomew",
    "identity_interpreter",
    "bartholomew_api_bridge_v0_1",
]

# Console scripts declared in pyproject.toml's [project.scripts].
DECLARED_CONSOLE_SCRIPTS = ["bartholomew", "bartholomew-backfill-fts"]

# Third-party imports in first-party runtime code that are intentionally NOT
# declared as hard runtime dependencies, because each is wrapped in a
# try/except that degrades gracefully when the package is absent. Each entry
# must stay genuinely guarded -- `test_optional_imports_are_actually_guarded`
# below enforces that, so this set cannot be used to silence a real undeclared
# dependency.
GUARDED_OPTIONAL_IMPORTS = {
    # Optional real-embeddings backend; embedding_engine falls back to a
    # deterministic hash embedder (documented in requirements.txt).
    "sentence_transformers",
    # Optional local-LLM client; llm_stub sets HAS_OLLAMA_CLIENT=False without it.
    "ollama",
}

# Modules that are known to fail a bare import because they are legacy
# duplicate entry points requiring a specific working directory, rather than
# part of the supported import surface. Kept explicit (not silently skipped)
# so the list is reviewable.
KNOWN_NON_IMPORTABLE = {
    # Legacy shim expecting cwd == bartholomew_api_bridge_v0_1/ ("from
    # services.api.app import app"). The supported entry points are the repo
    # root app.py and bartholomew_api_bridge_v0_1.services.api.app.
    "bartholomew_api_bridge_v0_1.app",
}


def _dist_base(spec: str) -> str:
    """Normalize a requirement string to its distribution name."""
    return re.split(r"[<>=!\[; ]", spec.strip())[0].lower().replace("_", "-")


def _declared_runtime_distributions() -> set[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {_dist_base(d) for d in data["project"]["dependencies"]}


def _iter_first_party_modules():
    """Yield importable module names for every first-party runtime package."""
    for root in FIRST_PARTY_PACKAGES:
        yield root
        spec = importlib.util.find_spec(root)
        if spec is None or not spec.submodule_search_locations:
            continue
        for info in pkgutil.walk_packages(spec.submodule_search_locations, prefix=root + "."):
            yield info.name


def _third_party_imports_in_runtime_code() -> dict[str, set[str]]:
    """Map third-party top-level module -> set of first-party files importing it."""
    stdlib = set(sys.stdlib_module_names)
    first_party_roots = set(FIRST_PARTY_PACKAGES) | {"scripts", "services", "tests", "conftest"}
    found: dict[str, set[str]] = {}

    for root in FIRST_PARTY_PACKAGES:
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
                continue
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules = [node.module.split(".")[0]]
                for module in modules:
                    if module in stdlib or module in first_party_roots or module.startswith("_"):
                        continue
                    found.setdefault(module, set()).add(str(path.relative_to(REPO_ROOT)))
    return found


@pytest.mark.smoke
@pytest.mark.parametrize("package", FIRST_PARTY_PACKAGES)
def test_first_party_package_imports(package: str) -> None:
    """Each first-party runtime package imports in the installed environment."""
    importlib.import_module(package)


@pytest.mark.smoke
def test_every_first_party_module_imports() -> None:
    """Every submodule of every first-party runtime package imports.

    This is what catches an entry point that is broken at import time (e.g. an
    API-incompatible decorator argument) even when nothing else imports it.
    """
    failures: list[str] = []
    for name in _iter_first_party_modules():
        if name in KNOWN_NON_IMPORTABLE:
            continue
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - reporting every failure is the point
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failures, "first-party modules failed to import:\n  " + "\n  ".join(failures)


@pytest.mark.smoke
def test_api_application_imports_by_canonical_path() -> None:
    """The FastAPI app imports by the path the server entry point uses."""
    from bartholomew_api_bridge_v0_1.services.api.app import app

    assert app is not None
    # The repo-root shim uvicorn uses (`uvicorn app:app`) must expose the same object.
    root_shim = importlib.import_module("app")
    assert root_shim.app is app


@pytest.mark.smoke
@pytest.mark.parametrize("script", DECLARED_CONSOLE_SCRIPTS)
def test_declared_console_script_runs_help(script: str) -> None:
    """Every console script in [project.scripts] loads and responds to --help.

    Uses `python -m` style resolution via the installed script so this fails
    when the entry point's module raises at import time.
    """
    result = subprocess.run(  # noqa: S603 - fixed, non-user-controlled argv
        [script, "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,  # assert on returncode below so the message is useful
    )
    assert result.returncode == 0, (
        f"`{script} --help` exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.mark.smoke
def test_no_undeclared_third_party_runtime_imports() -> None:
    """Third-party imports in runtime code are declared, or guarded-optional."""
    declared = _declared_runtime_distributions()
    module_to_dists = packages_distributions()
    offenders: list[str] = []

    for module, files in sorted(_third_party_imports_in_runtime_code().items()):
        if module in GUARDED_OPTIONAL_IMPORTS:
            continue
        dists = {d.lower().replace("_", "-") for d in module_to_dists.get(module, [])}
        if dists & declared:
            continue
        offenders.append(
            f"{module} (installed as {sorted(dists) or 'nothing'}) "
            f"imported by {sorted(files)[0]} but not in pyproject dependencies",
        )

    assert not offenders, (
        "undeclared third-party runtime imports:\n  "
        + "\n  ".join(offenders)
        + "\n\nEither add the distribution to pyproject [project.dependencies], "
        "or guard the import and add it to GUARDED_OPTIONAL_IMPORTS."
    )


@pytest.mark.smoke
def test_optional_imports_are_actually_guarded() -> None:
    """Every entry in GUARDED_OPTIONAL_IMPORTS is imported inside a try block.

    Without this, GUARDED_OPTIONAL_IMPORTS would be a way to silence the
    undeclared-dependency test above rather than a statement of fact.
    """
    unguarded: list[str] = []

    for root in FIRST_PARTY_PACKAGES:
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
                continue

            guarded_nodes: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Try):
                    for child in ast.walk(node):
                        guarded_nodes.add(id(child))

            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules = [node.module.split(".")[0]]
                for module in modules:
                    if module in GUARDED_OPTIONAL_IMPORTS and id(node) not in guarded_nodes:
                        rel = path.relative_to(REPO_ROOT)
                        unguarded.append(f"{module} imported unguarded at {rel}:{node.lineno}")

    assert (
        not unguarded
    ), "imports listed as guarded-optional are not inside a try/except:\n  " + "\n  ".join(
        unguarded,
    )
