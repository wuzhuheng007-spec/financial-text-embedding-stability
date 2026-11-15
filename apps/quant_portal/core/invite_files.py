"""Store invite codes only in a private local file, never in HTTP URLs or logs."""
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError


def write_private_invite(path: Path, code: str) -> Path:
    private_root = (Path(settings.BASE_DIR) / "var").resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(private_root):
        raise CommandError("Invite output must stay inside app/var")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, (code + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    try:
        os.chmod(resolved, 0o600)
    except OSError:
        # Windows ACLs differ; path remains under the current user's private app/var.
        pass
    return resolved
