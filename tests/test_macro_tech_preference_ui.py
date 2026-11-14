"""Contract checks for the private macro event preference page."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.build_macro_tech_preference_ui import build, validate_catalog


def synthetic_event(event_id: str = "T01") -> dict:
    return {
        "id": event_id,
        "category": "MACRO_RELEASE",
        "date": "2026-11-10",
        "title_zh": "测试 </script><script>evil()</script>",
        "title_en": "Synthetic test",
        "facts_zh": ["测试事实一", "测试事实二"],
        "facts_en": ["Synthetic fact one", "Synthetic fact two"],
        "unknowns_zh": ["公布前共识未知"],
        "tech_link_zh": "仅为测试假设",
        "tech_link_en": "Synthetic hypothesis only",
        "source_urls": ["https://www.bls.gov/news.release/archives/cpi_11102026.htm"],
    }


class MacroPreferenceUiTest(unittest.TestCase):
    def test_catalog_guards(self) -> None:
        event = synthetic_event()
        base = {"schema": "macro_tech_candidate_events_v1", "events": [event]}
        self.assertEqual(len(validate_catalog(base)), 1)
        with self.assertRaises(ValueError):
            validate_catalog({"schema": base["schema"], "events": [event, event]})
        with self.assertRaises(ValueError):
            validate_catalog({"schema": base["schema"], "events": [{**event, "source_urls": ["http://example.org"]}]})
        with self.assertRaises(ValueError):
            validate_catalog({"schema": base["schema"], "events": [{**event, "unknowns_zh": []}]})

    @unittest.skipUnless(shutil.which("node"), "Node is needed for JavaScript contract checks")
    def test_build_and_choice_contract(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            catalog, page, receipt = (root / part for part in ("catalog.json", "index.html", "receipt.json"))
            catalog.write_text(json.dumps({"schema": "macro_tech_candidate_events_v1",
                                           "events": [synthetic_event()]}, ensure_ascii=False), encoding="utf-8")
            result = build(catalog, page, receipt)
            html = page.read_text(encoding="utf-8")
            self.assertEqual(result["candidate_catalog_sha256"], hashlib.sha256(catalog.read_bytes()).hexdigest())
            self.assertEqual(result["candidate_count"], 1)
            self.assertEqual(result["questions_per_card"], 3)
            self.assertEqual(result["initial_saved_preferences"], 0)
            self.assertEqual(result["free_text_inputs"], 0)
            self.assertEqual(html.count("</script>"), 1)
            self.assertNotIn("<script>evil()", html)
            self.assertNotIn("<textarea", html)
            self.assertNotIn('type="text"', html)
            with self.assertRaises(FileExistsError):
                build(catalog, page, receipt)

            script = re.search(r"<script>\n(.*?)\n</script>", html, re.DOTALL)
            self.assertIsNotNone(script)
            source = root / "page.js"
            source.write_text(script.group(1), encoding="utf-8")
            subprocess.run(["node", "--check", str(source)], check=True, capture_output=True, text=True)
            smoke = root / "smoke.js"
            smoke.write_text(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync(process.argv[2],'utf8').replace(/init\(\);\s*$/,'');
const ctx={Date,URL,Object,Set,JSON}; vm.createContext(ctx); vm.runInContext(src,ctx);
let empty=vm.runInContext('payload()',ctx);
assert.strictEqual(empty.rows.length,1);
assert.strictEqual(Object.keys(empty.rows[0].choices).length,0);
vm.runInContext('validatePayload(payload())',ctx);
let changed=JSON.parse(JSON.stringify(empty)); changed.catalog_sha256='wrong';
assert.throws(()=>vm.runInContext('validatePayload('+JSON.stringify(changed)+')',ctx));
changed=JSON.parse(JSON.stringify(empty)); changed.rows[0].choices.interest='INVALID';
assert.throws(()=>vm.runInContext('validatePayload('+JSON.stringify(changed)+')',ctx));
changed=JSON.parse(JSON.stringify(empty)); changed.rows[0].choices.interest='NO';
const checked=vm.runInContext('validatePayload('+JSON.stringify(changed)+')',ctx);
assert.strictEqual(checked.T01.interest,'NO');
assert.strictEqual(empty.rows[0].choices.interest,undefined);
''', encoding="utf-8")
            subprocess.run(["node", str(smoke), str(source)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
