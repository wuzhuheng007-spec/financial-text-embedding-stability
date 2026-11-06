"""Synthetic runtime-provenance checks; these do not load a model or infer."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import importlib

import pytest

from finembed.small_chunk_provenance import (
    RuntimeProvenanceError, capture_runtime_provenance, verify_runtime_provenance,
)


ROOT = Path(__file__).resolve().parents[1]


def test_current_checkout_capture_and_verify_never_create_session(monkeypatch):
    ort = pytest.importorskip("onnxruntime")

    def forbidden_session(*_args, **_kwargs):
        pytest.fail("ONNX session constructed during provenance check")

    monkeypatch.setattr(ort, "InferenceSession", forbidden_session)
    captured = capture_runtime_provenance(ROOT)
    assert captured["project_root"] == str(ROOT)
    assert captured["critical_modules"]["finembed.model"]["path"] == str(
        ROOT / "src" / "finembed" / "model.py")
    assert captured["required_provider"] == "CPUExecutionProvider"
    assert captured["onnx_session_created"] is False
    assert verify_runtime_provenance(ROOT, captured)["status"] == "PASS_RUNTIME_PROVENANCE_UNCHANGED"


def test_wrong_checkout_and_shadowed_import_fail_closed(monkeypatch, tmp_path):
    other_package = tmp_path / "src" / "finembed"
    other_package.mkdir(parents=True)
    with pytest.raises(RuntimeProvenanceError, match="Imported finembed comes from"):
        capture_runtime_provenance(tmp_path)

    model = importlib.import_module("finembed.model")
    shadow = tmp_path / "model.py"
    shadow.write_text("# synthetic shadow module\n", encoding="utf-8")
    monkeypatch.setattr(model, "__file__", str(shadow))
    with pytest.raises(RuntimeProvenanceError, match="Imported finembed.model comes from"):
        capture_runtime_provenance(ROOT)


@pytest.mark.parametrize("field", ["interpreter", "dependencies", "ort_available_providers",
                                   "critical_modules"])
def test_changed_runtime_binding_is_rejected(field):
    pytest.importorskip("onnxruntime")
    captured = capture_runtime_provenance(ROOT)
    altered = deepcopy(captured)
    if field == "interpreter":
        altered[field]["version"] = "0.0.0"
    elif field == "dependencies":
        altered[field]["tokenizers"]["version"] = "0.0.0"
    elif field == "ort_available_providers":
        altered[field] = ["SyntheticProvider"]
    else:
        altered[field]["finembed.model"]["sha256"] = "0" * 64
    with pytest.raises(RuntimeProvenanceError, match="Runtime provenance changed"):
        verify_runtime_provenance(ROOT, altered)


def test_missing_provider_is_rejected():
    pytest.importorskip("onnxruntime")
    with pytest.raises(RuntimeProvenanceError, match="provider unavailable"):
        capture_runtime_provenance(ROOT, required_provider="SyntheticProvider")
