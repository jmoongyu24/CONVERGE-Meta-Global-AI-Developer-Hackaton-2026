"""Turn a team's records into Sources: dated Markdown files (demo data, exports) or Notion pages.

Usage, from backend/:
    uv run python -m app.ingest demo                      # demo scenarios + gold labels
    uv run python -m app.ingest notion <page-id> --team path/to/team.json
    uv run python -m app.ingest push-notion <page-id> [--scenario sim-to-real]
"""

import argparse
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from notion_client import Client

from app import REPO_ROOT, store
from app.models import Gold, Source, SourceAnalysis, Workspace

DEMO_DIR = REPO_ROOT / "data" / "demo"
DATED_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-.+\.md$")
DATE_IN_TEXT = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
CLOCK = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
NUMBERED = re.compile(r"^\d+\. ")
HEADER_LINES = 5  # notes state their date and start time near the top


def note_time(lines: list[str], date: datetime | None = None) -> datetime | None:
    """When a meeting started, from a note's header: `date` (or the header's own date) at the
    header's first clock time."""
    header = "\n".join(lines[:HEADER_LINES])
    if date is None and (match := DATE_IN_TEXT.search(header)):
        date = datetime.fromisoformat(match.group(1))
    if date is not None and (clock := CLOCK.search(header)):
        date = date.replace(hour=int(clock.group(1)), minute=int(clock.group(2)))
    return date


# --- Markdown ------------------------------------------------------------------


def parse_markdown_source(path: Path) -> Source:
    """One `YYYY-MM-DD-<slug>.md` record; the start time is read from its header."""
    match = DATED_NAME.match(path.name)
    if not match:
        raise ValueError(f"{path.name}: expected a YYYY-MM-DD-<slug>.md file name")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = next((line.lstrip("#").strip() for line in lines if line.startswith("# ")), path.stem)
    return Source(
        id=path.stem,
        occurred_at=note_time(lines, datetime.fromisoformat(match.group(1))),
        title=title,
        text=text,
    )


def load_markdown_folder(folder: Path) -> tuple[Workspace, list[Source]]:
    """A folder holding team.json plus one dated Markdown file per record."""
    workspace = Workspace.model_validate_json((folder / "team.json").read_text(encoding="utf-8"))
    sources = [parse_markdown_source(p) for p in folder.iterdir() if DATED_NAME.match(p.name)]
    return workspace, sorted(sources, key=lambda s: s.occurred_at)


def load_gold(folder: Path) -> list[SourceAnalysis]:
    """Hand-labelled analyses of a demo scenario; empty when the folder has none."""
    path = folder / "gold.json"
    if not path.exists():
        return []
    return Gold.model_validate_json(path.read_text(encoding="utf-8")).analyses


def demo_folders() -> list[Path]:
    return sorted(p.parent for p in DEMO_DIR.glob("*/team.json"))


def load_demo(conn: sqlite3.Connection) -> list[str]:
    """Store every demo scenario with its gold analyses; returns one summary line per scenario."""
    lines = []
    for folder in demo_folders():
        workspace, sources = load_markdown_folder(folder)
        lines.append(save(conn, workspace, sources))
        store.save_analyses(conn, workspace.id, "gold", load_gold(folder))
    return lines


def save(conn: sqlite3.Connection, workspace: Workspace, sources: list[Source]) -> str:
    store.save_workspace(conn, workspace)
    store.save_sources(conn, workspace.id, sources)
    if not sources:
        return f"{workspace.id}: no sources"
    first, last = sources[0].occurred_at, sources[-1].occurred_at
    return f"{workspace.id}: {len(sources)} sources ({first:%Y-%m-%d} → {last:%Y-%m-%d})"


# --- Notion --------------------------------------------------------------------

# Block type -> Markdown template. Types not listed here (tables, embeds, ...) carry
# no meeting text and are skipped.
TEXT_BLOCKS = {
    "paragraph": "{}",
    "heading_1": "# {}",
    "heading_2": "## {}",
    "heading_3": "### {}",
    "bulleted_list_item": "- {}",
    "numbered_list_item": "1. {}",
    "toggle": "- {}",
    "quote": "> {}",
    "callout": "> {}",
}
# Markdown line prefix -> block type, for writing notes into Notion. First match wins.
LINE_PREFIXES = [
    ("### ", "heading_3"),
    ("## ", "heading_2"),
    ("# ", "heading_1"),
    ("- [ ] ", "to_do"),
    ("- [x] ", "to_do"),
    ("- ", "bulleted_list_item"),
    ("> ", "quote"),
]
SEPARATE_PAGES = {"child_page", "child_database"}  # imported as their own sources, not inlined


def notion() -> Client:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise SystemExit("NOTION_TOKEN is not set; add it to the .env file at the repo root.")
    return Client(auth=token)


def plain_text(rich_text: list[dict]) -> str:
    # The API returns "plain_text"; blocks built locally only have "text.content".
    return "".join(
        part["plain_text"] if "plain_text" in part else part["text"]["content"]
        for part in rich_text
    )


def blocks_to_markdown(blocks: list[dict], depth: int = 0) -> list[str]:
    """Render Notion blocks as Markdown lines, nested children indented by two spaces."""
    lines: list[str] = []
    indent = "  " * depth
    for block in blocks:
        kind = block["type"]
        body = block.get(kind, {})
        text = plain_text(body.get("rich_text", []))
        if kind in TEXT_BLOCKS:
            lines.append(indent + TEXT_BLOCKS[kind].format(text))
        elif kind == "to_do":
            lines.append(f"{indent}- [{'x' if body.get('checked') else ' '}] {text}")
        elif kind == "code":
            lines += [
                f"{indent}```",
                *(indent + line for line in text.splitlines()),
                f"{indent}```",
            ]
        elif kind == "divider":
            lines.append(f"{indent}---")
        elif kind == "image":
            lines.append(f"{indent}[image]")
        # Read from the API, children are attached to the block by fetch_blocks; built by
        # markdown_to_blocks, they sit inside the block body as the create API expects.
        children = block.get("children") or body.get("children", [])
        lines += blocks_to_markdown(children, depth + 1)
    return lines


