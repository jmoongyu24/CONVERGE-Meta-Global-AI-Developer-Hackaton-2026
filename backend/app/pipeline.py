"""Replay per-source analyses into the team's idea lineage.

Each SourceAnalysis works like a commit: replaying them in time order rebuilds the idea
registry as it stood after any source, which is what the UI's time slider shows. The LLM
extraction (F2) will produce these analyses; until then the hand-labelled gold drives it.
"""

from collections import Counter, defaultdict

from app.models import (
    Digest,
    Edge,
    IdeaOp,
    IdeaVersion,
    IdeaView,
    LineageState,
    LocatedItem,
    LocatedMention,
    Source,
    SourceAnalysis,
    SourceMeta,
    StanceCell,
    StanceShift,
    Workspace,
)

DORMANT_AFTER = 3  # an active idea untouched for this many sources is shown as dormant
PARENT_COUNTS = {"new": range(0, 1), "branch": range(1, 2), "merge": range(2, 100)}


def locate(text: str, quote: str) -> int | None:
    """1-based line where `quote` starts in `text`, or None when it is not there."""
    position = text.find(quote) if quote.strip() else -1
    return None if position < 0 else text.count("\n", 0, position) + 1


def op_problem(op: IdeaOp, ideas: dict[str, IdeaView], text: str) -> str | None:
    """Why `op` cannot be applied to the registry, or None when it is valid."""
    if locate(text, op.quote) is None:
        return "quote not found"
    if op.op not in PARENT_COUNTS:
        return None if op.idea in ideas else "unknown idea"
    if op.idea in ideas:
        return "idea already exists"
    if not op.title:
        return "missing title"
    if len(op.parents) not in PARENT_COUNTS[op.op] or not all(p in ideas for p in op.parents):
        return f"bad parents {op.parents}"
    return None


def build_state(
    workspace: Workspace,
    sources: list[Source],
    analyses: dict[str, SourceAnalysis],
    as_of: str | None = None,
) -> LineageState:
    """The idea lineage after replaying every analysis up to and including source `as_of`."""
    order = [s.id for s in sources]
    as_of = as_of or order[-1]
    cutoff = order.index(as_of)
    members = {m.id for m in workspace.members}

    ideas: dict[str, IdeaView] = {}
    last_touched: dict[str, int] = {}  # idea id -> index of the latest source that touched it
    edges: list[Edge] = []
    mentions: list[LocatedMention] = []
    items: list[LocatedItem] = []
    positions: dict[tuple[str, str], LocatedMention] = {}  # (member, idea) -> latest stance
    issues: list[str] = []
    digest = Digest(source=as_of)

    for index, source in enumerate(sources[: cutoff + 1]):
        analysis = analyses.get(source.id) or SourceAnalysis(source=source.id)
        before = {key: m.stance for key, m in positions.items()}
        touched: set[str] = set()
        applied: list[IdeaOp] = []

        for op in analysis.ops:
            if problem := op_problem(op, ideas, source.text):
                issues.append(f"{source.id}: {op.op} {op.idea} dropped ({problem})")
                continue
            owner = op.owner if op.owner in members else None
            if op.owner and owner is None:
                issues.append(
                    f"{source.id}: {op.op} {op.idea} kept without unknown owner {op.owner!r}"
                )
            if op.op in PARENT_COUNTS:
                title, summary = op.title or op.idea, op.summary or ""
                ideas[op.idea] = IdeaView(
                    id=op.idea,
                    title=title,
                    summary=summary,
                    owner=owner,
                    status="active",
                    parents=op.parents,
                    born=source.id,
                    last_active=source.id,
                    versions=[IdeaVersion(source=source.id, title=title, summary=summary)],
                )
                edges += [
                    Edge(parent=p, child=op.idea, kind=op.op, source=source.id) for p in op.parents
                ]
                touched.update(op.parents)
            elif op.op == "update":
                idea = ideas[op.idea]
                idea.title = op.title or idea.title
                idea.summary = op.summary or idea.summary
                idea.owner = owner or idea.owner
                idea.status = "active"
                idea.versions.append(
                    IdeaVersion(source=source.id, title=idea.title, summary=idea.summary)
                )
            else:
                ideas[op.idea].status = "parked" if op.op == "park" else "dropped"
            touched.add(op.idea)
            applied.append(op)

        for mention in analysis.mentions:
            line = locate(source.text, mention.quote)
            if line is None or mention.member not in members or mention.idea not in ideas:
                issues.append(
                    f"{source.id}: mention dropped (bad quote or reference): {mention.quote!r}"
                )
                continue
            located = LocatedMention(**mention.model_dump(), source=source.id, line=line)
            mentions.append(located)
            touched.add(mention.idea)
            key = (mention.member, mention.idea)
            # Reporting results is not a position, so "inform" never overrides a real stance.
            if mention.stance != "inform" or key not in positions:
                positions[key] = located

        for item in analysis.items:
            line = locate(source.text, item.quote)
            if line is None:
                issues.append(f"{source.id}: item dropped (quote not found): {item.quote!r}")
                continue
            member = item.member if item.member in members else None
            idea = item.idea if item.idea in ideas else None
            if (item.member and member is None) or (item.idea and idea is None):
                issues.append(f"{source.id}: item kept without unknown reference: {item.quote!r}")
            items.append(
                LocatedItem(
                    kind=item.kind,
                    quote=item.quote,
                    member=member,
                    idea=idea,
                    source=source.id,
                    line=line,
                )
            )

        for idea_id in touched:
            last_touched[idea_id] = index
            ideas[idea_id].last_active = source.id

        if index == cutoff:
            digest = Digest(
                source=source.id,
                ops=applied,
                shifts=[
                    StanceShift(member=key[0], idea=key[1], before=before[key], after=latest.stance)
                    for key, latest in positions.items()
                    # changed in this source, and the member had a position before it
                    if latest.source == source.id
                    and before.get(key, latest.stance) != latest.stance
                ],
                items=[item for item in items if item.source == source.id],
            )

    for idea in ideas.values():
        if idea.status == "active" and cutoff - last_touched[idea.id] >= DORMANT_AFTER:
            idea.status = "dormant"

    tallies: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for mention in mentions:
        tallies[(mention.member, mention.idea)][mention.stance] += 1

    return LineageState(
        workspace=workspace,
        sources=[SourceMeta(**s.model_dump(exclude={"text"})) for s in sources],
        as_of=as_of,
        ideas=list(ideas.values()),
        edges=edges,
        mentions=mentions,
        items=items,
        stances=[
            StanceCell(
                member=member,
                idea=idea,
                latest=latest.stance,
                source=latest.source,
                counts=dict(tallies[(member, idea)]),
            )
            for (member, idea), latest in positions.items()
        ],
        digest=digest,
        issues=issues,
    )
