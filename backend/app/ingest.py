"""Turn a team's records into Sources: dated Markdown files (demo data, exports) or Notion pages.

Usage, from backend/:
    uv run python -m app.ingest demo                      # demo scenarios + gold labels
    uv run python -m app.ingest notion <page-id> --team path/to/team.json
    uv run python -m app.ingest push-notion <page-id> [--scenario sim-to-real]
    uv run python -m app.ingest link-notion <workspace-id> <page-id>
    uv run python -m app.ingest analyze <workspace-id>        # what the Update button does
"""

import argparse
import mimetypes
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from notion_client import APIResponseError, Client

from app import REPO_ROOT, store
from app.models import Asset, Gold, Source, SourceAnalysis, Workspace

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


def media_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def load_assets(folder: Path) -> list[Asset]:
    """The images a folder's notes embed, kept in its assets/ subfolder."""
    return [
        Asset(name=path.name, media_type=media_type(path.name), data=path.read_bytes())
        for path in sorted((folder / "assets").glob("*"))
        if path.is_file()
    ]


def load_gold(folder: Path) -> list[SourceAnalysis]:
    """Hand-labelled analyses of a demo scenario; empty when the folder has none."""
    path = folder / "gold.json"
    if not path.exists():
        return []
    return Gold.model_validate_json(path.read_text(encoding="utf-8")).analyses


def demo_folders() -> list[Path]:
    return sorted(p.parent for p in DEMO_DIR.glob("*/team.json"))


def load_demo(conn: sqlite3.Connection) -> list[str]:
    """Store every demo scenario with its images and gold analyses; returns one summary line
    per scenario."""
    lines = []
    for folder in demo_folders():
        workspace, sources = load_markdown_folder(folder)
        lines.append(save(conn, workspace, sources))
        store.save_assets(conn, workspace.id, load_assets(folder))
        store.save_analyses(conn, workspace.id, "gold", load_gold(folder))
    return lines


def save(conn: sqlite3.Connection, workspace: Workspace, sources: list[Source]) -> str:
    store.save_workspace(conn, workspace)
    store.save_sources(conn, workspace.id, sources)
    if not sources:
        return f"{workspace.id}: no sources"
    first, last = sources[0].occurred_at, sources[-1].occurred_at
    return f"{workspace.id}: {len(sources)} sources ({first:%Y-%m-%d} → {last:%Y-%m-%d})"


# --- Notion blocks <-> Markdown --------------------------------------------------
#
# Notes keep one Notion block per line, children indented by two spaces. Beyond plain text:
#   ▸ toggle            | a | b | table rows        ![caption](assets/x.png) image
#   📄 sub-page title (its content nested under it)    $$ x^2 $$ equation

# Block type -> Markdown template for blocks that hold one line of text.
TEXT_BLOCKS = {
    "paragraph": "{}",
    "heading_1": "# {}",
    "heading_2": "## {}",
    "heading_3": "### {}",
    "bulleted_list_item": "- {}",
    "numbered_list_item": "1. {}",
    "toggle": "▸ {}",
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
    ("▸ ", "toggle"),
]
LAYOUT_BLOCKS = {"column_list", "column", "synced_block"}  # their children render in place
LINK_BLOCKS = {"bookmark", "embed", "link_preview", "video", "pdf", "file", "audio"}
SUB_PAGE = "📄 "
IMAGE_LINE = re.compile(r"^!\[(?P<caption>.*)\]\((?P<url>[^()\s]+)\)$")
TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")
CELL_BORDER = re.compile(r"(?<!\\)\|")
SESSION_TITLE = "Converge session"  # pages Converge writes itself; skipped on import


def plain_text(rich_text: list[dict]) -> str:
    # The API returns "plain_text"; blocks built locally only have "text.content".
    return "".join(
        part["plain_text"] if "plain_text" in part else part["text"]["content"]
        for part in rich_text
    )


def rich_text(content: str) -> list[dict]:
    return [{"type": "text", "text": {"content": content}}] if content else []


def file_url(body: dict) -> str:
    """URL of an image or file block: an external link, or a Notion-hosted file."""
    kind = body.get("type", "")
    return body.get(kind, {}).get("url", "") if kind in ("external", "file") else ""


def table_row(cells: list[str]) -> str:
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"


def table_cells(line: str) -> list[str]:
    """Cells of a Markdown table row; `\\|` is a pipe inside a cell."""
    inner = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip().replace("\\|", "|") for cell in CELL_BORDER.split(inner)]


