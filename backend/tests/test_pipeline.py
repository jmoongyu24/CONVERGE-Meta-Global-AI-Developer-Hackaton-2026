"""Tests for ingest, storage, the lineage engine, and the API (no network, no LLM)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import store
from app.ingest import (
    DEMO_DIR,
    blocks_to_markdown,
    demo_folders,
    load_demo,
    load_gold,
    load_markdown_folder,
    markdown_to_blocks,
)
from app.main import app
from app.models import LineageState, StanceShift
from app.pipeline import build_state

SCENARIOS = demo_folders()


def replay(scenario: str, as_of: str | None = None) -> LineageState:
    folder = DEMO_DIR / scenario
    workspace, sources = load_markdown_folder(folder)
    analyses = {a.source: a for a in load_gold(folder)}
    return build_state(workspace, sources, analyses, as_of)


# --- Demo data and ingest ------------------------------------------------------


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
def test_gold_labels_are_clean(folder: Path) -> None:
    """Gold replays without issues, and every quote points at exactly one place in its source."""
    workspace, sources = load_markdown_folder(folder)
    analyses = load_gold(folder)
    assert [a.source for a in analyses] == [s.id for s in sources]
    assert build_state(workspace, sources, {a.source: a for a in analyses}).issues == []
    ambiguous = [
        f"{analysis.source}: {entry.quote!r}"
        for analysis, source in zip(analyses, sources, strict=True)
        for entry in [*analysis.ops, *analysis.mentions, *analysis.items]
        if source.text.count(entry.quote) != 1
    ]
    assert ambiguous == []


@pytest.mark.parametrize("folder", SCENARIOS, ids=lambda p: p.name)
def test_notes_survive_a_notion_round_trip(folder: Path) -> None:
    """Markdown -> Notion blocks -> Markdown keeps every non-empty line of every note."""
    _, sources = load_markdown_folder(folder)
    for source in sources:
        title, blocks = markdown_to_blocks(source.text)
        assert [f"# {title}", *blocks_to_markdown(blocks)] == [
            line for line in source.text.splitlines() if line.strip()
        ]


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


def test_store_round_trip(tmp_path: Path) -> None:
    workspace, sources = load_markdown_folder(SCENARIOS[0])
    with store.connect(tmp_path / "test.db") as conn:
        store.save_workspace(conn, workspace)
        store.save_sources(conn, workspace.id, sources)
        store.save_sources(conn, workspace.id, sources)  # importing again must not duplicate
        assert store.list_workspaces(conn) == [workspace]
        assert store.list_sources(conn, workspace.id) == sources


# --- Lineage engine ------------------------------------------------------------


def test_lineage_of_self_organizing_agents() -> None:
    state = replay("self-organizing-agents")
    assert {i.id: i.status for i in state.ideas} == {
        "learned-topology": "parked",
        "role-negotiation": "active",
        "per-role-finetune": "dropped",
        "bandit-topology": "active",
        "comm-budget": "active",
        "hybrid-self-org": "active",
        "hierarchical-negotiation": "active",
    }
    assert {(e.parent, e.child, e.kind) for e in state.edges} == {
        ("learned-topology", "bandit-topology", "branch"),
        ("role-negotiation", "per-role-finetune", "branch"),
        ("role-negotiation", "hybrid-self-org", "merge"),
        ("bandit-topology", "hybrid-self-org", "merge"),
        ("role-negotiation", "hierarchical-negotiation", "branch"),
    }


def test_time_travel_shows_only_the_past() -> None:
    state = replay("self-organizing-agents", as_of="2026-09-23-weekly-05")
    seen = {s.id for s in state.sources[:5]}
    assert [i.id for i in state.ideas] == [
        "learned-topology",
        "role-negotiation",
        "per-role-finetune",
    ]
    assert state.ideas[2].status == "parked"
    assert {m.source for m in state.mentions} <= seen
    assert [op.op for op in state.digest.ops] == ["park"]


def test_dormancy_and_stance_shifts() -> None:
    state = replay("long-term-dependency")
    status = {i.id: i.status for i in state.ideas}
    assert status["gru"] == "dormant"  # merged into gru-attention, then never discussed alone
    assert status["conv-encoder"] == "dropped"
    assert status["lstm"] == "parked"
    # Daniel came around on attention: a challenge in September, support in November.
    november = replay("long-term-dependency", as_of="2014-11-12-group-meeting")
    shift = StanceShift(member="daniel", idea="soft-attention", before="challenge", after="support")
    assert shift in november.digest.shifts


def test_bad_claims_are_dropped_not_trusted() -> None:
    folder = DEMO_DIR / "sim-to-real"
    workspace, sources = load_markdown_folder(folder)
    first = load_gold(folder)[0]
    first.mentions[0].quote = "a sentence nobody wrote"
    first.mentions[1].member = "someone-else"
    state = build_state(workspace, sources, {first.source: first}, as_of=first.source)
    assert len(state.mentions) == len(first.mentions) - 2
    assert len(state.issues) == 2


# --- API -----------------------------------------------------------------------


def test_api_serves_state_and_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "api.db")
    with store.connect() as conn:
        load_demo(conn)
    client = TestClient(app)

    workspaces = client.get("/api/workspaces").json()
    assert {w["id"] for w in workspaces} == {p.name for p in SCENARIOS}

    url = "/api/workspaces/long-term-dependency/state"
    state = client.get(url, params={"as_of": "2014-11-12-group-meeting"}).json()
    assert [i["id"] for i in state["ideas"]] == ["vanilla-rnn", "lstm", "soft-attention", "gru"]
    assert len(state["sources"]) == 10  # the slider always sees the whole timeline
    assert client.get(url, params={"as_of": "no-such-meeting"}).status_code == 404

    source = client.get("/api/workspaces/sim-to-real/sources/2026-03-04-kickoff").json()
    assert source["text"].startswith("# Kickoff")
