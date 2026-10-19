"""Write explicitly synthetic boundary evidence for parser inspection only."""
from pathlib import Path

from finembed.extract import primary_text, extract_item1, comparison
from finembed.run import sha256, write_json


root = Path(__file__).resolve().parents[1]
out = root / "artifacts" / "synthetic_fixture_preview"
out.mkdir(parents=True, exist_ok=True)
intro = "<p>TABLE OF CONTENTS</p><p>Item 1. Business</p><p>Item 1A. Risk Factors</p>"
business = "<h2>Item 1. Business</h2><p>" + "Synthetic business description. " * 120 + "</p>"
risk = "<h2>Item 1A. Risk Factors</h2><p>" + "Synthetic risk discussion. " * 100 + "</p>"
raw = ("<SEC-DOCUMENT>SYNTHETIC FIXTURE; NOT A SEC FILING\n<DOCUMENT><TYPE>10-K\n<TEXT>"
       "<html><body><p>Cover " + "synthetic cover " * 200 + "</p>" + intro + business + risk +
       "</body></html></TEXT></DOCUMENT></SEC-DOCUMENT>").encode("utf-8")
(out / "synthetic_original_submission.txt").write_bytes(raw)
text, _ = primary_text(raw)
item, offsets = extract_item1(text)
(out / "synthetic_primary_text.txt").write_text(text, encoding="utf-8", newline="\n")
(out / "synthetic_item1_business.txt").write_text(item, encoding="utf-8", newline="\n")
write_json(out / "synthetic_boundary_comparison.json", {
    "fixture_type": "SYNTHETIC; NO SEC DOWNLOAD OR REAL FILING CLAIM",
    "raw_sha256": sha256(raw),
    "primary_text_sha256": sha256(text.encode("utf-8")),
    "item1_sha256": sha256(item.encode("utf-8")),
    "offsets": offsets,
    **comparison(text, item, offsets),
})
print(out)
