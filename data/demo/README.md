# Demo dataset (synthetic)

Three fictional research teams. Every person, meeting, and result is invented; real papers and tools appear only as background references.

| Scenario | Team | Records | Period | Competing directions |
|---|---|---|---|---|
| `self-organizing-agents` | Prof. Han, Mina (PhD), Leo (PhD), Jae (MS) | 11 | 2026-08 → 2026-10 | learned → bandit topology (Mina) vs. in-context role negotiation (Leo, backed by Jae) |
| `long-term-dependency` | Prof. Ruiz, Daniel (PhD), Sora (PhD), Amir (MS) | 10 | 2014-03 → 2017-07 | gated recurrence, RNN → LSTM → GRU (Daniel) vs. attention → Transformer (Sora); Amir switches sides as results come in |
| `sim-to-real` | Prof. Okafor, Hana (PhD), Marco (PhD), Yuki (MS) | 7 | 2026-03 → 2026-05 | domain randomization (Hana) vs. system identification (Marco, backed by Yuki) |

In every scenario two PhD students compete to lead the main paper, the MS student backs one of them with experiments, and the PI pushes for a single direction. Each scenario mixes points the whole team agrees on with open disagreements, and ends without a decision, so the Converge Room has a real conflict to resolve.

## Files per scenario

- `team.json`: workspace id, name, and members with the aliases the notes use for them (`"Prof"`, `"D"`, ...)
- `YYYY-MM-DD-<slug>.md`: one meeting record, as exported from a notes app. Note-takers rotate, so formatting and speaker labels vary, and "I" means the note-taker.
- `gold.json`: hand-labelled answer key in the shape the analysis pipeline outputs (`SourceAnalysis` in `backend/app/models.py`):
  - `ops`: idea registry changes (`new`, `update`, `branch`, `merge`, `park`, `drop`)
  - `mentions`: who took which stance (`propose`, `support`, `refine`, `question`, `challenge`, `inform`) on which idea
  - `items`: `question`, `decision`, `action`, `consensus`

Every label carries a verbatim quote; `backend/tests/test_pipeline.py` checks that each quote occurs exactly once in its record and that every idea and member reference resolves in time order.
