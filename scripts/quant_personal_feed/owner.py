"""Resolve the single personal-cache owner from the portal account database.

Two resolvers exist:

* ``sqlite_portal_db`` - the original Windows personal install, where the
  portal runs on the ignored ``apps/quant_portal/var/dev.sqlite3`` file.
* ``django_orm`` - Linux/container deployment, where the portal account table
  lives in PostgreSQL. The lookup goes through Django's configured user model
  and database settings instead of issuing a raw SQLite query.

Either way the private feed cache keeps its ``owner_binding`` row; a resolved
ID that differs from the bound owner is refused by ``PersonalFeedStore``.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "apps/quant_portal"
RESOLVERS = ("sqlite_portal_db", "django_orm")


class OwnerResolutionError(ValueError):
    """Raised without account details beyond the configured identifier."""


def resolver_name(config: dict) -> str:
    name = os.environ.get("QUANT_OWNER_RESOLVER") or config.get("owner_resolver") or "sqlite_portal_db"
    if name not in RESOLVERS:
        raise OwnerResolutionError(f"Unknown owner resolver: {name}")
    return name


def _sqlite_owner(config: dict) -> int:
    db = (ROOT / config["portal_db"]).resolve()
    if not db.is_relative_to(ROOT):
        raise OwnerResolutionError("Configured portal database leaves the repository")
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as connection:
        rows = connection.execute("SELECT id FROM auth_user WHERE username=?",
                                  (config["owner_username"],)).fetchall()
    if len(rows) != 1:
        raise OwnerResolutionError("Expected one existing personal owner account; registration is not changed")
    return int(rows[0][0])


def _django_owner(config: dict) -> int:
    if str(PORTAL) not in sys.path:
        sys.path.insert(0, str(PORTAL))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal.settings")
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()
    from django.contrib.auth import get_user_model
    from django.db import connections

    try:
        model = get_user_model()
        lookup = {model.USERNAME_FIELD: config["owner_username"], "is_active": True}
        ids = list(model._default_manager.filter(**lookup).values_list("pk", flat=True)[:2])
    finally:
        # The collector is long-running; do not keep an idle PostgreSQL
        # connection open after the one-time owner lookup.
        connections.close_all()
    if len(ids) != 1:
        raise OwnerResolutionError("Expected one active personal owner account in the portal database")
    return int(ids[0])


def resolve_owner_id(config: dict) -> int:
    if resolver_name(config) == "django_orm":
        return _django_owner(config)
    return _sqlite_owner(config)
