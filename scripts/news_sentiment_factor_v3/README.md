# News sentiment factor v3: run order

This is a training-period, official-earnings language **development** workflow. The immutable v3 outputs already exist in the local ignored `artifacts/news_sentiment_factor_v3_20261117` directory. Do not rerun a completed stage onto those files. The public receipts are under `results/news_sentiment_factor_v3_20261117`; the protocol is `configs/news_sentiment_factor_v3_20261117.json`.

1. `source.py` selects SEC ticker/submissions metadata within the frozen request budget.
2. `archive.py` reads accession index and complete submission; `resolve.py` retains and resolves the eight initial exhibit ambiguities; `review_source.py` excludes two Tesla delivery reports.
3. `text.py` freezes source-located narrative chunks. Only the final 1,728-block version was scored; earlier selector candidates remain private and their redacted receipts remain public. `audit_text.py` checks exact normalized DOM-node spans.
4. In the isolated local research environment, `score.py preflight`, then `score.py score`, calculate the fixed LM and FinBERT output. `factors.py` aggregates by equal block weight inside present sections and equal section weight per event, then computes F2 from two prior issuer events.
5. `price.py` makes at most 30 read-only moomoo calls for ten new securities, using the existing owner-only encrypted quote credential; NVDA/MU raw bars/actions are reused. `align.py preflight`, then `align.py align`, construct the retrospective SEC-acceptance date-proxy labels. The old two price files extend beyond March 2026, so the parser consumes only their first 251 training-window bar objects.
6. `evaluate.py` executes pooled, paired Ridge fits only when precommitted gates pass. `calendar_compare.py` explains half-session changes relative to the eight old events. The private per-event scores, prices, labels and predictions must stay local.

The count feature is constant one, so B=A and D=C. F2 does not meet the fit gate. No April–July 2026 label, Qwen generation, live portal update or trading operation is part of this workflow. SEC acceptance is not proof of first public availability. Numerical results in the public report are development summaries, not a generalizable return model.
