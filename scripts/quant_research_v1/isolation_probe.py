"""Isolation probe for the COMPUTE container (acceptance drills; finding F1).

Started by quant-research-job.sh --probe-isolation with EXACTLY the compute container's flags. It tries to change
the read-only input through every alias it can reach (direct path, /proc/self/root, rename, delete, chmod,
truncate, utime, hard/soft links, new files and directories) and to write outside its own work directory, then
records what the kernel answered. It never relies on environment variables or on file modes for its verdict:
DENIED means the system call itself failed. Exit 0 only when every forbidden operation was denied, the input
hash is unchanged and its own work directory is writable.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import sys
from pathlib import Path


def sha(p: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def attempt(op: str, fn) -> dict:
    try:
        fn()
        return {"op": op, "result": "SUCCEEDED"}
    except OSError as e:
        return {"op": op, "result": "DENIED", "errno": errno.errorcode.get(e.errno, str(e.errno))}


def mounts() -> list:
    out = []
    try:
        for ln in Path("/proc/self/mountinfo").read_text().splitlines():
            p = ln.split()
            sep = p.index("-")
            out.append({"mount": p[4], "root": p[3], "options": p[5], "fstype": p[sep + 1]})
    except (OSError, ValueError):
        pass
    return out


SYSTEM_MOUNTS = ("/", "/proc", "/dev", "/sys", "/tmp", "/etc/hosts", "/etc/hostname", "/etc/resolv.conf")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input"); ap.add_argument("--work", default="/work")
    a = ap.parse_args(argv)
    inp, work = Path(a.input), Path(a.work)
    snap = inp / "market_research.sqlite"
    files_before = {p.name: sha(p) for p in sorted(inp.iterdir()) if p.is_file()}
    forbidden = [
        ("create a new file in the input directory", lambda: open(inp / "probe_new_file", "x").close()),
        ("open the snapshot for writing", lambda: open(snap, "r+b").close()),
        ("append to the snapshot", lambda: open(snap, "ab").close()),
        ("rename the snapshot", lambda: os.rename(snap, inp / "probe_renamed")),
        ("rename the snapshot into the work directory", lambda: os.rename(snap, work / "probe_moved")),
        ("delete the snapshot", lambda: os.unlink(snap)),
        ("delete manifest.json", lambda: os.unlink(inp / "manifest.json")),
        ("chmod the snapshot", lambda: os.chmod(snap, 0o666)),
        ("chmod the input directory", lambda: os.chmod(inp, 0o777)),
        ("change the snapshot's timestamps", lambda: os.utime(snap, (0, 0))),
        ("truncate the snapshot", lambda: os.truncate(snap, 0)),
        ("create a directory in the input", lambda: os.mkdir(inp / "probe_dir")),
        ("symlink inside the input", lambda: os.symlink("/work", inp / "probe_symlink")),
        ("hard-link the snapshot into the work directory", lambda: os.link(snap, work / "probe_hardlink")),
        ("write through the /proc/self/root alias", lambda: open("/proc/self/root" + str(inp) + "/probe_alias", "x").close()),
        ("chmod through the /proc/self/root alias", lambda: os.chmod("/proc/self/root" + str(snap), 0o666)),
        ("write outside the work directory via ..", lambda: open(str(work) + "/../probe_escape", "x").close()),
        ("write the root filesystem", lambda: open("/probe_root_file", "x").close()),
        ("write the application directory", lambda: open("/app/probe_app_file", "x").close()),
    ]
    results = [attempt(op, fn) for op, fn in forbidden]
    files_after = {p.name: sha(p) for p in sorted(inp.iterdir()) if p.is_file()}
    own_path = work / "probe_own_file.txt"
    own = attempt("write a file in its own work directory", lambda: own_path.write_text("probe\n"))
    own_rm = attempt("remove that file", lambda: own_path.unlink())
    visible = {p: os.path.exists(p) for p in ("/research", "/inputs", "/app/apps/quant_portal/var/research",
                                               "/app/apps/quant_portal/var/research/registry.sqlite3", "/app/apps/quant_portal/var/news")}
    m = mounts()
    data_mounts = [x for x in m if x["mount"] not in SYSTEM_MOUNTS and not x["mount"].startswith(("/proc/", "/dev/", "/sys/"))]
    expected = {str(inp): "ro", str(work): "rw"}
    mount_ok = sorted(x["mount"] for x in data_mounts) == sorted(expected) and all(
        expected[x["mount"]] in x["options"].split(",") for x in data_mounts)
    res = {"schema": "compute_isolation_probe_v1", "uid": os.getuid() if hasattr(os, "getuid") else None,
           "input_files_sha256_before": files_before, "input_files_sha256_after": files_after,
           "input_unchanged": files_before == files_after and snap.name in files_after,
           "forbidden_operations": results, "all_forbidden_denied": all(r["result"] == "DENIED" for r in results),
           "own_work_write": own, "own_work_cleanup": own_rm, "data_mounts": data_mounts, "data_mounts_exactly_input_ro_and_work_rw": mount_ok,
           "registry_or_other_var_paths_visible": visible}
    res["ok"] = (res["input_unchanged"] and res["all_forbidden_denied"] and own["result"] == "SUCCEEDED" and mount_ok
                 and not any(visible.values()))
    try:
        (work / "isolation_probe.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    except OSError:
        pass
    print(json.dumps(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
