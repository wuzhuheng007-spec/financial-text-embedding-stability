"""Package only reviewed v2.5 code and redacted receipts for handoff."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from zoneinfo import ZoneInfo
from zipfile import ZipFile, ZIP_DEFLATED


ROOT = Path(__file__).resolve().parents[1]
HANDOFFS = ROOT.parent / "quant" / "handoffs"
PREFIX = "quant_financial_input_v25_"
FILES = (
    "README.md",
    "docs/news_research/README.md",
    "docs/news_research/STATUS.json",
    "docs/news_research/CHANGELOG.md",
    "configs/market_observer_financial_text_v25_20261116.json",
    "scripts/market_observer_financial_text_v25/__init__.py",
    "scripts/market_observer_financial_text_v25/adapter.py",
    "scripts/market_observer_financial_text_v25/evaluate.py",
    "scripts/market_observer_financial_text_v25/freeze_reference.py",
    "scripts/market_observer_financial_text_v25/preflight.py",
    "scripts/market_observer_financial_text_v25/run.py",
    "scripts/package_market_observer_financial_text_v25_handoff.py",
    "tests/test_market_observer_financial_text_v25_adapter.py",
    "tests/test_market_observer_financial_text_v25_evaluate.py",
    "tests/test_market_observer_financial_text_v25_freeze.py",
    "reports/2026-11-16_market_observer_financial_text_v25.zh.md",
    "reports/2026-11-16_market_observer_financial_text_v25.en.md",
    "results/market_observer_financial_text_v25_20261116/plan_execution.csv",
    "results/market_observer_financial_text_v25_20261116/reference_freeze_redacted.json",
    "results/market_observer_financial_text_v25_20261116/preflight_frozen_v1_redacted.json",
    "results/market_observer_financial_text_v25_20261116/parse_frozen_v1_redacted.json",
    "results/market_observer_financial_text_v25_20261116/pack_frozen_v1_redacted.json",
    "results/market_observer_financial_text_v25_20261116/source_audit_frozen_v1_redacted.json",
    "results/market_observer_financial_text_v25_20261116/source_audit_helper_attempts_redacted.json",
    "results/market_observer_financial_text_v25_20261116/evaluation_frozen_v1_redacted.json",
    "results/market_observer_financial_text_v25_20261116/postrun_regression_audit_redacted.json",
    "results/market_observer_financial_text_v25_20261116/postrun_gate_interpretation_redacted.json",
    "results/market_observer_financial_text_v25_20261116/git_sync_initial_redacted.json",
)


def digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def build() -> dict:
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    folder = HANDOFFS / f"{PREFIX}{stamp}"
    zip_path = HANDOFFS / f"{PREFIX}{stamp}.zip"
    if folder.exists() or zip_path.exists():
        raise FileExistsError("Refusing to overwrite a prior handoff")
    folder.mkdir(parents=True)
    copied: dict[str, str] = {}
    for relative in FILES:
        if relative.startswith(("artifacts/", "local_only/")) or ".." in Path(relative).parts:
            raise ValueError("private or unsafe handoff member")
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(relative)
        raw = source.read_bytes()
        if source.suffix.lower() in {".py", ".json", ".csv", ".md", ".txt"}:
            raw.decode("utf-8-sig")
        if (re.search(rb"C:\\Users\\[A-Za-z0-9]", raw)
                or re.search(rb"hf_[A-Za-z0-9]{16,}", raw)
                or re.search(rb"Bearer [A-Za-z0-9_-]{12,}", raw)):
            raise ValueError("private path or credential marker in " + relative)
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if target.read_bytes() != raw:
            raise RuntimeError("handoff copy mismatch: " + relative)
        copied[relative] = digest(raw)
    readme = (
        "财务输入 v2.5 脱敏交接 / Financial input v2.5 redacted handoff\r\n"
        "读取顺序: reports/2026-11-16_market_observer_financial_text_v25.zh.md; "
        "对应英文报告; results/.../plan_execution.csv; reference/preflight/parse/pack/"
        "source-audit/evaluation receipts; then code and tests.\r\n"
        "结果: 8 个已接触训练期事件、16 份已保存原件完成一次解析与一次定额装包。"
        "60 条既有来源支持候选中，15 条经本轮逐字段核对在解析和装包后完整，"
        "1 条 EPS 脚注绑定错误；20 条段落候选预算遗漏。所有包均为 REJECTED_CANDIDATE。\r\n"
        "没有原件、原始财务数值、私人数据库、账户或凭据。没有模型调用、拟合或收益评价。"
        "不要将本开发结果当作独立测试或金融有效性结论。\r\n"
    ).encode("utf-8-sig")
    (folder / "README.txt").write_bytes(readme)
    copied["README.txt"] = digest(readme)
    manifest = {"schema": "financial_text_v25_redacted_handoff_manifest_v1",
                "created_at_asia_shanghai": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "source_scope": "2025-04-01_to_2026-03-31_saved_NVDA_MU_training_only",
                "file_sha256": copied, "private_data_included": False}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (folder / "manifest.json").write_bytes(manifest_bytes)
    with ZipFile(zip_path, "x", compression=ZIP_DEFLATED) as archive:
        for relative in [*FILES, "README.txt", "manifest.json"]:
            archive.write(folder / relative, relative)
    with ZipFile(zip_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("handoff ZIP CRC check failed")
        if set(archive.namelist()) != {*FILES, "README.txt", "manifest.json"}:
            raise RuntimeError("handoff ZIP member list mismatch")
        for relative in copied:
            if digest(archive.read(relative)) != copied[relative]:
                raise RuntimeError("handoff ZIP member SHA mismatch: " + relative)
        if archive.read("manifest.json") != manifest_bytes:
            raise RuntimeError("handoff manifest ZIP mismatch")
    result = {"schema": "financial_text_v25_handoff_validation_v1",
              "folder": str(folder), "zip": str(zip_path),
              "zip_sha256": digest(zip_path.read_bytes()),
              "member_count": len(FILES) + 2,
              "crc_all_pass": True, "member_sha256_all_pass": True,
              "utf8_readback_pass": True,
              "readme_utf8_bom": readme.startswith(b"\xef\xbb\xbf"),
              "private_data_included": False}
    (folder / "VALIDATION.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))
