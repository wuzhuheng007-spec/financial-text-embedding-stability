"""Package an explicitly reviewed, redacted v2.4 Git snapshot for handoff.

The path list is deliberately explicit. This script cannot establish source
redistribution rights or replace a human review of every selected Git blob.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
RESULT_ROOT = "results/market_observer_financial_text_v24_20261116/"
REPORT_NAMES = {
    "reports/2026-11-16_market_observer_financial_text_v24.zh.md",
    "reports/2026-11-16_market_observer_financial_text_v24.en.md",
}
EXACT_NAMES = {
    "README.md", "SHARING_SCOPE.md", "docs/news_research/README.md",
    "docs/news_research/STATUS.json", "docs/news_research/CHANGELOG.md",
    "configs/market_observer_financial_text_v24_20261116.json",
    "configs/market_observer_financial_text_v24_handoff_paths.txt",
    "scripts/market_observer_financial_text_v21/__init__.py",
    "scripts/market_observer_financial_text_v21/parser.py",
    "scripts/market_observer_financial_text_v23/__init__.py",
    "scripts/market_observer_financial_text_v23/structure.py",
    "scripts/freeze_market_observer_financial_text_v24.py",
    "scripts/package_market_observer_financial_text_v24_handoff.py",
    "reports/2026-11-16_market_observer_financial_text_v23.zh.md",
    "reports/2026-11-16_market_observer_financial_text_v23.en.md",
    "results/market_observer_financial_text_v23_20261116/postrun_false_header_diagnosis_redacted.json",
    *REPORT_NAMES,
}
PRIVATE_PATH_PARTS = {"artifacts", "local_only", "private", "credentials", "secrets", "raw"}
PRIVATE_NAME_TOKENS = {"raw", "private", "token", "credential", "secret", "body"}
PRIVATE_CONTENT = (
    re.compile(rb"hf_[A-Za-z0-9]{20,}"),
    re.compile(rb"Bearer\s+[A-Za-z0-9._-]{20,}", re.I),
    re.compile(rb'"(?:access_token|refresh_token|password|api_key)"\s*:\s*"[^" ]{8,}"', re.I),
    re.compile(rb"[A-Za-z]:[/\\]Users[/\\][^/\\\s]+[/\\]", re.I),
    re.compile(rb"-----BEGIN\s+[A-Z ]+PRIVATE\s+KEY-----"),
)
FORBIDDEN_RESULT_KEYS = {
    "raw_text", "source_text", "source_body", "original_html", "html",
    "quote", "excerpt", "paragraph_text", "cell_text", "selected_text",
    "input_text", "value_text", "raw_response", "credential", "token", "account_id",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def _allowed(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        return False
    if PRIVATE_PATH_PARTS.intersection(x.lower() for x in path.parts):
        return False
    if PRIVATE_NAME_TOKENS.intersection(re.split(r"[_\-.]", path.name.lower())):
        return False
    if name in EXACT_NAMES:
        return True
    if name.startswith("scripts/market_observer_financial_text_v24/"):
        return path.parent.as_posix() == "scripts/market_observer_financial_text_v24" and path.suffix == ".py"
    if name.startswith("tests/test_market_observer_financial_text_v24"):
        return path.parent.as_posix() == "tests" and path.suffix == ".py"
    if name.startswith(RESULT_ROOT):
        return path.parent.as_posix() == RESULT_ROOT.rstrip("/") and (
            name.endswith("_redacted.json") or name.endswith("_redacted.jsonl")
            or path.name == "plan_execution.csv"
        )
    return False


def _forbidden_json_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        found = {key for key in value if key.lower() in FORBIDDEN_RESULT_KEYS}
        for child in value.values():
            found.update(_forbidden_json_keys(child))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found.update(_forbidden_json_keys(child))
        return found
    return set()


def _review_payload(name: str, payload: bytes) -> None:
    if any(pattern.search(payload) for pattern in PRIVATE_CONTENT):
        raise ValueError(f"possible credential or personal path in {name}")
    text = payload.decode("utf-8-sig")
    if name in REPORT_NAMES and ("[PENDING" in text or "DRAFT — DO NOT PUBLISH" in text):
        raise ValueError(f"unfinished report: {name}")
    if name.startswith("results/") and name.endswith(".json"):
        keys = _forbidden_json_keys(json.loads(text))
        if keys:
            raise ValueError(f"private-looking result keys in {name}: {sorted(keys)}")
    if name.startswith(RESULT_ROOT) and name.endswith(".jsonl"):
        for line in text.splitlines():
            if line.strip():
                keys = _forbidden_json_keys(json.loads(line))
                if keys:
                    raise ValueError(f"private-looking result keys in {name}: {sorted(keys)}")
    if name.endswith(".txt") and not payload.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"Windows TXT lacks UTF-8 BOM: {name}")


def build(commit: str, paths_file: Path, out_dir: Path, zip_path: Path) -> dict:
    if out_dir.exists() or zip_path.exists():
        raise FileExistsError("handoff destination already exists; preserve prior output")
    full_commit = _git("rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    names = [line.strip() for line in paths_file.read_text(encoding="utf-8-sig").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if len(names) != len(set(names)) or not names:
        raise ValueError("path list is empty or contains duplicates")
    if not REPORT_NAMES.issubset(names) or f"{RESULT_ROOT}plan_execution.csv" not in names:
        raise ValueError("both reports and the P0-P7 ledger are required")
    payloads: dict[str, bytes] = {}
    for name in names:
        if not _allowed(name):
            raise ValueError(f"unreviewed or forbidden path: {name}")
        payload = _git("show", f"{full_commit}:{name}")
        _review_payload(name, payload)
        payloads[name] = payload
    if not any(name.startswith(RESULT_ROOT) and name.endswith("_redacted.json") for name in names):
        raise ValueError("no reviewed redacted result is listed")
    out_dir.mkdir(parents=True)
    entries = []
    for name, payload in payloads.items():
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        entries.append({"path": name, "bytes": len(payload), "sha256": _sha(payload)})
    readme = (
        "# Financial table semantics v2.4 / 财务表格语义 v2.4\n\n"
        "Read the Chinese report, English summary, P0-P5 plan-actual ledger, "
        "frozen regression receipt, configuration, parser, evaluator, and synthetic tests.\n"
        "先读中文报告、英文摘要和 P0–P5 计划—实际台账，再读冻结回执、配置、解析器、评价器及合成测试。\n\n"
        "This package excludes original filing bodies, private cell-level review, "
        "model inputs, credentials, account data, and private databases. "
        "The four-table training regression is not an independent test or production fact admission. "
        "The eight-event build, 20,000-character adapter, model calls and financial evaluation were not run.\n"
        "本包不含申报原文、私人逐单元格核对、模型输入、凭据、账户资料或数据库。"
        "四表训练回归不是独立检验或正式事实准入；八事件构建、固定预算装包、模型及收益评价未运行。\n\n"
        f"Committed snapshot: {full_commit}\n"
    ).encode("utf-8")
    (out_dir / "HANDOFF_README.md").write_bytes(readme)
    entries.append({"path": "HANDOFF_README.md", "bytes": len(readme), "sha256": _sha(readme)})
    manifest = (json.dumps({"schema": "financial_text_v24_handoff_manifest_v1",
                            "commit": full_commit, "files": entries}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (out_dir / "MANIFEST_SHA256.json").write_bytes(manifest)
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                archive.write(path, f"{out_dir.name}/{path.relative_to(out_dir).as_posix()}")
    with zipfile.ZipFile(zip_path) as archive:
        failed = archive.testzip()
        if failed:
            raise ValueError(f"ZIP CRC failed: {failed}")
        for path in sorted(out_dir.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(out_dir).as_posix()
            if archive.read(f"{out_dir.name}/{name}") != path.read_bytes():
                raise ValueError(f"ZIP content mismatch: {name}")
    return {"folder": str(out_dir), "zip": str(zip_path),
            "zip_sha256": _sha(zip_path.read_bytes()), "crc": "PASS",
            "commit": full_commit, "selected_files": len(payloads)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="reviewed Git commit SHA")
    parser.add_argument("--paths-file", type=Path, required=True, help="manually reviewed path list")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.commit, args.paths_file.resolve(), args.out_dir.resolve(),
                           args.zip.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