def markdown_line_to_block(line: str) -> dict:
    if line == "---":
        return {"type": "divider", "divider": {}}
    kind, content = "paragraph", line
    if match := NUMBERED.match(line):
        kind, content = "numbered_list_item", line[match.end() :]
    else:
        for prefix, prefix_kind in LINE_PREFIXES:
            if line.startswith(prefix):
                kind, content = prefix_kind, line[len(prefix) :]
                break
    body: dict = {"rich_text": [{"type": "text", "text": {"content": content}}]}
    if kind == "to_do":
        body["checked"] = line.startswith("- [x] ")
    return {"type": kind, kind: body}


def markdown_to_blocks(text: str) -> tuple[str, list[dict]]:
    """Split a Markdown note into its title (first `# ` line) and Notion blocks.

    The inverse of blocks_to_markdown for the syntax our notes use: indented lines become
    children of the line above them, and inline Markdown such as **bold** stays literal.
    """
    title = ""
    blocks: list[dict] = []
    open_blocks: list[dict] = []  # the latest block at each indentation depth
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not title and raw.startswith("# "):
            title = line[2:]
            continue
        block = markdown_line_to_block(line)
        depth = min((len(raw) - len(raw.lstrip(" "))) // 2, len(open_blocks))
        if depth:
            parent = open_blocks[depth - 1]
            parent[parent["type"]].setdefault("children", []).append(block)
        else:
            blocks.append(block)
        del open_blocks[depth:]
        open_blocks.append(block)
    return title, blocks


def fetch_blocks(client: Client, block_id: str) -> list[dict]:
    """All child blocks of a page or block, following pagination and nesting."""
    blocks: list[dict] = []
    cursor = None
    while True:
        paging = {"start_cursor": cursor} if cursor else {}
        page = client.blocks.children.list(block_id=block_id, **paging)
        for block in page["results"]:
            if block.get("has_children") and block["type"] not in SEPARATE_PAGES:
                block["children"] = fetch_blocks(client, block["id"])
            blocks.append(block)
        if not page.get("has_more"):
            return blocks
        cursor = page["next_cursor"]


def wall_time(value: str) -> datetime:
    """A Notion timestamp as the wall-clock time written, so all source times compare."""
    return datetime.fromisoformat(value).replace(tzinfo=None)


def page_time(page: dict, title: str, body: list[str]) -> datetime:
    """Meeting start: the page's Date property, else the date and time near the top, else
    the page's creation time."""
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "date" and prop.get("date"):
            return wall_time(prop["date"]["start"])
    return note_time([title, *body]) or wall_time(page["created_time"])


def import_notion(parent_page_id: str) -> list[Source]:
    """Each child page of the given page becomes one Source. Databases are not read yet."""
    client = notion()
    sources = []
    for block in fetch_blocks(client, parent_page_id):
        if block["type"] != "child_page":
            continue
        title = block["child_page"]["title"]
        page = client.pages.retrieve(page_id=block["id"])
        body = blocks_to_markdown(fetch_blocks(client, block["id"]))
        sources.append(
            Source(
                id=block["id"],
                occurred_at=page_time(page, title, body),
                title=title,
                text="\n".join([f"# {title}", "", *body]),
                url=page.get("url"),
            )
        )
    return sorted(sources, key=lambda s: s.occurred_at)


def title_property(title: str) -> dict:
    return {"title": {"title": [{"type": "text", "text": {"content": title}}]}}


def push_to_notion(parent_page_id: str, title: str, sources: list[Source]) -> str:
    """Create page `title` under the parent with one child page per source; returns its id.

    Puts the synthetic demo scenarios into Notion for demos and end-to-end import tests.
    """
    client = notion()
    folder = client.pages.create(
        parent={"page_id": parent_page_id}, properties=title_property(title)
    )
    for source in sources:
        page_title, blocks = markdown_to_blocks(source.text)
        client.pages.create(
            parent={"page_id": folder["id"]}, properties=title_property(page_title), children=blocks
        )
    return folder["id"]


# --- CLI -----------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Load team records into data/converge.db.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="store the demo scenarios with their gold labels")
    pull = commands.add_parser("notion", help="import the child pages of one Notion page")
    pull.add_argument("page_id")
    pull.add_argument("--team", type=Path, required=True, help="team.json: workspace and members")
    push = commands.add_parser("push-notion", help="copy demo scenarios under a Notion page")
    push.add_argument("page_id")
    push.add_argument("--scenario", action="append", help="scenario folder name (default: all)")
    args = parser.parse_args()

    if args.command == "push-notion":
        for folder in demo_folders():
            if args.scenario and folder.name not in args.scenario:
                continue
            workspace, sources = load_markdown_folder(folder)
            page_id = push_to_notion(args.page_id, workspace.name, sources)
            print(f"{workspace.id}: {len(sources)} pages under Notion page {page_id}")
        return

    with store.connect() as conn:
        if args.command == "demo":
            print("\n".join(load_demo(conn)))
        else:
            workspace = Workspace.model_validate_json(args.team.read_text(encoding="utf-8"))
            print(save(conn, workspace, import_notion(args.page_id)))


if __name__ == "__main__":
    main()