def blocks_to_markdown(blocks: list[dict], depth: int = 0) -> list[str]:
    """Render Notion blocks as Markdown lines, nested children indented by two spaces."""
    lines: list[str] = []
    indent = "  " * depth
    for block in blocks:
        kind = block["type"]
        body = block.get(kind, {})
        # Read from the API, children are attached to the block by fetch_blocks; built by
        # markdown_to_blocks, they sit inside the block body as the create API expects.
        children = block.get("children") or body.get("children", [])
        text = plain_text(body.get("rich_text", []))
        if kind in LAYOUT_BLOCKS:
            lines += blocks_to_markdown(children, depth)
            continue
        if kind == "table":
            for number, row in enumerate(children):
                cells = [plain_text(cell) for cell in row["table_row"]["cells"]]
                lines.append(indent + table_row(cells))
                if number == 0 and body.get("has_column_header"):
                    lines.append(indent + table_row(["---"] * len(cells)))
            continue
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
        elif kind == "equation":
            lines.append(f"{indent}$$ {body.get('expression', '')} $$")
        elif kind == "image":
            lines.append(f"{indent}![{plain_text(body.get('caption', []))}]({file_url(body)})")
        elif kind in LINK_BLOCKS:
            url = body.get("url") or file_url(body)
            label = plain_text(body.get("caption", [])) or body.get("name") or url
            lines.append(f"{indent}[{label}]({url})")
        elif kind == "child_page":
            lines.append(f"{indent}{SUB_PAGE}{body.get('title', '')}")
        elif kind == "child_database":
            lines.append(f"{indent}🗃 {body.get('title', '')}")
        lines += blocks_to_markdown(children, depth + 1)
    return lines


def markdown_line_to_block(line: str) -> dict:
    if line == "---":
        return {"type": "divider", "divider": {}}
    if match := IMAGE_LINE.match(line):
        return {
            "type": "image",
            "image": {
                "type": "external",
                "external": {"url": match["url"]},
                "caption": rich_text(match["caption"]),
            },
        }
    if line.startswith(SUB_PAGE):
        return {"type": "child_page", "child_page": {"title": line.removeprefix(SUB_PAGE)}}
    if line.startswith("$$ ") and line.endswith(" $$"):
        return {"type": "equation", "equation": {"expression": line[3:-3]}}
    kind, content = "paragraph", line
    if match := NUMBERED.match(line):
        kind, content = "numbered_list_item", line[match.end() :]
    else:
        for prefix, prefix_kind in LINE_PREFIXES:
            if line.startswith(prefix):
                kind, content = prefix_kind, line[len(prefix) :]
                break
    body: dict = {"rich_text": rich_text(content)}
    if kind == "to_do":
        body["checked"] = line.startswith("- [x] ")
    return {"type": kind, kind: body}


def table_row_block(cells: list[str], width: int) -> dict:
    cells = (cells + [""] * width)[:width]
    return {"type": "table_row", "table_row": {"cells": [rich_text(c) for c in cells]}}


