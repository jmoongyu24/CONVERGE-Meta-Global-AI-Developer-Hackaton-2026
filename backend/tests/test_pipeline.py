"""Tests for ingest, storage, the lineage and architecture engine, and the API (no network,
no LLM)."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import llm, pipeline, store
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
from app.models import (
    Acceptance,
    Assignment,
    Board,
    Claim,
    Contest,
    Contribution,
    Evidence,
    Extraction,
    IdeaOp,
    LineageState,
    Member,
    ModuleOp,
    PersonFocus,
    Position,
    Proposal,
    Roster,
    SourceAnalysis,
    StanceShift,
    Step,
    Synthesis,
    Workspace,
)
from app.pipeline import build_state, check_board, repair_quote

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
        for entry in [*analysis.ops, *analysis.mentions, *analysis.items, *analysis.modules]
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

    def cells(*texts: str) -> dict:
        return {"type": "table_row", "table_row": {"cells": [[{"plain_text": t}] for t in texts]}}

    proposal = block("bulleted_list_item", "Mina: proposal")
    proposal["children"] = [block("bulleted_list_item", "needs 4 GPUs")]
    details = block("toggle", "Architecture")
    table = {"type": "table", "table": {"has_column_header": True}}
    table["children"] = [cells("Module", "Owner"), cells("Encoder | v2", "Daniel")]
    details["children"] = [table]
    image = {
        "type": "image",
        "image": {
            "type": "file",
            "file": {"url": "https://files.example/sketch.png"},
            "caption": [{"plain_text": "whiteboard"}],
        },
    }
    sub_page = {"type": "child_page", "child_page": {"title": "Replication notes"}}
    sub_page["children"] = [block("paragraph", "4 layers")]
    columns = {"type": "column_list", "column_list": {}}
    columns["children"] = [
        {"type": "column", "column": {}, "children": [block("paragraph", "left")]}
    ]
    blocks = [
        block("heading_2", "Discussion"),
        proposal,
        block("to_do", "Jae: baseline", checked=True),
        block("divider"),
        details,
        image,
        sub_page,
        columns,
        {"type": "equation", "equation": {"expression": "a^2"}},
    ]
    assert blocks_to_markdown(blocks) == [
        "## Discussion",
        "- Mina: proposal",
        "  - needs 4 GPUs",
        "- [x] Jae: baseline",
        "---",
        "▸ Architecture",
        "  | Module | Owner |",
        "  | --- | --- |",
        "  | Encoder \\| v2 | Daniel |",
        "![whiteboard](https://files.example/sketch.png)",
        "📄 Replication notes",
        "  4 layers",
        "left",
        "$$ a^2 $$",
    ]


def test_markdown_builds_rich_notion_blocks() -> None:
    title, blocks = markdown_to_blocks(
        "# Weekly\n"
        "## Plan\n"
        "  - toggled under the heading\n"
        "▸ Architecture\n"
        "  | Module | Owner |\n"
        "  | --- | --- |\n"
        "  | Encoder | Daniel |\n"
        "![Sora's sketch](assets/sketch.png)\n"
        "📄 Replication\n"
        "  4 layers\n"
    )
    heading, toggle, image, sub_page = blocks
    assert title == "Weekly"
    assert heading["heading_2"]["is_toggleable"]
    table = toggle["toggle"]["children"][0]["table"]
    assert (table["table_width"], table["has_column_header"], len(table["children"])) == (
        2,
        True,
        2,
    )
    assert image["image"]["external"]["url"] == "assets/sketch.png"
    assert sub_page["child_page"]["title"] == "Replication"
    assert sub_page["child_page"]["children"][0]["type"] == "paragraph"


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
    closed = {i.id: i.closed for i in state.ideas if i.closed}
    assert closed["conv-encoder"].quote == "Dropping the conv encoder."
    assert set(closed) == {"vanilla-rnn", "lstm", "conv-encoder"}
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


# --- Architecture --------------------------------------------------------------


def test_architecture_of_long_term_dependency() -> None:
    state = replay("long-term-dependency")
    modules = {m.id: m for m in state.modules}
    assert {m.id for m in state.modules if m.status == "removed"} == {
        "rnn-encoder",
        "lstm-encoder",
        "conv-encoder",
    }
    assert modules["attention"].status == "adopted" and modules["attention"].owner == "sora"
    assert {m.id for m in state.modules if m.group == "self-attn-encoder"} == {"mhsa", "ffn"}
    assert modules["answer"].inputs == ["attention", "self-attn-encoder", "recurrent-memory"]
    # the module's history is its blame: every change with the quote behind it
    assert [h.source for h in modules["self-attn-encoder"].history][:2] == [
        "2016-06-08-group-meeting",
        "2017-06-21-group-meeting",
    ]


def test_architecture_time_travel_and_digest() -> None:
    state = replay("long-term-dependency", as_of="2014-09-17-group-meeting")
    modules = {m.id: m for m in state.modules}
    assert modules["rnn-encoder"].status == "removed"
    assert modules["attention"].status == "proposed"
    assert modules["answer"].inputs == ["lstm-encoder", "attention"]
    assert "gru-encoder" not in modules
    assert [(op.op, op.module) for op in state.digest.modules] == [
        ("update", "lstm-encoder"),
        ("add", "attention"),
        ("update", "answer"),
    ]
    image_line = state.digest.modules[-1].line
    assert (
        "![Sora's whiteboard"
        in load_markdown_folder(DEMO_DIR / "long-term-dependency")[1][3].text.splitlines()[
            image_line - 1
        ]
    )


def test_removing_a_block_removes_its_layers() -> None:
    folder = DEMO_DIR / "long-term-dependency"
    workspace, sources = load_markdown_folder(folder)
    analyses = {a.source: a for a in load_gold(folder)}
    last = sources[-1].id
    analyses[last].modules.append(
        ModuleOp(op="remove", module="self-attn-encoder", quote="Lab agenda and who leads it")
    )
    modules = {m.id: m for m in build_state(workspace, sources, analyses).modules}
    assert {modules[m].status for m in ("self-attn-encoder", "mhsa", "ffn")} == {"removed"}
    assert modules["answer"].inputs == ["attention", "recurrent-memory"]


def test_bad_module_ops_are_dropped_or_repaired() -> None:
    folder = DEMO_DIR / "sim-to-real"
    workspace, sources = load_markdown_folder(folder)
    first = sources[0]
    analysis = SourceAnalysis(
        source=first.id,
        modules=[
            ModuleOp(op="add", module="x", title="X", quote="a sentence nobody wrote"),
            ModuleOp(op="update", module="ghost", quote="Peg-in-hole insertion"),
            ModuleOp(
                op="add",
                module="sim",
                title="Simulator",
                owner="nobody",
                inputs=["missing"],
                quote="Peg-in-hole insertion",
            ),
        ],
    )
    state = build_state(workspace, sources, {first.id: analysis}, as_of=first.id)
    assert [(m.id, m.owner, m.inputs) for m in state.modules] == [("sim", None, [])]
    assert len(state.issues) == 4  # two ops dropped, two references repaired


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

    image = client.get("/api/workspaces/sim-to-real/assets/2026-05-13-calibrated-band.png")
    assert image.headers["content-type"] == "image/png" and image.content.startswith(b"\x89PNG")
    assert client.get("/api/workspaces/sim-to-real/assets/nope.png").status_code == 404


def test_edits_become_quoted_session_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "edits.db")
    with store.connect() as conn:
        load_demo(conn)
    client = TestClient(app)
    base = "/api/workspaces/self-organizing-agents"
    before = client.get(f"{base}/revision").json()["revision"]

    def edit(**body: object) -> dict:
        response = client.post(f"{base}/edits", json=body)
        assert response.status_code == 200, response.text
        return response.json()

    added = edit(
        member="jae",
        action="add",
        title="Shift detector",
        inputs=["runtime"],
        owner="jae",
        text="flags mid-episode task changes",
    )
    assert added["notion"] == "off"
    assert added["line"].endswith(
        'Jae Kim proposed a new module "Shift detector", fed by "Agent runtime", owned by '
        "Jae Kim: flags mid-episode task changes"
    )
    edit(member="mina", action="update", module="shift-detector", owner="mina")
    edit(member="leo", action="question", module="bandit", text="Does it react mid-episode?")
    edit(member="leo", action="challenge", module="bandit", text="Model selection, not self-org.")
    rejected = client.post(
        f"{base}/edits", json={"member": "leo", "action": "support", "module": "tasks"}
    )
    assert rejected.status_code == 400  # the task suite implements no idea to take a side on

    state = client.get(f"{base}/state").json()
    assert state["sources"][-1]["kind"] == "session" and state["issues"] == []
    detector = next(m for m in state["modules"] if m["id"] == "shift-detector")
    assert (detector["owner"], detector["status"], detector["inputs"]) == (
        "mina",
        "proposed",
        ["runtime"],
    )
    assert any(i["module"] == "bandit" and i["kind"] == "question" for i in state["items"])
    leo = next(
        c for c in state["stances"] if c["member"] == "leo" and c["idea"] == "bandit-topology"
    )
    assert leo["latest"] == "challenge" and leo["source"] == added["source"]
    assert client.get(f"{base}/revision").json()["revision"] != before


# --- Reading notes with the model (no network: the model is faked) ---------------


def test_strict_schema_requires_every_field() -> None:
    schema = llm.strict_schema(SourceAnalysis)
    objects = [schema, *schema["$defs"].values()]
    assert all(set(o["required"]) == set(o["properties"]) for o in objects)
    assert all(o["additionalProperties"] is False for o in objects)
    assert "default" not in str(schema)
    assert "title" in schema["$defs"]["IdeaOp"]["properties"]  # a field named title survives


def test_repair_quote_finds_what_the_model_retyped() -> None:
    text = "- Leo: Learning a policy per task sounds expensive.\nMina said it's \u201cfine\u201d."
    assert repair_quote(text, "sounds expensive") == "sounds expensive"
    assert repair_quote(text, 'Mina said it\'s "fine".') == "Mina said it's \u201cfine\u201d."
    assert repair_quote(text, "Learning a policy per task sound expensive") is not None
    assert repair_quote(text, "nobody wrote this sentence at all") is None


def test_contributions_mark_competing_modules() -> None:
    workspace, sources = load_markdown_folder(DEMO_DIR / "sim-to-real")
    first, second = sources[0], sources[1]
    analyses = {
        first.id: SourceAnalysis(
            source=first.id,
            ops=[
                IdeaOp(
                    op="new",
                    idea="dr",
                    title="DR",
                    owner="hana",
                    quote="massive domain randomization",
                ),
                IdeaOp(
                    op="new",
                    idea="sysid",
                    title="Sys ID",
                    owner="marco",
                    quote="system identification",
                ),
            ],
            modules=[
                ModuleOp(
                    op="add",
                    module="sim",
                    title="Simulator",
                    quote="Policies are trained in simulation",
                    contributions=[
                        Contribution(
                            member="hana",
                            approach="randomize widely",
                            idea="dr",
                            quote="massive domain randomization",
                        ),
                        Contribution(
                            member="marco",
                            approach="calibrate contact",
                            idea="sysid",
                            quote="system identification",
                        ),
                        Contribution(member="nobody", approach="x", quote="system identification"),
                    ],
                )
            ],
        ),
        second.id: SourceAnalysis(
            source=second.id,
            modules=[
                ModuleOp(
                    op="update",
                    module="sim",
                    quote="Hana agreed to drop it.",
                    contributions=[
                        Contribution(member="marco", approach="", quote="Hana agreed to drop it.")
                    ],
                )
            ],
        ),
    }
    early = build_state(workspace, sources, analyses, as_of=first.id)
    sim = next(m for m in early.modules if m.id == "sim")
    assert [c.member for c in sim.contributions] == ["hana", "marco"] and sim.contested
    assert len(early.issues) == 1  # the unknown member's contribution
    later = next(m for m in build_state(workspace, sources, analyses, second.id).modules)
    assert [c.member for c in later.contributions] == ["hana"] and not later.contested


def test_briefing_keeps_only_claims_whose_quotes_check_out() -> None:
    workspace, sources = load_markdown_folder(DEMO_DIR / "sim-to-real")
    kickoff = sources[0]
    good = Evidence(source=kickoff.id, quote="Peg-in-hole insertion with 0.5 mm clearance")
    bad = Evidence(source=kickoff.id, quote="a sentence nobody wrote")

    def step(text: str, found: Evidence, contest: int | None) -> Step:
        return Step(text=text, why="", due="", contest=contest, evidence=[found])

    contests = [
        Contest(
            question="Made up?", stakes="", module=None, sides=[], decide_by=None, evidence=[bad]
        ),
        Contest(
            question="DR or ID?", stakes="", module=None, sides=[], decide_by=None, evidence=[good]
        ),
    ]
    board = Board(
        headline=Claim(text="Insertion is open", evidence=[good]),
        problem=Claim(text="Tight insertion", evidence=[good, bad]),
        status=Claim(text="Invented", evidence=[bad]),
        agreed=[Claim(text="Hardware only", evidence=[good])],
        people=[
            PersonFocus(
                member="Hana",
                idea=Claim(text="DR", evidence=[good]),
                next=[step("Widen ranges", good, 1), step("Invent", bad, 0)],
                modules=["x"],
            ),
            PersonFocus(member="Stranger", idea=None, next=[], modules=[]),
        ],
        contests=contests,
    )
    checked, issues = check_board(board, workspace, sources, modules=set())
    assert checked.problem is not None and checked.problem.evidence == [good]
    assert checked.status is None and checked.headline is not None and len(checked.agreed) == 1
    assert [c.question for c in checked.contests] == ["DR or ID?"]
    hana = checked.people[0]
    assert [p.member for p in checked.people] == ["hana"] and hana.modules == []
    assert [(s.text, s.contest) for s in hana.next] == [("Widen ranges", 0)]  # index remapped
    # four quotes not found (problem, status, contest, step), the dropped status, contest,
    # and step, and the unknown member
    assert len(issues) == 8


def fake_model(calls: list[str]):
    """Stands in for llm.complete: answers extraction calls with one decision per note and
    briefing calls with a one-claim board."""

    def complete(messages, output, *, purpose, **_):
        calls.append(purpose)
        text = messages[-1]["content"]
        text = text if isinstance(text, str) else text[0]["text"]
        if output is Roster:
            return Roster(members=[]), llm.Usage()
        if output is Extraction:
            ids = [line.split()[2] for line in text.splitlines() if line.startswith("=== SOURCE")]
            return Extraction(analyses=[SourceAnalysis(source=i) for i in ids]), llm.Usage(
                cost=0.01
            )
        quote = "Peg-in-hole insertion with 0.5 mm clearance"
        claim = Claim(
            text="Insertion", evidence=[Evidence(source="2026-03-04-kickoff", quote=quote)]
        )
        board = Board(headline=claim, problem=claim, status=None, agreed=[], people=[], contests=[])
        return board, llm.Usage(cost=0.02)

    return complete


def test_update_reads_only_new_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "update.db")
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete", fake_model(calls))
    with store.connect() as conn:
        load_demo(conn)
    progress: list[dict] = []
    board = pipeline.update_workspace("sim-to-real", lambda **p: progress.append(p))
    assert board.problem is not None and board.issues == []
    assert [c.split()[0] for c in calls] == ["roster", "extract", "board"]
    with store.connect() as conn:
        assert len(store.list_bases(conn, "sim-to-real", "llm")) == 7
        assert store.get_board(conn, "sim-to-real", "llm") == board
    assert progress[-1]["cost"] == 0.03
    pipeline.update_workspace("sim-to-real", lambda **p: None)
    assert [c.split()[0] for c in calls][3:] == ["board"]  # nothing new to read


def test_update_job_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "job.db")
    monkeypatch.setattr(llm, "complete", fake_model([]))
    with store.connect() as conn:
        load_demo(conn)
    client = TestClient(app)
    base = "/api/workspaces/sim-to-real"
    assert client.get(f"{base}/board").status_code == 404
    assert client.post(f"{base}/update").json()["state"] == "running"
    for _ in range(100):
        status = client.get(f"{base}/update").json()
        if status["state"] != "running":
            break
        time.sleep(0.05)
    assert status["state"] == "done", status
    assert client.get(f"{base}/board").json()["problem"]["text"] == "Insertion"
    assert client.get(f"{base}/state").json()["issues"] == []  # replays the model's analyses


# --- CONVERGE Room ------------------------------------------------------------------


def test_converge_room_runs_and_adoption_lands_on_the_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "room.db")
    quote = "Calibrate the simulator's contact parameters from a few real rollouts"

    def complete(messages, output, *, purpose, **_):
        if output is Position:
            claimed = [Evidence(source="2026-03-04-kickoff", quote=quote)]
            if "hana" in purpose:  # this delegate invents a quote and must be marked unsupported
                claimed = [Evidence(source="2026-03-04-kickoff", quote="words nobody said")]
            return Position(message="My view.", evidence=claimed), llm.Usage(cost=0.01)
        if output is Synthesis:
            proposal = Proposal(
                title="Calibrate first",
                decision="Calibrate, then randomize narrowly.",
                why=Claim(text="Precision", evidence=[Evidence(source="nowhere", quote="x")]),
                assignments=[
                    Assignment(member="marco", step="identify brass", module="simulator"),
                    Assignment(member="ghost", step="haunt", module=None),
                ],
                acceptance=[Acceptance(member="marco", likely="yes", reason="his line")],
                risk="Overfits one material.",
            )
            return Synthesis(proposals=[proposal]), llm.Usage(cost=0.02)
        return fake_model([])(messages, output, purpose=purpose)

    monkeypatch.setattr(llm, "complete", complete)
    with store.connect() as conn:
        load_demo(conn)
    client = TestClient(app)
    base = "/api/workspaces/sim-to-real"
    room = client.post(
        f"{base}/converge",
        json={"question": "DR or system ID?", "members": ["hana", "marco"], "module": None},
    ).json()
    for _ in range(100):
        room = client.get(f"{base}/converge/{room['id']}").json()
        if room["state"] != "running":
            break
        time.sleep(0.05)
    assert room["state"] == "done", room
    assert [(t["member"], t["round"], t["supported"]) for t in room["turns"]] == [
        ("hana", 1, False),
        ("marco", 1, True),
        ("hana", 2, False),
        ("marco", 2, True),
    ]
    proposal = room["proposals"][0]
    assert proposal["why"] is None  # its only quote did not check out
    assert [a["member"] for a in proposal["assignments"]] == ["marco"]
    assert proposal["assignments"][0]["module"] == "simulator"

    adopted = client.post(
        f"{base}/converge/{room['id']}/adopt", json={"proposal": 0, "member": "okafor"}
    )
    assert adopted.status_code == 200, adopted.text
    assert "Prof. Okafor adopted in the CONVERGE Room: Calibrate first." in adopted.json()["line"]
    for _ in range(100):
        if client.get(f"{base}/update").json()["state"] != "running":
            break
        time.sleep(0.05)
    # The fake model's own analyses are empty, so read the picture the gold labels draw.
    state = client.get(f"{base}/state", params={"origin": "gold"}).json()
    marco = [i for i in state["items"] if i["kind"] == "action" and i["member"] == "marco"]
    assert marco and marco[-1]["quote"].endswith("Next for Marco Rossi: identify brass")
    simulator = next(m for m in state["modules"] if m["id"] == "simulator")
    assert ("marco", "identify brass") in [
        (c["member"], c["approach"]) for c in simulator["contributions"]
    ]


def test_merge_roster_adds_only_new_people() -> None:
    known = [
        Member(id="daniel", name="Daniel Weber", aliases=["D"]),
        Member(id="sora", name="Sora Ito"),
    ]
    found = [
        Member(id="dan", name="Dan", aliases=["Daniel Weber"]),  # Daniel again
        Member(id="daniel", name="Daniel Park"),  # a second Daniel
        Member(id="lena", name="Lena Fox"),
    ]
    merged = pipeline.merge_roster(known, found)
    assert [m.id for m in merged] == ["daniel", "sora", "daniel-2", "lena"]
    assert merged[2].name == "Daniel Park"


def test_notion_page_lookup_finds_session_and_sub_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "lookup.db")
    linked = "a" * 32
    with store.connect() as conn:
        store.save_workspace(
            conn, Workspace(id="team", name="Team", members=[], notion_page_id=linked)
        )
    monkeypatch.setattr(
        "app.ingest.notion_ancestors", lambda page: [linked] if page == "b" * 32 else ["c" * 32]
    )
    client = TestClient(app)
    assert client.get(f"/api/notion/{linked}").json() == {"workspace": "team"}
    assert client.get(f"/api/notion/{'b' * 32}").json() == {"workspace": "team"}
    assert client.get(f"/api/notion/{'d' * 32}").json() == {"workspace": None}
