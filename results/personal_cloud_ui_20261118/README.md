# Validation receipt

Base: `64f16d3c5acf44960903785ea2f4347ddd1f879b`. Synthetic fixtures only. No production Windows database or Alibaba Cloud host was accessible.

- `python-tests.log`: 14 checks, including existing cache tests and new lock recovery / production-settings checks.
- `django-tests.log`: 16 interface and owner-isolation checks. This supersedes the earlier overlapping 11-check run; do not add them together.
- `dom_results.json`: Chinese and English synthetic navigation scenarios. This is DOM behavior, not visual acceptance.
- Chromium visual attempt failed at launch: host denied socket creation (`Operation not permitted`). Desktop and 390px visual acceptance remain NOT_RUN.
- Actual provider reconnection, new analysis quality, supplier authorization and cloud deployment remain NOT_RUN.

Reproduction from repository root (an isolated checkout, never the owner's active database):

```sh
python -m unittest tests.test_personal_feed_lock_recovery tests.test_quant_personal_feed tests.test_personal_production_settings -v
SECRET_KEY=isolated-test-key-only DEBUG=1 DJANGO_TESTING=1 ALLOWED_HOSTS=localhost,127.0.0.1,testserver PYTHONPATH=. python apps/quant_portal/manage.py test core.tests.PublicCacheTests core.test_personal_feed core.test_personal_timeline core.test_research_sentiment_v41 --noinput
npm install --prefix artifacts/cloud_ui_20261118 jsdom@31.0.1 --no-audit --no-fund
python tests/browser/personal_workspace_fixture.py artifacts/cloud_ui_20261118
NODE_PATH="$PWD/artifacts/cloud_ui_20261118/node_modules" node tests/browser/personal_workspace_dom.cjs artifacts/cloud_ui_20261118
```

The fixture writes only its own SQLite database and rendered test HTML in the supplied artifacts directory. Source hashes and runtime versions are in `validation.json`.
