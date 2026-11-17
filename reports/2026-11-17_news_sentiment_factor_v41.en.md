# Chinese news tone v4.1: real local scoring and prospective records

Executed on 17 November 2026 (Asia/Shanghai) as a **separate Chinese research branch**. The verified starting commit was `78343fd60d4b709b6e819748fd5925b6b9612d70`. English v4, previous financial experiments, the owner account and feed, and financial input v2.5 `REJECTED_CANDIDATE` were preserved.

## Plan versus execution

| Phase | Planned | Executed |
| --- | --- | --- |
| P0 | Protect stores and freeze a protocol | Verified local and remote baseline; backed up five private SQLite stores and ran `quick_check`. An HTML segmentation preflight failure was retained, then corrected in a separate pre-inference rule revision. |
| P1 | Select existing authorized Chinese versions without new provider calls | Froze 30 existing moomoo v4 versions and the first 200 eligible Jin10 versions ordered by local receipt time. One exact normalized-text duplicate was excluded before the cap. These are replay material, never prospective records. |
| P2 | Pin one Chinese classifier and smoke-test it | Pinned `yiyanghkust/finbert-tone-chinese` revision `e91b1a3af10e1e8c9c03429d3cd7d5e9a1c8000d`; safetensors SHA-256 `0c25b105902f90d1e039d511944af4f49d1490f6698e701f2704a714ca74e66b`. Local CPU inference, no remote code. The published card and config both map 0 neutral, 1 positive, 2 negative. Five synthetic cases, empty input, complete long-text splitting and batch consistency were checked. |
| P3 | Separate title/body and link only explicit company mentions | Produced 31 title and 1,070 body units. Markup in 15 moomoo bodies was removed with a raw-source character map. Association states: 894 no explicit company, 168 one explicit company, 39 unresolved multi-company units. |
| P4 | Freeze source review before scores and run genuine inference | Twelve same-team source judgments were frozen before opening model outputs. **1,101 real units** were scored in **138 batches**, with **0 failed units**. Of eight three-class-comparable source judgments, seven labels matched and one differed; four were mixed or unresolved. |
| P5 | Observe new versions and configure a separate worker | A controlled stop and a completed five-minute bounded observation were recorded. That completed window saw **0 new versions**. The later continuous worker received and scored **1 new Jin10 version**, received at 07:15:01 UTC and scored at 07:15:23 UTC. It has no explicit company link and remains an unreviewed text-tone candidate. The worker checks local cache every 30 seconds with a daily cap of 500 new versions and sends no provider requests. |
| P6 | Build descriptive N1/N2 and freeze later return work | The observed cache yielded 70 algorithmic company-event groups and 16 company-by-China-receipt-day rows for 13 companies across two receipt dates. This is an incomplete-cache development proxy, not a no-news panel. No return label, model fit or IC was computed. |
| P7 | Show owner-only research state and hand off | The signed-in local panel separates Chinese v4.1 from English v4 and displays source, score time, text tone and company-alias clue. English and Chinese were viewed at desktop and 390px. The 390px document width was 375px, with no page-wide horizontal overflow. A WRDS access inquiry was drafted but not sent. |

## Interpretation and review limits

The fixed score is `P(positive) - P(negative)` from the three raw softmax outputs. These **uncalibrated text-classification outputs** are neither equity-return probabilities nor verification of a report. Title and body scores remain separate; long original text is split without silent truncation. Each scored unit records a source span, input/config hash, pinned model revision and actual score time. The [run lock](../results/news_sentiment_factor_v41_20261117/run_lock_real_replay_redacted.json) preserves the code hashes used for the completed replay; a later SQLite connection-close repair did not rewrite them.

The 12-item `CODEX_REVIEW` is a same-team development check, not independent ground truth. The confusion matrix, with source-reference rows and model columns ordered neutral/positive/negative, is neutral `2/0/0`, positive `1/3/0`, negative `0/0/2`. The disagreement concerns a future-facility company claim judged positive by the reviewer but neutral by the classifier. The four mixed/unresolved judgments do not enter that matrix. Association review found seven direct matches, two unresolved multi-company items, one partial entity recall, one entity outside the frozen watchlist and one item without a company. Two reviewed units were price recaps. Company-specific sentiment remains a **candidate**, and no score is promoted to a verified financial fact.

The [publisher's model card](https://huggingface.co/yiyanghkust/finbert-tone-chinese) lists Apache-2.0 and training on private analyst-report sentences; it does not disclose a clear training-data cutoff. We retained that limitation. Jin10 pagination still has a possible continuity gap; the observed cache is not a complete news archive. Original publication, first local receipt and score completion are separate fields. Replay inference performed today cannot be backdated into historical signal availability.

The first continuous worker later exited without a final receipt while its old state file still said `RUNNING`; the exact cause is unestablished. Status and the owner panel now check the actual process and report `STALE_EXIT`. The earlier run was retained and a separate worker was restarted. Final continuity must be judged from the live process check at handoff, not the old heartbeat.

The subsequent new version has genuine local receipt and scoring timestamps and is visible as a new receipt on the owner page. Its publication timestamp preceded local receipt by roughly ten minutes; publication is not used as signal availability. Its segment has no explicit company alias, so **qualified company-level prospective N1/N2 inputs remain zero**. It has not received source-semantic review or independent news verification. The redacted receipt contains timings and counts only; source text, unit score and article identity stay in private SQLite.

## Frozen next-stage protocol and operations

`N1` is the equal-weight mean of text tone across explicitly company-linked algorithmic event groups; `N2` counts those groups. Algorithmic grouping is not independent corroboration. The present 16 rows use an Asia/Shanghai **receipt civil date**, not an exchange trading date. Entity recall, price-recap filtering and source completeness require further review. The separately versioned next-stage protocol fixes a 16:15 America/New_York post-close snapshot, next eligible regular-session open as entry, the same session close as the one-day exit and the fifth eligible session close as the five-day exit. Calendar, price source, corporate actions and complete news coverage must pass gates before any comparison. A/B/C/D are lagged price-volume, plus attention, plus tone, and plus both, on a shared chronological sample. This run performed **0 return fits, 0 IC calculations and 0 trades**.

Two isolated synthetic rule tests and four isolated Django tests passed; they do not establish semantic accuracy. Browser review covered the added research panel rather than the full portal. Private news text, per-unit scores, databases and weights remain local. English news access and WRDS `ravenpack_dj` remain an independent pending item.

Owner page: <http://127.0.0.1:8765/>. From the repository root:

```powershell
& artifacts/news_sentiment_factor_v3_20261117/.venv/Scripts/python.exe -m scripts.news_sentiment_factor_v41.worker status
& artifacts/news_sentiment_factor_v3_20261117/.venv/Scripts/python.exe -m scripts.news_sentiment_factor_v41.worker stop
& artifacts/news_sentiment_factor_v3_20261117/.venv/Scripts/python.exe -m scripts.news_sentiment_factor_v41.worker start --continuous
```

Stopping this research worker does not stop the existing portal or provider collectors. The old automatic trusted-interpretation worker remains paused.
