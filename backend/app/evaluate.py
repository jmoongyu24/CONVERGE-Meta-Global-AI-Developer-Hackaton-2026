"""Measure the model's analyses against the hand-labelled gold, scenario by scenario.

The model names ideas in its own words, so its ideas are first matched to gold ideas through
the statements they share: a statement is (source, line, member), the speaker's line in a
note. Everything else is compared through that matching.

Usage, from backend/:
    uv run python -m app.evaluate            # every demo workspace, as a Markdown table
    uv run python -m app.evaluate --rooms    # also run one Converge Room per workspace
"""

import argparse
import json
from collections import Counter, defaultdict
from statistics import fmean as mean

from app import converge, ingest, store
from app.models import (
    ConvergeSession,
    Extraction,
    LineageState,
    Source,
    SourceAnalysis,
)
from app.pipeline import build_state

Statement = tuple[str, int, str]  # (source, line, member)
FOR = {"propose", "support", "refine"}


def polarity(stance: str) -> str:
    return "for" if stance in FOR else "against" if stance == "challenge" else "neutral"


def f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def prf(hits: int, predicted: int, expected: int) -> dict[str, float]:
    precision = hits / predicted if predicted else 0.0
    recall = hits / expected if expected else 0.0
    return {"precision": precision, "recall": recall, "f1": f1(precision, recall)}


def statements(state: LineageState) -> dict[Statement, str]:
    """Each speaker's line and the idea it is about (the first one, when a line names two)."""
    found: dict[Statement, str] = {}
    for m in state.mentions:
        found.setdefault((m.source, m.line, m.member), m.idea)
    return found


def b_cubed(gold: dict[Statement, str], model: dict[Statement, str]) -> dict[str, float]:
    """How well the model groups statements into ideas, over the statements both labelled."""
    shared = gold.keys() & model.keys()
    if not shared:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    gold_groups: dict[str, set] = defaultdict(set)
    model_groups: dict[str, set] = defaultdict(set)
    for statement in shared:
        gold_groups[gold[statement]].add(statement)
        model_groups[model[statement]].add(statement)
    both = [len(gold_groups[gold[s]] & model_groups[model[s]]) for s in shared]
    precision = mean(n / len(model_groups[model[s]]) for n, s in zip(both, shared, strict=True))
    recall = mean(n / len(gold_groups[gold[s]]) for n, s in zip(both, shared, strict=True))
    return {"precision": precision, "recall": recall, "f1": f1(precision, recall)}


def match_ideas(gold: LineageState, model: LineageState) -> dict[str, str]:
    """Gold idea -> the model idea that shares the most of its statements."""
    votes: Counter = Counter()
    gold_statements, model_statements = statements(gold), statements(model)
    for statement in gold_statements.keys() & model_statements.keys():
        votes[(gold_statements[statement], model_statements[statement])] += 1
    best: dict[str, tuple[int, str]] = {}
    for (gold_idea, model_idea), count in votes.items():
        if count > best.get(gold_idea, (0, ""))[0]:
            best[gold_idea] = (count, model_idea)
    return {gold_idea: model_idea for gold_idea, (_, model_idea) in best.items()}


def lineage_scores(gold: LineageState, model: LineageState, mapping: dict[str, str]) -> dict:
    gold_edges = {(e.parent, e.child): e.kind for e in gold.edges}
    model_edges = {(e.parent, e.child): e.kind for e in model.edges}
    translated = {
        (mapping[parent], mapping[child]): kind
        for (parent, child), kind in gold_edges.items()
        if parent in mapping and child in mapping
    }
    hits = [edge for edge in translated if edge in model_edges]
    same_kind = sum(translated[edge] == model_edges[edge] for edge in hits)
    model_status = {i.id: i.status for i in model.ideas}
    compared = [(i.status, model_status[mapping[i.id]]) for i in gold.ideas if i.id in mapping]
    closed = {"parked", "dropped"}
    gold_closed = [i for i in gold.ideas if i.status in closed]
    found_closed = [i for i in gold_closed if model_status.get(mapping.get(i.id, "")) in closed]
    return {
        "edges": prf(len(hits), len(model_edges), len(gold_edges)),
        "edge_kind": same_kind / len(hits) if hits else None,
        "status": mean(a == b for a, b in compared) if compared else None,
        "closed_recall": len(found_closed) / len(gold_closed) if gold_closed else None,
    }


