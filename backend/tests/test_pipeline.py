"""Tests for ingest, storage, and the demo dataset's gold labels (no network, no LLM)."""

from pathlib import Path

import pytest

from app import store
from app.ingest import DEMO_DIR, blocks_to_markdown, load_markdown_folder
from app.models import Gold

SCENARIOS = sorted(p.parent for p in DEMO_DIR.glob("*/team.json"))
PARENT_COUNTS = {"new": range(0, 1), "branch": range(1, 2), "merge": range(2, 10)}


def test_demo_has_three_scenarios() -> None:
    assert len(SCENARIOS) >= 3


@pytest.mark.parametrize("folder", SCENARIOS, ids=lambda p: p.name)
def test_scenario_loads_in_time_order(folder: Path) -> None:
    workspace, sources = load_markdown_folder(folder)
    times = [s.occurred_at for s in sources]
    assert 3 <= len(workspace.members) <= 5
    assert len(sources) >= 5
    assert times == sorted(times) and len(set(times)) == len(times)
    assert all(t.hour or t.minute for t in times), "every note should state its start time"


@pytest.mark.parametrize("folder", SCENARIOS, ids=lambda p: p.name)
def test_gold_labels_match_sources(folder: Path) -> None:
    """Every gold quote occurs exactly once in its source; every id resolves in time order."""
    workspace, sources = load_markdown_folder(folder)
    gold = Gold.model_validate_json((folder / "gold.json").read_text(encoding="utf-8"))
    assert gold.workspace == workspace.id
    assert [a.source for a in gold.analyses] == [s.id for s in sources]

    members = {m.id for m in workspace.members}
    ideas: set[str] = set()
    problems: list[str] = []
    for analysis, source in zip(gold.analyses, sources, strict=True):
        where = analysis.source
        for op in analysis.ops:
            if op.op in PARENT_COUNTS:
                if op.idea in ideas or not op.title:
                    problems.append(f"{where}: {op.op} {op.idea} is a duplicate or has no title")
                if len(op.parents) not in PARENT_COUNTS[op.op] or not set(op.parents) <= ideas:
                    problems.append(f"{where}: {op.op} {op.idea} has bad parents {op.parents}")
                ideas.add(op.idea)
            elif op.idea not in ideas:
                problems.append(f"{where}: {op.op} on unknown idea {op.idea}")
            if op.owner and op.owner not in members:
                problems.append(f"{where}: unknown owner {op.owner}")
        for m in analysis.mentions:
            if m.member not in members or m.idea not in ideas:
                problems.append(f"{where}: mention of unknown {m.member}/{m.idea}")
        for item in analysis.items:
            if (item.member and item.member not in members) or (
                item.idea and item.idea not in ideas
            ):
                problems.append(f"{where}: item refers to unknown {item.member}/{item.idea}")
        for entry in [*analysis.ops, *analysis.mentions, *analysis.items]:
            if (count := source.text.count(entry.quote)) != 1:
                problems.append(f"{where}: quote found {count}x: {entry.quote!r}")
    assert not problems, "\n".join(problems)


def test_store_round_trip(tmp_path: Path) -> None:
    workspace, sources = load_markdown_folder(SCENARIOS[0])
    with store.connect(tmp_path / "test.db") as conn:
        store.save_workspace(conn, workspace)
        store.save_sources(conn, workspace.id, sources)
        store.save_sources(conn, workspace.id, sources)  # importing again must not duplicate
        assert store.list_workspaces(conn) == [workspace]
        assert store.list_sources(conn, workspace.id) == sources


def test_notion_blocks_render_as_markdown() -> None:
    def block(kind: str, text: str = "", **extra: object) -> dict:
        return {"type": kind, kind: {"rich_text": [{"plain_text": text}], **extra}}

    proposal = block("bulleted_list_item", "Mina: proposal")
    proposal["children"] = [block("bulleted_list_item", "needs 4 GPUs")]
    blocks = [
        block("heading_2", "Discussion"),
        proposal,
        block("to_do", "Jae: baseline", checked=True),
        block("divider"),
    ]
    assert blocks_to_markdown(blocks) == [
        "## Discussion",
        "- Mina: proposal",
        "  - needs 4 GPUs",
        "- [x] Jae: baseline",
        "---",
    ]
