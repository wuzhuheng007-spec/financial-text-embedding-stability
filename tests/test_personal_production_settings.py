"""Settings import checks only; no database/network connection."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
class PersonalProductionSettingsTests(unittest.TestCase):
    def settings(self, **changes):
        env=dict(os.environ, PYTHONPATH=str(ROOT/'apps/quant_portal'), DEBUG='0',
                 PERSONAL_MODE='1', TRUSTED_REVERSE_PROXY='1', PUBLIC_MARKET_DATA_ENABLED='0',
                 ALLOWED_HOSTS='private.example.test', SECRET_KEY='synthetic-test-only-not-a-deployed-secret',
                 POSTGRES_HOST='not-connected', POSTGRES_DB='test', POSTGRES_USER='test', POSTGRES_PASSWORD='test')
        env.update(changes)
        code="import json; import portal.settings as s; print(json.dumps([s.PERSONAL_MODE,s.DEBUG,s.SESSION_COOKIE_SECURE,s.CSRF_COOKIE_SECURE]))"
        return subprocess.run([sys.executable,'-c',code],env=env,text=True,capture_output=True)
    def test_private_mode_works_without_debug_and_keeps_secure_cookies(self):
        result=self.settings()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[True,False,True,True])
    def test_private_production_mode_requires_proxy(self):
        self.assertNotEqual(self.settings(TRUSTED_REVERSE_PROXY='0').returncode,0)
    def test_private_mode_refuses_public_feed_switch(self):
        self.assertNotEqual(self.settings(PUBLIC_MARKET_DATA_ENABLED='1').returncode,0)
if __name__=='__main__':unittest.main()
