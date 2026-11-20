"""Write RELEASE_MANIFEST.json (schema m1m2_release_manifest_v3) for the M1/M2 operator scripts.

Run on the release machine, inside the repository checkout. Docker must have the images loaded.

  python deploy/linux/host/release/make_manifest.py --commit <delivery sha> --out <OUT>/RELEASE_MANIFEST.json \
      --app quant-personal-app:<sh> --app-package <OUT>/app_<sh>.tar.gz \
      --numeric quant-research-numeric:<sh> --numeric-package <OUT>/numeric_<sh>.tar.gz \
      --pre-release-backup /srv/quant/backup/host-before-<sh> --pre-release-app quant-personal-app:<old sh> \
      [--pre-release-app-id sha256:<id>]

Delivery-file hashes come from the commit (git show), not from the working tree. Image ids and revision labels
come from the local Docker daemon. Package hashes come from the files given.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

DELIVERY_FILES = [
    "deploy/linux/compose.yaml",
    "deploy/linux/host/quant-research-runner.sh", "deploy/linux/host/quant-research-job.sh",
    "deploy/linux/host/quant-restore-check.sh", "deploy/linux/host/quant-research-runner.service",
    "deploy/linux/host/quant_research_backup.py", "deploy/linux/host/backup_scope.json",
    "deploy/linux/host/release/m1m2_env.sh", "deploy/linux/host/release/m1m2_30_prerelease_backup.sh",
    "deploy/linux/host/release/m1m2_45_switch.sh", "deploy/linux/host/release/m1m2_60_resume.sh",
    "deploy/linux/host/release/m1m2_70_register_inputs.sh", "deploy/linux/host/release/m1m2_101_rollback_numeric.sh",
    "deploy/linux/host/release/m1m2_102_rollback_full.sh", "deploy/linux/host/release/make_manifest.py",
    "docs/research_platform/M1M2_DEPLOY_ROLLBACK.md",
]
FROZEN = "research/market_sentiment/phase5_strategy_v1"


def git(*a: str) -> bytes:
    return subprocess.run(["git", *a], check=True, capture_output=True).stdout


def image(tag: str) -> dict:
    out = subprocess.run(["docker", "image", "inspect", tag, "--format", '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'],
                         check=True, capture_output=True, text=True).stdout.split()
    return {"tag": tag, "local_image_id": out[0], "revision_label": out[1] if len(out) > 1 else None}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    for k in ("--commit", "--out", "--app", "--app-package", "--numeric", "--numeric-package", "--pre-release-backup", "--pre-release-app"):
        ap.add_argument(k, required=True)
    ap.add_argument("--pre-release-app-id")
    ap.add_argument("--input-sha256", default="c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e")
    ap.add_argument("--input-version", default="c5b5ec22")
    ap.add_argument("--reference-files", type=int, default=330)
    a = ap.parse_args()
    commit = git("rev-parse", a.commit).decode().strip()
    files = {f: hashlib.sha256(git("show", f"{commit}:{f}")).hexdigest() for f in DELIVERY_FILES}
    old = {"tag": a.pre_release_app, "local_image_id": a.pre_release_app_id or image(a.pre_release_app)["local_image_id"]}
    app, num = image(a.app), image(a.numeric)
    app.update({"package": Path(a.app_package).name, "package_sha256": sha(Path(a.app_package))})
    num.update({"package": Path(a.numeric_package).name, "package_sha256": sha(Path(a.numeric_package))})
    frozen = {n: hashlib.sha256(git("show", f"{commit}:{FROZEN}/{n}")).hexdigest() for n in ("run.py", "check.py", "config.json", "PROTOCOL_zh.md")}
    m = {
        "schema": "m1m2_release_manifest_v3",
        "release_id": commit[:7],
        "runtime": {"images": {"app": app, "numeric": num}, "registry_digest": "NONE (not pushed); accept by local image id + package sha256",
                    "frozen_study_files": frozen},
        "delivery": {"commit": commit, "short": commit[:7], "source_files": files,
                     "scripts": "deploy/linux/host/release/*.sh, run as: sudo bash -euo pipefail <file> </dev/null"},
        "production": {"pre_release_backup": a.pre_release_backup, "pre_release_app_image": old},
        "inputs": {"phase5_bundle": {"dataset_version": a.input_version, "sha256": a.input_sha256, "reference_files": a.reference_files}},
        "expected_platform_result": {"results_digest": "1783db80eb33f48fd9b84233491f986606655adf66e1b0f5c32de8ac3671ca06",
                                     "ledgers_raw_identical": "330/330", "primary_passes": "0/12", "scientific_status": "FIRST_SCREEN_NOT_PASSED"},
    }
    Path(a.out).write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{a.out}: release {m['release_id']}, {len(files)} delivery files, app {app['local_image_id'][:19]}, numeric {num['local_image_id'][:19]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