def stance_scores(gold: LineageState, model: LineageState, mapping: dict[str, str]) -> dict:
    said: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for m in model.mentions:
        said[(m.source, m.member, m.idea)].add(m.stance)
    matched = exact = same_side = 0
    for m in gold.mentions:
        stances = said.get((m.source, m.member, mapping.get(m.idea, "")))
        if not stances:
            continue
        matched += 1
        exact += m.stance in stances
        same_side += polarity(m.stance) in {polarity(s) for s in stances}
    # Speaker attribution: where gold has a statement on a line, does the model name a speaker
    # gold also names there?
    gold_speakers: dict[tuple[str, int], set[str]] = defaultdict(set)
    for m in gold.mentions:
        gold_speakers[(m.source, m.line)].add(m.member)
    located = [m for m in model.mentions if (m.source, m.line) in gold_speakers]
    return {
        "coverage": matched / len(gold.mentions) if gold.mentions else None,
        "stance": exact / matched if matched else None,
        "side": same_side / matched if matched else None,
        "speaker": mean(m.member in gold_speakers[(m.source, m.line)] for m in located)
        if located
        else None,
    }


def item_scores(gold: LineageState, model: LineageState) -> dict:
    def lines(state: LineageState, kind: str | None) -> set[tuple[str, int]]:
        return {(i.source, i.line) for i in state.items if kind in (None, i.kind)}

    scores = {}
    for kind in ["decision", "action", "question", "consensus", None]:
        expected, predicted = lines(gold, kind), lines(model, kind)
        scores[kind or "all"] = prf(len(expected & predicted), len(predicted), len(expected))
    return scores


def quote_scores(workspace_id: str, sources: list[Source], model: LineageState) -> dict:
    """How often the model copied quotes exactly, and how many survived the check after repair."""
    texts = {s.id: s.text for s in sources}
    with store.connect() as conn:
        rows = conn.execute(
            """SELECT purpose, response, tokens_in, tokens_out, cost, seconds FROM llm_calls
               WHERE purpose LIKE ? ORDER BY created_at""",
            (f"% {workspace_id} %",),
        ).fetchall()
    latest = {row["purpose"]: row for row in rows}  # the newest response per call
    quotes = []
    for purpose, row in latest.items():
        if purpose.startswith("extract "):
            for analysis in Extraction.model_validate_json(row["response"]).analyses:
                text = texts.get(analysis.source, "")
                quotes += [entry.quote in text for entry in entries(analysis)]
    dropped = sum("dropped" in issue for issue in model.issues)
    spent = [row for purpose, row in latest.items() if purpose.split()[0] in ("extract", "board")]
    notes = len([s for s in sources if s.kind != "session"]) or 1
    return {
        "verbatim": mean(quotes) if quotes else None,
        "kept": 1 - dropped / len(quotes) if quotes else None,
        "cost_per_note": sum(row["cost"] for row in spent) / notes,
        "seconds_per_note": sum(row["seconds"] for row in spent) / notes,
        "tokens_per_note": sum(row["tokens_in"] + row["tokens_out"] for row in spent) / notes,
    }


def entries(analysis: SourceAnalysis) -> list:
    return [
        *analysis.ops,
        *analysis.mentions,
        *analysis.items,
        *analysis.modules,
        *(c for m in analysis.modules for c in m.contributions),
    ]


def room_scores(workspace_id: str, model: LineageState, sources: list[Source], run: bool) -> dict:
    """Share of Converge Room delegate turns backed by a quote that checks out."""
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT session FROM converge_sessions WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()
        board = store.get_board(conn, workspace_id, "llm")
    sessions = [ConvergeSession.model_validate_json(row["session"]) for row in rows]
    if run and not sessions and board and board.contests:
        contest = board.contests[0]
        session = ConvergeSession(
            id=f"eval-{workspace_id}",
            workspace=workspace_id,
            question=contest.question,
            members=sorted({m for side in contest.sides for m in side.members}),
            module=contest.module,
            state="running",
        )
        converge.sessions[session.id] = session
        converge.converge(session.id, model, sources)
        with store.connect() as conn:
            store.save_converge(conn, converge.sessions[session.id])
        sessions = [converge.sessions[session.id]]
    turns = [t for s in sessions if s.state == "done" for t in s.turns]
    return {
        "rooms": len(sessions),
        "supported": mean(t.supported for t in turns) if turns else None,
        "room_cost": mean(s.cost for s in sessions) if sessions else None,
    }


