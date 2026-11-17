"""One explicitly synthetic FinBERT/LM plumbing check, excluded from research counts."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.news_sentiment_factor_v4 import score, store
from scripts.news_sentiment_factor_v4.preflight import PUBLIC as REAL_PUBLIC


def main():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        db = root / "synthetic.sqlite3"
        sample = root / "synthetic.json"
        sample.write_text(json.dumps({"ret_code": 0, "data": {"items": [
            {"news_id": "SYNTHETIC-ONLY", "title": "Company announces lower revenue guidance",
             "publish_time": 1762200000000, "source": "Synthetic publisher"}
        ]}}), encoding="utf-8")
        (root / "preflight_redacted.json").write_bytes((REAL_PUBLIC / "preflight_redacted.json").read_bytes())
        with patch.object(score, "PUBLIC", root), patch.object(store, "PUBLIC", root):
            store.ingest_moomoo_probe(sample, mode="BASELINE", db=db)
            result = score.score_pending(limit=1, db=db)
        assert result["actual_finbert_inference_titles"] == 1
        assert result["actual_lm_scores"] == 1
        print(json.dumps({"schema": "news_v4_synthetic_model_plumbing_only",
                          "synthetic_titles": 1, "finbert_inference": 1, "lm_scores": 1,
                          "real_news_scores": 0, "status": "PASS"}))


if __name__ == "__main__":
    main()
