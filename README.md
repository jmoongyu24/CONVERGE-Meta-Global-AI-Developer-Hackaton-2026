# Converge

Converge sits beside your team's Notion meeting notes and answers four questions at a glance:
where the team stands, which ideas are in play, which ones compete, and what you should do next.
It draws the team's work as one picture, each module split into the colors of the people working
on it, and every sentence links to the line of the notes it comes from.

Built for the Meta Global AI Developer Hackathon 2026 on Muse Spark (Meta Model API).

## Quick start

```bash
git clone <this repository> converge && cd converge && ./start.sh
```

`start.sh` installs what is missing (it needs Node.js 20 or newer), asks once for your
[Meta Model API key](https://dev.meta.ai/api-keys) and
[Notion integration token](https://www.notion.so/my-integrations) (saved only to `.env` in this
folder), builds the web app, loads three demo teams, and serves Converge at
http://localhost:8000.

### Beside Notion

1. In Chrome, open `chrome://extensions`, turn on Developer mode, click **Load unpacked**, and
   pick the `extension/` folder.
2. In Notion, open the page that holds your meeting notes (sub-pages or a database of notes) and
   add your integration under **••• → Connections**.
3. Click the Converge icon: the side panel offers to connect the page. After that, **Update**
   reads the newest notes and refreshes the board.

## What happens on Update

1. The page is imported with everything inside it: sub-pages, database rows, toggles, tables,
   and images (downloaded before Notion's links expire).
2. Muse Spark reads the notes in one pass, images included, and returns their history as
   structured data: ideas and how they branch and merge, each member's stance, questions,
   decisions, next steps, and the modules of the work with who builds or proposes what.
3. Every quote is checked against the notes. Anything the notes do not say word for word is
   dropped ("no quote, no claim").
4. A second call writes the briefing: the problem, where things stand, each member's idea and
   next steps, and the open contests.

An update of a team with ten meetings costs about $0.08; later updates read only new or changed
notes.

## Converge Room

Every open contest on the board has a **Converge on this** button. Each member involved gets a
delegate that speaks only from what that member said in the notes (its quotes are checked, and
a delegate without a matching quote is marked unsupported). The delegates state positions and
answer each other; a facilitator then proposes two ways to settle the question, each ending in
who does what next and each member's likely acceptance. Adopting one writes the decision and
the next steps to the session note and to Notion, and the board follows.

Other edits made in Converge (assigning a module, asking a question, proposing a module) are
written back to the Notion page the same way, as lines of a session note.

## Evaluation

The three demo teams (28 meetings) are hand-labelled with the same structure the model produces:
ideas and how they branch and merge, each member's stance, decisions, actions, and questions,
every label tied to a verbatim quote. `cd backend && uv run python -m app.evaluate` compares
Muse Spark's analyses with those labels. The model names ideas in its own words, so its ideas are
first matched to the labelled ones through the statements (meeting, line, speaker) they share.

| Metric | long-term-dependency | self-organizing-agents | sim-to-real | Mean |
|---|---|---|---|---|
| Ideas found (gold → model) | 9 → 9 | 7 → 8 | 6 → 6 | – |
| Idea grouping, B³ F1 | 0.90 | 0.85 | 0.94 | 0.89 |
| Lineage edges (branch/merge), F1 | 0.75 | 0.91 | 1.00 | 0.89 |
| Parked/dropped ideas caught | 0.67 | 1.00 | 1.00 | 0.89 |
| Final idea status, accuracy | 0.62 | 1.00 | 1.00 | 0.88 |
| Gold stances the model also found | 0.78 | 0.71 | 0.82 | 0.77 |
| Stance, exact | 0.89 | 0.78 | 0.78 | 0.82 |
| Stance side (for / against / neutral) | 0.91 | 0.86 | 0.83 | 0.87 |
| Speaker attribution | 0.98 | 1.00 | 1.00 | 0.99 |
| Decisions, F1 | 0.62 | 0.77 | 0.44 | 0.61 |
| Action items, F1 | 0.94 | 0.98 | 0.82 | 0.91 |
| Open questions, F1 | 0.86 | 0.80 | 0.89 | 0.85 |
| Quotes copied verbatim | 1.00 | 0.99 | 1.00 | 1.00 |
| Claims kept after the quote check | 1.00 | 0.97 | 1.00 | 0.99 |
| Converge Room turns backed by a quote | 1.00 | 1.00 | 1.00 | 1.00 |
| Cost per meeting (USD) | $0.0106 | $0.0109 | $0.0159 | – |
| Model time per meeting (s) | 19.0 | 18.9 | 30.4 | – |

One prompt revision, guided by the errors of the first run, moved the main numbers (mean of the
three teams):

| Metric | First prompt | Revised prompt |
|---|---|---|
| Idea grouping, B³ F1 | 0.83 | 0.89 |
| Lineage edges, F1 | 0.77 | 0.89 |
| Stance, exact | 0.66 | 0.82 |
| Stance side | 0.81 | 0.87 |
| Decisions, F1 | 0.58 | 0.61 |
| Final idea status | 0.96 | 0.88 |

The first run reported results as support instead of plain information, treated successors
such as GRU as new ideas rather than branches of LSTM, and missed calls made by the PI as
decisions; the revised prompt defines each of those. Final status got worse in one team because
later discussion attached to "GRU" instead of "GRU + attention", which changes which of the two
counts as dormant. The set is small and the revision was tuned on it, so read the numbers as a
sanity check rather than a benchmark.

## Development

| Task | Command |
|---|---|
| API server with reload | `cd backend && uv run uvicorn app.main:app --reload` |
| Web app with hot reload | `cd frontend && npm run dev` |
| Tests | `cd backend && uv run pytest` |
| Analyze a team from the terminal | `cd backend && uv run python -m app.ingest analyze <workspace-id>` |

The demo teams in `data/demo/` are synthetic: every person, meeting, and result is invented.
