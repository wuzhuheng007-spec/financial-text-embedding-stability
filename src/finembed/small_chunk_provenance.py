"""Fail-closed runtime provenance for the small-chunk preflight and future runner.

This module inspects imports and provider availability. It never creates an
ONNX session, loads weights, or invokes an encoder.
"""
from __future__ import annotations

import hashlib
import importlib
from importlib import metadata
from pathlib import Path
import platform
import sys


class RuntimeProvenanceError(RuntimeError):
    """The active runtime differs from the explicitly requested checkout."""


CRITICAL_MODULES = (
    "finembed",
    "finembed.analysis",
    "finembed.config",
    "finembed.extract",
    "finembed.m6",
    "finembed.model",
    "finembed.run",
    "finembed.sec",
    "finembed.small_chunk_decision",
    "finembed.small_chunk_metrics",
    "finembed.sensitivity_preflight",
    "finembed.small_chunk_provenance",
    "finembed.small_chunk_runner",
)

DEPENDENCIES = (
    ("numpy", "numpy"),
    ("onnxruntime", "onnxruntime"),
    ("tokenizers", "tokenizers"),
    ("yaml", "PyYAML"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _module_file(name: str) -> Path:
    try:
        module = importlib.import_module(name)
        source = getattr(module, "__file__", None)
        if source is None:
            raise RuntimeProvenanceError(f"Imported module has no source file: {name}")
        return Path(source).resolve(strict=True)
    except (ImportError, OSError) as exc:
        raise RuntimeProvenanceError(f"Cannot inspect imported module {name}: {exc}") from exc


def _dependency_record(import_name: str, distribution_name: str) -> dict:
    source = _module_file(import_name)
    module = importlib.import_module(import_name)
    try:
        distribution_version = metadata.version(distribution_name)
    except metadata.PackageNotFoundError as exc:
        raise RuntimeProvenanceError(f"Missing distribution metadata: {distribution_name}") from exc
    module_version = getattr(module, "__version__", None)
    if module_version is None or str(module_version) != distribution_version:
        raise RuntimeProvenanceError(
            f"Imported {import_name} version differs from {distribution_name} metadata")
    return {"distribution": distribution_name, "version": distribution_version,
            "module_path": str(source), "module_sha256": _sha256(source)}


def capture_runtime_provenance(project_root: Path, *,
                               required_provider: str = "CPUExecutionProvider") -> dict:
    """Capture only when critical imports resolve to ``project_root/src``.

    The caller must derive ``project_root`` from the selected checkout or a
    trusted receipt, not from an already imported ``finembed`` module.
    """
    try:
        root = Path(project_root).resolve(strict=True)
        package_dir = (root / "src" / "finembed").resolve(strict=True)
        executable = Path(sys.executable).resolve(strict=True)
    except OSError as exc:
        raise RuntimeProvenanceError(f"Runtime path is unavailable: {exc}") from exc
    if not package_dir.is_dir() or not package_dir.is_relative_to(root):
        raise RuntimeProvenanceError("Requested checkout has no internal src/finembed package")
    if not required_provider:
        raise RuntimeProvenanceError("A required ONNX Runtime provider must be declared")

    modules = {}
    for name in CRITICAL_MODULES:
        expected_name = "__init__.py" if name == "finembed" else name.rsplit(".", 1)[1] + ".py"
        expected_path = (package_dir / expected_name).resolve()
        imported_path = _module_file(name)
        if imported_path != expected_path:
            raise RuntimeProvenanceError(
                f"Imported {name} comes from {imported_path}, expected {expected_path}")
        modules[name] = {"path": str(imported_path), "sha256": _sha256(imported_path)}

    dependencies = {name: _dependency_record(name, distribution)
                    for name, distribution in DEPENDENCIES}
    ort = importlib.import_module("onnxruntime")
    providers = ort.get_available_providers()
    if not isinstance(providers, list) or required_provider not in providers:
        raise RuntimeProvenanceError(f"Required ONNX Runtime provider unavailable: {required_provider}")
    return {"schema_version": 1, "kind": "SMALL_CHUNK_RUNTIME_PROVENANCE",
            "project_root": str(root),
            "interpreter": {"executable": str(executable),
                            "implementation": platform.python_implementation(),
                            "version": platform.python_version()},
            "critical_modules": modules, "dependencies": dependencies,
            "ort_available_providers": providers, "required_provider": required_provider,
            "onnx_session_created": False}


def verify_runtime_provenance(project_root: Path, expected: dict, *,
                              required_provider: str = "CPUExecutionProvider") -> dict:
    """Refuse reuse when the active interpreter, imports or provider drift."""
    if not isinstance(expected, dict) or expected.get("kind") != "SMALL_CHUNK_RUNTIME_PROVENANCE":
        raise RuntimeProvenanceError("Expected runtime provenance is missing or malformed")
    current = capture_runtime_provenance(project_root, required_provider=required_provider)
    if current != expected:
        changed = sorted(key for key in current if current[key] != expected.get(key))
        raise RuntimeProvenanceError(f"Runtime provenance changed: {', '.join(changed)}")
    return {"status": "PASS_RUNTIME_PROVENANCE_UNCHANGED",
            "project_root": current["project_root"],
            "critical_module_count": len(current["critical_modules"]),
            "required_provider": required_provider,
            "onnx_session_created": False}
