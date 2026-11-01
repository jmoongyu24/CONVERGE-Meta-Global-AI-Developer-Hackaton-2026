"""Turn a team's records into Sources: dated Markdown files (demo data, exports) or Notion pages.

Usage, from backend/:
    uv run python -m app.ingest demo
    uv run python -m app.ingest notion <parent-page-id> --team path/to/team.json
"""

import argparse
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from notion_client import Client

from app import REPO_ROOT, store
from app.models import Source, Workspace

DEMO_DIR = REPO_ROOT / "data" / "demo"
DATED_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-.+\.md$")
DATE_IN_TEXT = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
CLOCK = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
HEADER_LINES = 5  # notes state their start time near the top


# --- Markdown ------------------------------------------------------------------


def parse_markdown_source(path: Path) -> Source:
    """One `YYYY-MM-DD-<slug>.md` record; the start time is read from its header."""
    match = DATED_NAME.match(path.name)
    if not match:
        raise ValueError(f"{path.name}: expected a YYYY-MM-DD-<slug>.md file name")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    occurred_at = datetime.fromisoformat(match.group(1))
    if clock := CLOCK.search("\n".join(lines[:HEADER_LINES])):
        occurred_at = occurred_at.replace(hour=int(clock.group(1)), minute=int(clock.group(2)))
    title = next((line.lstrip("#").strip() for line in lines if line.startswith("# ")), path.stem)
    return Source(id=path.stem, occurred_at=occurred_at, title=title, text=text)


def load_markdown_folder(folder: Path) -> tuple[Workspace, list[Source]]:
    """A folder holding team.json plus one dated Markdown file per record."""
    workspace = Workspace.model_validate_json((folder / "team.json").read_text(encoding="utf-8"))
    sources = [parse_markdown_source(p) for p in folder.iterdir() if DATED_NAME.match(p.name)]
    return workspace, sorted(sources, key=lambda s: s.occurred_at)


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
SEPARATE_PAGES = {"child_page", "child_database"}  # imported as their own sources, not inlined


def plain_text(rich_text: list[dict]) -> str:
    return "".join(part.get("plain_text", "") for part in rich_text)


def blocks_to_markdown(blocks: list[dict], depth: int = 0) -> list[str]:
    """Render Notion blocks, with children pre-fetched under "children", as Markdown lines."""
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
        lines += blocks_to_markdown(block.get("children", []), depth + 1)
    return lines


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


def naive_utc(value: str) -> datetime:
    """Parse a Notion timestamp; offsets are converted to UTC so all times compare."""
    moment = datetime.fromisoformat(value)
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def page_time(page: dict, title: str) -> datetime:
    """Meeting time: the page's Date property, else a date in the title, else its creation time."""
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "date" and prop.get("date"):
            return naive_utc(prop["date"]["start"])
    if match := DATE_IN_TEXT.search(title):
        return datetime.fromisoformat(match.group(1))
    return naive_utc(page["created_time"])


def import_notion(parent_page_id: str) -> list[Source]:
    """Each child page of the given page becomes one Source. Databases are not read yet."""
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise SystemExit("NOTION_TOKEN is not set; add it to the .env file at the repo root.")
    client = Client(auth=token)
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
                occurred_at=page_time(page, title),
                title=title,
                text="\n".join([f"# {title}", "", *body]),
                url=page.get("url"),
            )
        )
    return sorted(sources, key=lambda s: s.occurred_at)


# --- CLI -----------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Load team records into data/converge.db.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="load every scenario under data/demo")
    notion = commands.add_parser("notion", help="import the child pages of one Notion page")
    notion.add_argument("page_id")
    notion.add_argument("--team", type=Path, required=True, help="team.json: workspace and members")
    args = parser.parse_args()

    if args.command == "demo":
        loaded = [load_markdown_folder(p.parent) for p in sorted(DEMO_DIR.glob("*/team.json"))]
    else:
        workspace = Workspace.model_validate_json(args.team.read_text(encoding="utf-8"))
        loaded = [(workspace, import_notion(args.page_id))]

    with store.connect() as conn:
        for workspace, sources in loaded:
            store.save_workspace(conn, workspace)
            store.save_sources(conn, workspace.id, sources)
            span = (
                f"{sources[0].occurred_at:%Y-%m-%d} → {sources[-1].occurred_at:%Y-%m-%d}"
                if sources
                else "empty"
            )
            print(f"{workspace.id}: {len(sources)} sources ({span})")


if __name__ == "__main__":
    main()
