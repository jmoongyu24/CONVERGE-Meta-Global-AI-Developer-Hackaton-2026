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
- `YYYY-MM-DD-<slug>.md`: one meeting record, as exported from a notes app. Note-takers rotate, so formatting and speaker labels vary, and "I" means the note-taker. Some records hold what Notion pages often do: toggles (`▸ title` with the content indented under it), tables (`| a | b |`), images (`![caption](assets/<file>)`), and a sub-page (`📄 title` with its content indented).
- `assets/`: the images the records embed: whiteboard sketches and a replication figure, drawn for this dataset.
- `gold.json`: hand-labelled answer key in the shape the analysis pipeline outputs (`SourceAnalysis` in `backend/app/models.py`):
  - `ops`: idea registry changes (`new`, `update`, `branch`, `merge`, `park`, `drop`)
  - `mentions`: who took which stance (`propose`, `support`, `refine`, `question`, `challenge`, `inform`) on which idea
  - `items`: `question`, `decision`, `action`, `consensus`
  - `modules`: architecture changes (`add`, `update`, `remove`): a module's title, note, owner (the member who builds it), status (`proposed` or `adopted`), inputs, enclosing block, and the idea it implements. Some are labelled from a table row or an image caption, the way a model reading the page with its images would find them.

Every label carries a verbatim quote; `backend/tests/test_pipeline.py` checks that each quote occurs exactly once in its record and that every idea, module, and member reference resolves in time order.

## Architectures in the scenarios

| Scenario | How the architecture evolves |
|---|---|
| `self-organizing-agents` | A fixed-role agent runtime gains Mina's RL topology policy (later replaced by her topology bandit, fed back by the evaluation suite), Leo's role negotiation, link negotiation, and Jae's hierarchical negotiation; Prof. Han adds the communication budget. |
| `long-term-dependency` | Embeddings → RNN encoder → answer layer becomes an LSTM, then a GRU encoder with Sora's soft attention; a conv encoder comes and goes; Sora's self-attention encoder grows into a Transformer block (multi-head self-attention, feed-forward) competing with Daniel's recurrent segment memory. |
| `sim-to-real` | Simulator → policy training → hardware evaluation, closed into a loop through force traces, system identification, the residual dynamics model, curriculum randomization, and Prof. Okafor's calibrated randomization band. |
