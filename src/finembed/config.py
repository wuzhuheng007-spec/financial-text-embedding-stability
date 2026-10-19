"""Strict smoke configuration with paths rooted in the project."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import hashlib
import re

import yaml


@dataclass(frozen=True)
class Company:
    ticker: str
    name: str


@dataclass(frozen=True)
class Config:
    source: Path
    source_sha256: str
    root: Path
    as_of: date
    timezone: str
    ticker: str
    company_name: str
    companies: tuple[Company, ...]
    first_run_symbol_limit: int | None
    cache: Path
    artifacts: Path
    model_cache: Path
    min_interval_seconds: float
    max_attempts: int
    timeout_seconds: int
    max_response_bytes: int
    model_id: str
    model_revision: str
    tokenizer_revision: str
    onnx_file: str
    onnx_sha256: str
    tokenizer_sha256: str
    model_config_sha256: str
    pooling_config_sha256: str
    body_tokens: int
    alternate_body_tokens: int
    overlap_tokens: int
    normalize_chunks: bool
    normalize_documents: bool
    top_k: int
    batch_size: int
    provider: str
    repeat_tolerance: float


def load_config(path: str | Path) -> Config:
    source = Path(path).resolve(strict=True)
    raw = source.read_bytes()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("Config must be a YAML mapping")
    study, sec, paths, embedding = (data[k] for k in ("study", "sec", "paths", "embedding"))
    if any(not isinstance(x, dict) for x in (study, sec, paths, embedding)):
        raise ValueError("study, sec, paths and embedding must be mappings")
    companies = study["companies"]
    if not isinstance(companies, list) or not companies or not isinstance(companies[0], dict):
        raise ValueError("study.companies must have a first company")
    parsed_companies = tuple(Company(str(x["ticker"]).upper(), str(x["name"])) for x in companies)
    if any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", x.ticker) for x in parsed_companies):
        raise ValueError("Invalid company ticker")
    if len({x.ticker for x in parsed_companies}) != len(parsed_companies):
        raise ValueError("Duplicate company ticker")
    first = parsed_companies[0]
    root = source.parent.parent

    def local_path(value: str) -> Path:
        p = (root / value).resolve()
        if not p.is_relative_to(root):
            raise ValueError("Configured output path escapes project root")
        return p

    out = Config(
        source=source,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        root=root,
        as_of=date.fromisoformat(str(study["as_of"])),
        timezone=str(study["timezone"]),
        ticker=first.ticker,
        company_name=first.name,
        companies=parsed_companies,
        first_run_symbol_limit=study.get("first_run_symbol_limit"),
        cache=local_path(str(paths["cache"])),
        artifacts=local_path(str(paths["artifacts"])),
        model_cache=local_path(str(paths["model_cache"])),
        min_interval_seconds=float(sec["min_interval_seconds"]),
        max_attempts=int(sec["max_attempts"]),
        timeout_seconds=int(sec["timeout_seconds"]),
        max_response_bytes=int(sec["max_response_bytes"]),
        model_id=str(embedding["model_id"]),
        model_revision=str(embedding["revision"]),
        tokenizer_revision=str(embedding["tokenizer_revision"]),
        onnx_file=str(embedding["onnx_file"]),
        onnx_sha256=str(embedding["onnx_sha256"]),
        tokenizer_sha256=str(embedding["tokenizer_sha256"]),
        model_config_sha256=str(embedding["model_config_sha256"]),
        pooling_config_sha256=str(embedding["pooling_config_sha256"]),
        body_tokens=int(embedding["body_tokens"]),
        alternate_body_tokens=int(embedding["alternate_body_tokens"]),
        overlap_tokens=int(embedding["overlap_tokens"]),
        normalize_chunks=embedding["normalize_chunks"],
        normalize_documents=embedding["normalize_documents"],
        top_k=int(embedding["top_k"]),
        batch_size=int(embedding["batch_size"]),
        provider=str(embedding["provider"]),
        repeat_tolerance=float(embedding["repeat_tolerance"]),
    )
    if out.timezone != "America/New_York":
        raise ValueError("SEC acceptance dates require America/New_York in this smoke implementation")
    if out.min_interval_seconds < 0.1 or not 1 <= out.max_attempts <= 5:
        raise ValueError("SEC rate/retry configuration is outside safe bounds")
    if not 1 <= out.timeout_seconds <= 120 or not 1024 <= out.max_response_bytes <= 100_000_000:
        raise ValueError("SEC response limits are outside safe bounds")
    if out.first_run_symbol_limit is not None:
        raise ValueError("first_run_symbol_limit must be null for the expanded demonstration corpus")
    if not re.fullmatch(r"[0-9a-f]{40}", out.model_revision) or out.tokenizer_revision != out.model_revision:
        raise ValueError("Model/tokenizer must share an exact 40-hex revision")
    if out.onnx_file != "onnx/model.onnx" or out.overlap_tokens != 0:
        raise ValueError("This protocol requires the pinned ONNX file and zero overlap")
    if any(not re.fullmatch(r"[0-9a-f]{64}", h) for h in (out.onnx_sha256, out.tokenizer_sha256,
                                                            out.model_config_sha256, out.pooling_config_sha256)):
        raise ValueError("Expected model file hashes must be SHA-256")
    if out.body_tokens != 128 or out.alternate_body_tokens != 192:
        raise ValueError("This protocol requires 128/192 body-token chunk lengths")
    if not isinstance(out.normalize_chunks, bool) or not isinstance(out.normalize_documents, bool):
        raise ValueError("Normalization flags must be booleans")
    if not 1 <= out.top_k < len(out.companies) or not 1 <= out.batch_size <= 64:
        raise ValueError("Invalid top_k or batch size")
    if out.provider != "CPUExecutionProvider" or not 0 < out.repeat_tolerance <= 1e-3:
        raise ValueError("Invalid provider or repeat tolerance")
    return out

