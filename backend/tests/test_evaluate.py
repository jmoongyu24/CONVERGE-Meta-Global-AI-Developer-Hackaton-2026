"""Tests for the evaluation against gold labels (no network, no model calls)."""

from pathlib import Path

import pytest

from app import store
from app.evaluate import b_cubed, evaluate, match_ideas, table
from app.ingest import DEMO_DIR, load_demo, load_gold, load_markdown_folder
from app.pipeline import build_state


def test_b_cubed_on_a_small_example() -> None:
    gold = {("s", 1, "a"): "X", ("s", 2, "b"): "X", ("s", 3, "c"): "Y"}
    model = {("s", 1, "a"): "P", ("s", 2, "b"): "Q", ("s", 3, "c"): "Q"}
    scores = b_cubed(gold, model)
    assert round(scores["precision"], 3) == round(2 / 3, 3)
    assert round(scores["recall"], 3) == round(2 / 3, 3)


def test_gold_matches_its_own_ideas() -> None:
    folder = DEMO_DIR / "long-term-dependency"
    workspace, sources = load_markdown_folder(folder)
    state = build_state(workspace, sources, {a.source: a for a in load_gold(folder)})
    mapping = match_ideas(state, state)
    assert all(gold == model for gold, model in mapping.items())


def test_gold_scores_perfectly_against_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "eval.db")
    with store.connect() as conn:
        load_demo(conn)
        store.save_analyses(conn, "sim-to-real", "llm", load_gold(DEMO_DIR / "sim-to-real"))
    result = evaluate("sim-to-real")
    assert result["ideas"]["f1"] == 1.0 and result["edges"]["f1"] == 1.0
    assert result["status"] == 1.0 and result["closed_recall"] == 1.0
    assert result["coverage"] == 1.0 and result["stance"] == 1.0 and result["speaker"] == 1.0
    assert result["items"]["all"]["f1"] == 1.0
    assert result["verbatim"] is None  # no model calls were made
    assert "| Idea grouping, B³ F1 | 1.00 |" in table([result])
