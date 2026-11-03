# CONVERGE

CONVERGE sits beside your team's Notion meeting notes and answers four questions at a glance:
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

## Development

| Task | Command |
|---|---|
| API server with reload | `cd backend && uv run uvicorn app.main:app --reload` |
| Web app with hot reload | `cd frontend && npm run dev` |
| Tests | `cd backend && uv run pytest` |
| Analyze a team from the terminal | `cd backend && uv run python -m app.ingest analyze <workspace-id>` |

The demo teams in `data/demo/` are synthetic: every person, meeting, and result is invented.