def evaluate(workspace_id: str, rooms: bool = False) -> dict:
    with store.connect() as conn:
        workspace = store.get_workspace(conn, workspace_id)
        sources = store.list_sources(conn, workspace_id)
        gold_analyses = store.list_analyses(conn, workspace_id, "gold")
        model_analyses = store.list_analyses(conn, workspace_id, "llm")
    if workspace is None or not gold_analyses or not model_analyses:
        raise ValueError(f"{workspace_id}: needs both gold and model analyses")
    notes = [s for s in sources if s.kind != "session"]
    gold = build_state(workspace, notes, gold_analyses)
    model = build_state(workspace, notes, model_analyses)
    mapping = match_ideas(gold, model)
    return {
        "workspace": workspace_id,
        "notes": len(notes),
        "gold_ideas": len(gold.ideas),
        "model_ideas": len(model.ideas),
        "ideas": b_cubed(statements(gold), statements(model)),
        **lineage_scores(gold, model, mapping),
        **stance_scores(gold, model, mapping),
        "items": item_scores(gold, model),
        **quote_scores(workspace_id, notes, model),
        **room_scores(workspace_id, model, notes, rooms),
    }


ROWS = [
    ("Ideas found (gold → model)", lambda r: f"{r['gold_ideas']} → {r['model_ideas']}"),
    ("Idea grouping, B³ F1", lambda r: r["ideas"]["f1"]),
    ("Lineage edges (branch/merge), F1", lambda r: r["edges"]["f1"]),
    ("Parked/dropped ideas caught", lambda r: r["closed_recall"]),
    ("Final idea status, accuracy", lambda r: r["status"]),
    ("Gold stances the model also found", lambda r: r["coverage"]),
    ("Stance, exact", lambda r: r["stance"]),
    ("Stance side (for / against / neutral)", lambda r: r["side"]),
    ("Speaker attribution", lambda r: r["speaker"]),
    ("Decisions, F1", lambda r: r["items"]["decision"]["f1"]),
    ("Action items, F1", lambda r: r["items"]["action"]["f1"]),
    ("Open questions, F1", lambda r: r["items"]["question"]["f1"]),
    ("Quotes copied verbatim", lambda r: r["verbatim"]),
    ("Claims kept after the quote check", lambda r: r["kept"]),
    ("Converge Room turns backed by a quote", lambda r: r["supported"]),
    ("Cost per meeting (USD)", lambda r: f"${r['cost_per_note']:.4f}"),
    ("Model time per meeting (s)", lambda r: f"{r['seconds_per_note']:.1f}"),
]


def cell(value: object) -> str:
    if value is None:
        return "–"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def table(results: list[dict]) -> str:
    """Rows are metrics, columns are workspaces, with the mean of numeric rows at the end."""
    header = ["Metric", *(r["workspace"] for r in results), "Mean"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for label, read in ROWS:
        values = [read(r) for r in results]
        numbers = [v for v in values if isinstance(v, float)]
        average = mean(numbers) if len(numbers) == len(values) and numbers else None
        lines.append(f"| {label} | " + " | ".join(map(cell, [*values, average])) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare model analyses with the gold labels.")
    parser.add_argument("--rooms", action="store_true", help="run one Converge Room each")
    parser.add_argument("--json", action="store_true", help="print raw numbers as JSON")
    args = parser.parse_args()
    workspaces = [folder.name for folder in ingest.demo_folders()]
    results = [evaluate(workspace, args.rooms) for workspace in workspaces]
    print(json.dumps(results, indent=2) if args.json else table(results))


if __name__ == "__main__":
    main()
