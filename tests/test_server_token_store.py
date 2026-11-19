"""Server token store and owner resolver checks with synthetic values only.

No network, no real credential, no Windows DPAPI blob is read.
"""
import base64
import json
import os
import secrets
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.market_observer_v1 import moomoo_webapi_oauth as oauth
from scripts.quant_personal_feed import owner

SYNTHETIC = {"access_token": "synthetic-access-not-a-credential", "token_type": "Bearer",
             "scope": "quote:read accid:000", "refresh_token": "synthetic-refresh",
             "client_id": "synthetic-client", "access_expires_epoch": time.time() + 7200}


class ServerTokenStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.key = self.dir / "key"
        self.key.write_text(base64.b64encode(secrets.token_bytes(32)).decode())
        os.chmod(self.key, 0o600)
        self.path = self.dir / "token.sealed"

    def tearDown(self):
        self.tmp.cleanup()

    def store(self):
        return oauth.ServerFileTokenStore(self.path, self.key)

    def test_round_trip_is_sealed_and_scope_checked(self):
        self.store().save(SYNTHETIC)
        raw = self.path.read_bytes()
        self.assertTrue(raw.startswith(b"QTS1"))
        self.assertNotIn(b"synthetic-access", raw)
        self.assertEqual(self.store().access_token(), SYNTHETIC["access_token"])
        status = self.store().status()
        self.assertEqual(status["credential_state"], "QUOTE_READ_AUTHORIZED")
        self.assertEqual(status["storage"], "SERVER_AES256GCM_KEYFILE")
        self.assertNotIn("synthetic-access", json.dumps(status))
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_wrong_key_and_dpapi_blob_are_refused(self):
        self.store().save(SYNTHETIC)
        other = self.dir / "other"
        other.write_text(base64.b64encode(secrets.token_bytes(32)).decode())
        os.chmod(other, 0o600)
        with self.assertRaises(oauth.OAuthError):
            oauth.ServerFileTokenStore(self.path, other).load()
        self.path.write_bytes(b"\x01\x00\x00\x00\xd0\x8c\x9d\xdf" + secrets.token_bytes(200))
        with self.assertRaises(oauth.OAuthError):
            self.store().load()
        self.assertEqual(self.store().status()["credential_state"], "UNREADABLE_OR_SCOPE_REJECTED")

    def test_missing_key_or_token_is_not_installed(self):
        self.assertEqual(self.store().status()["credential_state"], "NOT_INSTALLED")
        self.key.unlink()
        with self.assertRaises(oauth.OAuthError):
            self.store().save(SYNTHETIC)

    def test_trade_scope_is_rejected(self):
        with self.assertRaises(oauth.OAuthError):
            self.store().save({**SYNTHETIC, "scope": "quote:read trade:write"})

    @unittest.skipIf(os.name == "nt", "POSIX permission check")
    def test_world_readable_key_is_refused(self):
        os.chmod(self.key, 0o644)
        with self.assertRaises(oauth.OAuthError):
            self.store().save(SYNTHETIC)

    def test_factory_selects_server_store_from_environment(self):
        env = {"MOOMOO_TOKEN_STORE": "server", "MOOMOO_TOKEN_FILE": str(self.path),
               "MOOMOO_TOKEN_KEY_FILE": str(self.key)}
        with patch.dict(os.environ, env):
            self.assertIsInstance(oauth.token_store(self.dir / "x.dpapi"), oauth.ServerFileTokenStore)
        with patch.dict(os.environ, {"MOOMOO_TOKEN_STORE": "server"}, clear=False):
            os.environ.pop("MOOMOO_TOKEN_FILE", None)
            with self.assertRaises(oauth.OAuthError):
                oauth.token_store(self.dir / "x.dpapi")
        with patch.dict(os.environ, {"MOOMOO_TOKEN_STORE": "dpapi"}):
            store = oauth.token_store(self.dir / "x.dpapi")
            self.assertIs(type(store), oauth.LocalTokenStore)


class OwnerResolverTests(unittest.TestCase):
    def test_sqlite_resolver_and_unknown_resolver(self):
        with tempfile.TemporaryDirectory(dir=owner.ROOT) as tmp:
            db = Path(tmp) / "portal.sqlite3"
            with closing(sqlite3.connect(db)) as con, con:
                con.execute("CREATE TABLE auth_user(id INTEGER PRIMARY KEY, username TEXT)")
                con.execute("INSERT INTO auth_user VALUES(7,'synthetic@local.invalid')")
            config = {"portal_db": str(db.relative_to(owner.ROOT)), "owner_username": "synthetic@local.invalid"}
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("QUANT_OWNER_RESOLVER", None)
                self.assertEqual(owner.resolve_owner_id(config), 7)
                with self.assertRaises(owner.OwnerResolutionError):
                    owner.resolve_owner_id({**config, "owner_username": "missing@local.invalid"})
            with patch.dict(os.environ, {"QUANT_OWNER_RESOLVER": "raw_postgres"}):
                with self.assertRaises(owner.OwnerResolutionError):
                    owner.resolve_owner_id(config)


if __name__ == "__main__":
    unittest.main()