def markdown_to_blocks(text: str) -> tuple[str, list[dict]]:
    """Split a Markdown note into its title (first `# ` line) and Notion blocks.

    The inverse of blocks_to_markdown for the syntax our notes use: indented lines become
    children of the line above them, consecutive `|` lines one table, and inline Markdown
    such as **bold** stays literal.
    """
    title = ""
    blocks: list[dict] = []
    open_blocks: list[dict] = []  # the latest block at each indentation depth
    table: dict | None = None  # the table the previous line added a row to
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            table = None
            continue
        if not title and raw.startswith("# "):
            title = line[2:]
            continue
        depth = min((len(raw) - len(raw.lstrip(" "))) // 2, len(open_blocks))
        if line.startswith("|") and table is not None and open_blocks[depth:][:1] == [table]:
            body = table["table"]
            cells = table_cells(line)
            if len(body["children"]) == 1 and all(TABLE_SEPARATOR.match(c) for c in cells):
                body["has_column_header"] = True
            else:
                body["children"].append(table_row_block(cells, body["table_width"]))
            continue
        if line.startswith("|"):
            cells = table_cells(line)
            table = block = {
                "type": "table",
                "table": {
                    "table_width": len(cells),
                    "has_column_header": False,
                    "has_row_header": False,
                    "children": [table_row_block(cells, len(cells))],
                },
            }
        else:
            table, block = None, markdown_line_to_block(line)
        if depth:
            parent = open_blocks[depth - 1]
            parent_body = parent[parent["type"]]
            parent_body.setdefault("children", []).append(block)
            if parent["type"].startswith("heading_"):
                parent_body["is_toggleable"] = True  # Notion headings hold children only as toggles
        else:
            blocks.append(block)
        del open_blocks[depth:]
        open_blocks.append(block)
    return title, blocks


# --- Notion API ----------------------------------------------------------------


def notion() -> Client:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError("NOTION_TOKEN is not set; add it to the .env file at the repo root.")
    return Client(auth=token)


def fetch_blocks(client: Client, block_id: str, nested: bool = True) -> list[dict]:
    """All child blocks of a page or block, following pagination. With `nested`, children are
    fetched too, including the content of sub-pages but not the rows of inline databases."""
    blocks: list[dict] = []
    cursor = None
    while True:
        paging = {"start_cursor": cursor} if cursor else {}
        page = client.blocks.children.list(block_id=block_id, **paging)
        for block in page["results"]:
            if nested and block.get("has_children") and block["type"] != "child_database":
                block["children"] = fetch_blocks(client, block["id"])
            blocks.append(block)
        if not page.get("has_more"):
            return blocks
        cursor = page["next_cursor"]


def localize_images(blocks: list[dict], assets: dict[str, Asset]) -> None:
    """Download Notion-hosted images into `assets` and point their blocks at the copies:
    Notion's file links expire after an hour."""
    for block in blocks:
        body = block.get(block["type"], {})
        if block["type"] == "image" and body.get("type") == "file":
            url = body["file"]["url"]
            response = httpx.get(url, timeout=60, follow_redirects=True)
            response.raise_for_status()
            name = re.sub(r"[\s()]+", "-", unquote(urlparse(url).path.rsplit("/", 1)[-1]))
            if not name or (name in assets and assets[name].data != response.content):
                name = f"{block['id'][:8]}-{name or 'image.png'}"
            assets[name] = Asset(name=name, media_type=media_type(name), data=response.content)
            block["image"] = {
                "type": "external",
                "external": {"url": f"assets/{name}"},
                "caption": body.get("caption", []),
            }
        localize_images(block.get("children", []), assets)


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


def page_title(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return plain_text(prop["title"]) or "Untitled"
    return "Untitled"


def database_pages(client: Client, database_id: str) -> list[dict]:
    """The row pages of a Notion database, from all of its data sources."""
    pages: list[dict] = []
    for data_source in client.databases.retrieve(database_id=database_id).get("data_sources", []):
        cursor = None
        while True:
            paging = {"start_cursor": cursor} if cursor else {}
            batch = client.data_sources.query(data_source_id=data_source["id"], **paging)
            pages += [row for row in batch["results"] if row.get("object") == "page"]
            if not batch.get("has_more"):
                break
            cursor = batch["next_cursor"]
    return pages


def page_source(client: Client, page: dict, assets: dict[str, Asset]) -> Source:
    """One Notion page with everything inside it (toggles, tables, images, sub-pages)."""
    title = page_title(page)
    blocks = fetch_blocks(client, page["id"])
    localize_images(blocks, assets)
    body = blocks_to_markdown(blocks)
    return Source(
        id=page["id"],
        occurred_at=page_time(page, title, body),
        title=title,
        text="\n".join([f"# {title}", "", *body]),
        url=page.get("url"),
    )


def import_notion(page_id: str) -> tuple[list[Source], list[Asset]]:
    """The notes under one Notion page: each child page and each row of a child database
    becomes one Source. A page without either is a single Source itself, and a database id
    reads the database's rows. Databases are how many labs keep meeting notes."""
    client = notion()
    assets: dict[str, Asset] = {}
    try:
        top = client.pages.retrieve(page_id=page_id)
    except APIResponseError:  # not a page: maybe the id of a database
        rows = database_pages(client, page_id)
        sources = [page_source(client, row, assets) for row in rows]
        return sorted(sources, key=lambda s: s.occurred_at), list(assets.values())
    pages: list[dict] = []
    for block in fetch_blocks(client, page_id, nested=False):
        if block["type"] == "child_page":
            if not block["child_page"]["title"].startswith(SESSION_TITLE):  # Converge's own mirrors
                pages.append(client.pages.retrieve(page_id=block["id"]))
        elif block["type"] == "child_database":
            pages += database_pages(client, block["id"])
    sources = [page_source(client, page, assets) for page in pages or [top]]
    return sorted(sources, key=lambda s: s.occurred_at), list(assets.values())


def title_property(title: str) -> dict:
    return {"title": {"title": [{"type": "text", "text": {"content": title}}]}}


def upload_images(client: Client, blocks: list[dict], assets: dict[str, Asset]) -> None:
    """Swap images that point at local `assets/<name>` files for Notion file uploads."""
    for block in blocks:
        body = block[block["type"]]
        url = file_url(body) if block["type"] == "image" else ""
        if (asset := assets.get(url.removeprefix("assets/"))) and url.startswith("assets/"):
            upload = client.file_uploads.create(
                mode="single_part", filename=asset.name, content_type=asset.media_type
            )
            client.file_uploads.send(
                file_upload_id=upload["id"], file=(asset.name, asset.data, asset.media_type)
            )
            block["image"] = {
                "type": "file_upload",
                "file_upload": {"id": upload["id"]},
                "caption": body.get("caption", []),
            }
        upload_images(client, body.get("children", []), assets)


def append_blocks(client: Client, parent_id: str, blocks: list[dict]) -> None:
    """Append blocks under a page or block. One request may nest only two levels, so deeper
    children follow in their own requests; sub-pages are created as pages at the end."""
    sent: list[tuple[dict, list[dict]]] = []  # (block as sent, children sent afterwards)
    for block in blocks:
        kind = block["type"]
        if kind == "child_page":
            continue
        children = block[kind].get("children", [])
        if kind != "table" and any(
            c["type"] == "child_page" or c[c["type"]].get("children") for c in children
        ):
            shallow = {key: value for key, value in block[kind].items() if key != "children"}
            sent.append(({"type": kind, kind: shallow}, children))
        else:
            sent.append((block, []))
    for start in range(0, len(sent), 100):  # the API takes at most 100 blocks per request
        batch = sent[start : start + 100]
        made = client.blocks.children.append(
            block_id=parent_id, children=[block for block, _ in batch]
        )["results"]
        for created, (_, later) in zip(made, batch, strict=True):
            if later:
                append_blocks(client, created["id"], later)
    for block in blocks:
        if block["type"] == "child_page":
            body = block["child_page"]
            page = client.pages.create(
                parent={"page_id": parent_id}, properties=title_property(body["title"])
            )
            append_blocks(client, page["id"], body.get("children", []))


def push_to_notion(
    parent_page_id: str, title: str, sources: list[Source], assets: list[Asset]
) -> str:
    """Create page `title` under the parent with one child page per source; returns its id.

    Puts the synthetic demo scenarios into Notion for demos and end-to-end import tests.
    """
    client = notion()
    by_name = {asset.name: asset for asset in assets}
    folder = client.pages.create(
        parent={"page_id": parent_page_id}, properties=title_property(title)
    )
    for source in sources:
        page_title, blocks = markdown_to_blocks(source.text)
        upload_images(client, blocks, by_name)
        page = client.pages.create(
            parent={"page_id": folder["id"]}, properties=title_property(page_title)
        )
        append_blocks(client, page["id"], blocks)
    return folder["id"]


def notion_page_id(url: str) -> str:
    return re.findall(r"[0-9a-f]{32}", url)[-1]


def write_session_line(parent_page_id: str, session: Source, line: str) -> str:
    """Mirror a new session-note line into Notion and return the session page's URL: the
    first edit of a day creates the page under the team's page, later ones append to it."""
    client = notion()
    if session.url:
        client.blocks.children.append(
            block_id=notion_page_id(session.url), children=[markdown_line_to_block(f"- {line}")]
        )
        return session.url
    title, blocks = markdown_to_blocks(session.text)
    page = client.pages.create(parent={"page_id": parent_page_id}, properties=title_property(title))
    append_blocks(client, page["id"], blocks)
    return page["url"]


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
    link = commands.add_parser(
        "link-notion", help='write a workspace\'s edits to a Notion page ("" unlinks)'
    )
    link.add_argument("workspace_id")
    link.add_argument("page_id")
    analyze = commands.add_parser(
        "analyze", help="read a workspace's notes with the model and write its briefing"
    )
    analyze.add_argument("workspace_id")
    analyze.add_argument("--fresh", action="store_true", help="read every note again")
    args = parser.parse_args()

    try:
        if args.command == "analyze":
            from app import pipeline  # imported here: pipeline itself imports this module

            def report(**progress: object) -> None:
                print("  ".join(f"{key}={value}" for key, value in progress.items()), flush=True)

            board = pipeline.update_workspace(args.workspace_id, report, fresh=args.fresh)
            print(board.model_dump_json(indent=2))
            return
        if args.command == "push-notion":
            for folder in demo_folders():
                if args.scenario and folder.name not in args.scenario:
                    continue
                workspace, sources = load_markdown_folder(folder)
                page_id = push_to_notion(args.page_id, workspace.name, sources, load_assets(folder))
                print(f"{workspace.id}: {len(sources)} pages under Notion page {page_id}")
            return

        with store.connect() as conn:
            if args.command == "demo":
                print("\n".join(load_demo(conn)))
            elif args.command == "link-notion":
                if not store.link_notion(conn, args.workspace_id, args.page_id or None):
                    raise RuntimeError(f"no workspace {args.workspace_id!r}; load it first")
                print(f"{args.workspace_id}: edits go to Notion page {args.page_id or '(none)'}")
            else:
                team = Workspace.model_validate_json(args.team.read_text(encoding="utf-8"))
                workspace = team.model_copy(update={"notion_page_id": args.page_id})
                sources, assets = import_notion(args.page_id)
                print(save(conn, workspace, sources))
                store.save_assets(conn, workspace.id, assets)
                print(f"{workspace.id}: {len(assets)} images")
    except RuntimeError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
