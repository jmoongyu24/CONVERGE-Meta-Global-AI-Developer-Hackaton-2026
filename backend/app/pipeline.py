"""Read a team's notes with the model, check every claim against the notes, and replay the
results into the team's idea lineage, architecture, and briefing.

Each SourceAnalysis works like a commit: replaying them in time order rebuilds the idea
registry and the architecture as they stood after any source, which is what the UI's time
slider shows. Muse Spark writes these analyses from the notes (images included); the
hand-labelled gold has the same shape and is the yardstick. Edits made in the UI become
analyses of a session note. The briefing (Board) is written last, from the replayed state
and the latest notes.
"""

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from difflib import SequenceMatcher

from app import ingest, llm, store
from app.models import (
    Asset,
    Board,
    BoardState,
    Claim,
    Contest,
    ContestSide,
    Digest,
    Edge,
    EditRequest,
    Evidence,
    Extraction,
    IdeaOp,
    IdeaVersion,
    IdeaView,
    Item,
    LineageState,
    Located,
    LocatedItem,
    LocatedMention,
    LocatedModuleOp,
    Member,
    Mention,
    ModuleContribution,
    ModuleOp,
    ModuleView,
    PersonFocus,
    Roster,
    Source,
    SourceAnalysis,
    SourceMeta,
    StanceCell,
    StanceShift,
    Step,
    Workspace,
)

DORMANT_AFTER = 3  # an active idea untouched for this many sources is shown as dormant
PARENT_COUNTS = {"new": range(0, 1), "branch": range(1, 2), "merge": range(2, 100)}
BATCH_CHARS = 30_000  # notes per extraction call: small enough to finish in about a minute
RECENT_NOTES = 3  # the briefing reads the latest notes in full, the rest through the state

EXTRACT_PROMPT = """You turn a research team's notes into a structured history: the ideas they \
argue about, who stands where, and the system they are building. A program replays your answer \
note by note, checks every quote against the note, and drops anything it cannot find there.

Return one analysis per note, in the order given, with:

ops: changes to the idea registry.
- new: a research direction someone argues for (a method, model, or approach; not a task or \
a result) that does not grow out of an existing idea. The team's current baseline counts as \
an idea too (e.g. "Vanilla RNN baseline"). No parents.
- branch: a new idea that grows out of exactly one existing idea: a variant, successor, \
extension, or replacement of it (a lighter or faster version, the next step in its line). \
Prefer branch over new whenever a proposal builds on or replaces a registry idea.
- merge: a new idea that combines two or more existing ideas (e.g. "GRU encoder + soft \
attention" from "GRU" and "soft attention").
- update: an existing idea was refined; give title or summary only when they changed.
- park: put on hold. drop: abandoned.
Reuse registry ids for existing ideas; new ideas get short kebab-case ids. owner = the member \
who proposed it.

mentions: each member's stance on an idea in this note:
- propose: puts the idea forward; also the idea's own champion arguing for it again later.
- support: someone other than its champion argues for it.
- refine: proposes a change, extension, or condition for the idea.
- question: asks about it, or doubts it in the form of a question, without arguing against it.
- challenge: argues against the idea itself, including naming a limitation as an objection \
(e.g. "both are still sequential").
- inform: reports results, numbers, or facts about it, even favorable ones, without arguing \
for or against.

items:
- decision: something this meeting settles: an approach chosen, an option kept or dropped, a \
rule or constraint the team adopts, or a call the PI makes. Not a plan to decide later.
- action: who will do what next (member = who).
- question: an open question nobody answered.
- consensus: a point the whole team agreed on.
Link the idea and module an item is about.

modules: the architecture of what the team builds: components, layers, stages, tools, data, \
and benchmarks, and how work flows between them (inputs = the modules that feed this one). \
Every idea that changes what the team builds appears here: as its own module when it adds \
a part (a residual model added on top of a simulator), or as a contribution to an existing \
module when it is another way to build that part (two different encoders are one "encoder" \
module with two contributions). add a module when it first appears; update its title, note, \
status (adopted once the team's main system uses it, proposed while it is discussed), \
inputs, or people when they change; remove it when the team takes it out. Reuse registry \
module ids. A project usually has 4 to 12 modules; fold small details into a module's note. \
Wire the modules: every module except the starting ones (tasks, data, simulators) lists its \
inputs, so the diagram reads from where work starts to where results come out.

contributions (inside a module op): each member who proposes, builds, runs, or champions \
an approach for that module in this note: approach in one plain line, plus the idea it \
belongs to. Members pursuing different ideas on one module are competing; members working \
on the same idea are collaborating. Use approach "" when someone stops working on it.

Example. Note: "Ana: swap our CNN encoder for a transformer. Ben: keep the CNN, add \
attention pooling. Chen will rebuild the data loader for longer clips." Modules: update \
"encoder" with contributions ana "replace the CNN with a transformer" (idea \
transformer-encoder) and ben "keep the CNN, add attention pooling" (idea attention-pooling); \
update "data-loader" with contribution chen "rebuild it for longer clips".

Every entry, and every contribution, has a quote: words copied exactly, character for \
character, from that note (for something shown in an image, its caption line), 4 to 30 \
words, no ellipses. Leave out anything you cannot quote.

Use member ids from the roster ("I" and "me" are the note-taker). Use only idea and module \
ids that are in the registry or that you introduced earlier in this answer. Report what the \
notes say; do not guess at intentions they do not state. Write titles, summaries, notes, and \
approaches in the language of the notes."""

BOARD_PROMPT = """You write the briefing a research team sees beside its notes. Someone who \
has never seen the team must understand it in five seconds, so every line is short, plain, \
and concrete. Weigh the latest notes most; older notes count only where they still hold.

Return:
- headline: the team's situation now in one sentence of at most 14 words, about the first \
(most urgent) contest when there is one: what is still open and when or how it gets \
decided. Name only what that contest asks; what else its decision settles goes in its \
stakes, not in the headline. Never word an open question as already decided (write "The \
main claim is still open; the team decides it on June 3.", not "Main claim decided on June \
3."). With no contests, say what was just settled or what the team does next.
- problem: the research problem, in one sentence of at most 16 words.
- status: where things stand, in at most two sentences.
- agreed: up to three points of substance the whole team has settled and that still hold \
(a method, metric, evaluation, or scope choice), newest first, each at most 10 words and \
worded as the settled point itself. Leave out plans to decide something later; those \
belong in headline and contests.
- people: one entry per roster member, in roster order, with member = their roster id. \
idea: the approach or position they push now, at most 10 words. next: up to two next \
steps, most important first, each with text (at most 10 words, starting with a verb), why \
(one natural sentence of at most 12 words on how it helps the team, e.g. "Helps the team \
choose one main research claim."), due (when it is due as a natural phrase, e.g. "Before \
the November 4 meeting"; "" when the notes give no date), and contest (the index in \
contests of the question it helps settle, or null). modules: ids of the modules they work \
on.
- contests: open disagreements that still need a decision, most important first, at most \
three: the question in at most 12 words; stakes, one sentence of at most 12 words on what \
the decision settles for the team (e.g. "Decides the paper's main contribution and lead \
author."); the module where the competing approaches meet (if any); each side's member \
ids and position, one line of at most 12 words saying what that side wants and why, so a \
newcomer sees how the sides differ; and decide_by (at most 6 words) when the notes say \
when or how it will be decided.

Write full, plain sentences a newcomer can read aloud: no abbreviations or shorthand, \
dates with the month spelled out ("November 4", not "11/04"), and other numbers as digits \
("4,000-token documents").
Every claim, agreed point, step, and contest needs evidence: one to three quotes copied \
exactly, character for character, from the notes, each with its source id; prefer the \
latest notes. Leave out what you cannot support. Write in the language of the notes."""

ROSTER_PROMPT = """List the people who take part in these research meeting notes. For each: \
id (first name in lowercase ASCII), name as written, role if the notes state it (PI, PhD \
student, MS student, ...), and aliases (other ways the notes refer to them, such as initials \
or "Prof"). Leave out people who only appear as authors of cited papers. When a roster so \
far is given, list only people who are not on it yet, and an empty list if there are none."""


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


def module_problem(op: ModuleOp, modules: dict[str, ModuleView], text: str) -> str | None:
    """Why `op` cannot be applied to the architecture, or None when it is valid."""
    if locate(text, op.quote) is None:
        return "quote not found"
    live = op.module in modules and modules[op.module].status != "removed"
    if op.op == "add":
        if live:
            return "module already exists"
        return None if op.title else "missing title"
    return None if live else "unknown module"


def inside(modules: dict[str, ModuleView], module_id: str, group_id: str | None) -> bool:
    """Whether `group_id` is `module_id` itself or sits somewhere inside it."""
    seen: set[str] = set()
    while group_id and group_id not in seen:
        if group_id == module_id:
            return True
        seen.add(group_id)
        group_id = modules[group_id].group if group_id in modules else None
    return False


def remove_module(modules: dict[str, ModuleView], module_id: str, source_id: str) -> None:
    """Remove a module together with everything inside it, and unplug it from the rest."""
    gone = {module_id}
    while (
        extra := {m.id for m in modules.values() if m.group in gone and m.status != "removed"}
        - gone
    ):
        gone |= extra
    for module in modules.values():
        if module.id in gone:
            module.status, module.changed = "removed", source_id
        else:
            module.inputs = [i for i in module.inputs if i not in gone]


def apply_module_op(
    op: LocatedModuleOp,
    modules: dict[str, ModuleView],
    members: set[str],
    ideas: set[str],
    contributions: list[ModuleContribution],
) -> list[str]:
    """Apply a valid module op with its checked contributions. References that do not resolve
    are left out and reported."""
    problems: list[str] = []
    live = {m.id for m in modules.values() if m.status != "removed"} - {op.module}

    def resolve(value: str | None, known: set[str], label: str) -> str | None:
        if value and value not in known:
            problems.append(f"unknown {label} {value!r}")
            return None
        return value

    owner = resolve(op.owner, members, "owner")
    idea = resolve(op.idea, ideas, "idea")
    group = resolve(op.group, live, "group")
    if group and inside(modules, op.module, group):
        problems.append(f"group {group!r} would contain itself")
        group = None
    inputs = None if op.inputs is None else [i for i in op.inputs if i in live]
    if op.inputs and inputs is not None and len(inputs) < len(op.inputs):
        problems.append(f"unknown inputs {sorted(set(op.inputs) - set(inputs))}")

    if op.op == "add":
        modules[op.module] = ModuleView(
            id=op.module,
            title=op.title or op.module,
            note=op.note or "",
            owner=owner or None,
            status=op.status or "proposed",
            inputs=inputs or [],
            group=group or None,
            idea=idea or None,
            born=op.source,
            changed=op.source,
            history=[op],
        )
        contribute(modules[op.module], contributions)
        return problems
    module = modules[op.module]
    module.changed = op.source
    module.history.append(op)
    if op.op == "remove":
        remove_module(modules, op.module, op.source)
        return problems
    module.title = op.title or module.title
    module.note = module.note if op.note is None else op.note
    module.owner = module.owner if owner is None else owner or None
    module.status = op.status or module.status
    module.inputs = module.inputs if inputs is None else inputs
    module.group = module.group if group is None else group or None
    module.idea = module.idea if idea is None else idea or None
    contribute(module, contributions)
    return problems


def contribute(module: ModuleView, contributions: list[ModuleContribution]) -> None:
    """Record what each member now does on the module; an empty approach means they stopped."""
    for contribution in contributions:
        kept = [c for c in module.contributions if c.member != contribution.member]
        module.contributions = [*kept, contribution] if contribution.approach else kept


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
    modules: dict[str, ModuleView] = {}
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
                idea.closed = None
                idea.versions.append(
                    IdeaVersion(source=source.id, title=idea.title, summary=idea.summary)
                )
            else:
                ideas[op.idea].status = "parked" if op.op == "park" else "dropped"
                ideas[op.idea].closed = Located(
                    source=source.id, line=locate(source.text, op.quote) or 0, quote=op.quote
                )
            touched.add(op.idea)
            applied.append(op)

        applied_modules: list[LocatedModuleOp] = []
        for module_op in analysis.modules:
            if problem := module_problem(module_op, modules, source.text):
                issues.append(
                    f"{source.id}: {module_op.op} module {module_op.module} dropped ({problem})"
                )
                continue
            located_op = LocatedModuleOp(
                **module_op.model_dump(),
                source=source.id,
                line=locate(source.text, module_op.quote) or 0,
            )
            contributions = []
            for c in module_op.contributions:
                line = locate(source.text, c.quote)
                if line is None or c.member not in members:
                    issues.append(
                        f"{source.id}: contribution dropped (bad quote or member): {c.quote!r}"
                    )
                    continue
                contributions.append(
                    ModuleContribution(
                        member=c.member,
                        approach=c.approach.strip(),
                        idea=c.idea if c.idea in ideas else None,
                        source=source.id,
                        line=line,
                        quote=c.quote,
                    )
                )
            for problem in apply_module_op(located_op, modules, members, set(ideas), contributions):
                issues.append(
                    f"{source.id}: {module_op.op} module {module_op.module} kept without {problem}"
                )
            applied_modules.append(located_op)

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
            module = item.module if item.module in modules else None
            if (
                (item.member and member is None)
                or (item.idea and idea is None)
                or (item.module and module is None)
            ):
                issues.append(f"{source.id}: item kept without unknown reference: {item.quote!r}")
            items.append(
                LocatedItem(
                    kind=item.kind,
                    quote=item.quote,
                    member=member,
                    idea=idea,
                    module=module,
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
                modules=applied_modules,
            )

    for idea in ideas.values():
        if idea.status == "active" and cutoff - last_touched[idea.id] >= DORMANT_AFTER:
            idea.status = "dormant"
    for module in modules.values():
        module.contested = len({c.idea for c in module.contributions if c.idea}) > 1

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
        modules=list(modules.values()),
        digest=digest,
        issues=issues,
    )


# --- Edits made in the UI ------------------------------------------------------


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "module"


def compose_edit(state: LineageState, edit: EditRequest, clock: str) -> tuple[str, SourceAnalysis]:
    """The session-note line an edit writes, and the analysis entries it stands for.

    The line is the quote of every entry it produces, so an edit is checked and replayed like
    anything said in a meeting. Raises ValueError for an edit that does not fit the state.
    """
    members = {m.id: m.name for m in state.workspace.members}
    modules = {m.id: m for m in state.modules if m.status != "removed"}
    ideas = {i.id: i.title for i in state.ideas}
    if edit.member not in members:
        raise ValueError(f"unknown member {edit.member!r}")
    for value, known, label in [
        (edit.owner, members, "owner"),
        (edit.group, modules, "group"),
        (edit.idea, ideas, "idea"),
    ]:
        if value and value not in known:
            raise ValueError(f"unknown {label} {value!r}")
    if unknown := set(edit.inputs or []) - set(modules):
        raise ValueError(f"unknown inputs {sorted(unknown)}")

    def called(module_id: str) -> str:
        return f'"{modules[module_id].title}"'

    name = members[edit.member]
    if edit.action == "add":
        title = (edit.title or "").strip()
        if not title:
            raise ValueError("a new module needs a title")
        module_id, taken = slug(title), {m.id for m in state.modules}
        module_id = next(
            candidate
            for n in range(1, len(taken) + 2)
            if (candidate := module_id if n == 1 else f"{module_id}-{n}") not in taken
        )
        parts = [f'{name} proposed a new module "{title}"']
        if edit.inputs:
            parts.append("fed by " + ", ".join(called(i) for i in edit.inputs))
        if edit.group:
            parts.append(f"inside {called(edit.group)}")
        if edit.owner:
            parts.append(f"owned by {members[edit.owner]}")
        sentence = ", ".join(parts)
    else:
        if edit.module not in modules:
            raise ValueError(f"unknown module {edit.module!r}")
        module_id, target = edit.module, called(edit.module)
        if edit.action == "update":
            changes = []
            if edit.owner is not None:
                changes.append(
                    f"unassigned {target}"
                    if not edit.owner
                    else f"took ownership of {target}"
                    if edit.owner == edit.member
                    else f"assigned {target} to {members[edit.owner]}"
                )
            if edit.status:
                changes.append(f"marked {target} as {edit.status}")
            if edit.title and edit.title.strip() != modules[module_id].title:
                changes.append(f'renamed {target} to "{edit.title.strip()}"')
            if edit.inputs is not None:
                sources = ", ".join(called(i) for i in edit.inputs) or "nothing"
                changes.append(f"rewired {target} to take input from {sources}")
            if edit.group is not None:
                changes.append(
                    f"moved {target} into {called(edit.group)}"
                    if edit.group
                    else f"moved {target} out of its group"
                )
            if edit.idea is not None:
                changes.append(
                    f'linked {target} to the idea "{ideas[edit.idea]}"'
                    if edit.idea
                    else f"unlinked {target} from its idea"
                )
            if edit.note is not None:
                changes.append(f"described {target}")
            if not changes:
                raise ValueError("the edit changes nothing")
            sentence = f"{name} " + "; ".join(changes)
        elif edit.action == "remove":
            sentence = f"{name} removed {target} from the architecture"
        else:
            if edit.action in ("support", "challenge") and not modules[module_id].idea:
                raise ValueError("link the module to an idea before taking a position on it")
            if edit.action in ("question", "action") and not edit.text.strip():
                raise ValueError(f"a {edit.action} needs some text")
            verb = {
                "question": "asked about",
                "action": "will work on",
                "support": "supports",
                "challenge": "challenges",
            }[edit.action]
            sentence = f"{name} {verb} {target}"

    text = " ".join(edit.text.split())
    line = f"{clock} {sentence}: {text}" if text else f"{clock} {sentence}."
    analysis = SourceAnalysis(source="")
    if edit.action in ("add", "update", "remove"):
        analysis.modules.append(
            ModuleOp(
                op=edit.action,
                module=module_id,
                title=edit.title.strip() if edit.title else None,
                note=edit.note,
                owner=edit.owner,
                status=edit.status or ("proposed" if edit.action == "add" else None),
                inputs=edit.inputs,
                group=edit.group,
                idea=edit.idea,
                quote=line,
            )
        )
    elif edit.action in ("question", "action"):
        analysis.items.append(
            Item(
                kind=edit.action,
                quote=line,
                member=edit.member,
                idea=modules[module_id].idea,
                module=module_id,
            )
        )
    else:
        analysis.mentions.append(
            Mention(
                idea=modules[module_id].idea or "",
                member=edit.member,
                stance=edit.action,
                quote=line,
            )
        )
    return line, analysis


# --- Checking quotes -----------------------------------------------------------

FOLD = str.maketrans(
    {
        "\u2019": "'",
        "\u2018": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)
SENTENCE_END = re.compile(r"(?<=[.;!?])\s+")


def folded(text: str) -> str:
    return " ".join(text.translate(FOLD).replace("**", "").split()).lower()


def repair_quote(text: str, quote: str) -> str | None:
    """`quote` when it occurs in `text`; otherwise the sentence of `text` it was meant to copy
    (the same words up to typography, or at least 85% alike), or None."""
    if not quote.strip():
        return None
    if quote in text:
        return quote
    target = folded(quote)
    # Sentences split at whitespace, so each one is a verbatim slice of the text.
    pieces = [
        piece
        for line in text.splitlines()
        for piece in SENTENCE_END.split(line.strip())
        if piece.strip()
    ]
    for piece in pieces:
        if target in folded(piece):
            return piece
    score, best = max(
        ((SequenceMatcher(None, folded(piece), target).ratio(), piece) for piece in pieces),
        default=(0.0, None),
    )
    return best if score >= 0.85 else None


def member_index(workspace: Workspace) -> dict[str, str]:
    """Every way the model may name a member (id, name, first name, alias) -> member id."""
    index: dict[str, str] = {}
    for member in workspace.members:
        first = member.name.removeprefix("Prof. ").split()[0] if member.name else member.id
        for name in [member.id, member.name, first, *member.aliases]:
            index.setdefault(name.lower(), member.id)
    return index


def repair_analysis(analysis: SourceAnalysis, text: str, members: dict[str, str]) -> None:
    """Point model quotes at the exact note text and model names at member ids, in place.
    Whatever still does not check out is dropped later by the replay, with an issue."""
    entries = [
        *analysis.ops,
        *analysis.mentions,
        *analysis.items,
        *analysis.modules,
        *(c for m in analysis.modules for c in m.contributions),
    ]
    for entry in entries:
        entry.quote = repair_quote(text, entry.quote) or entry.quote
    for entry in [*analysis.mentions, *(c for m in analysis.modules for c in m.contributions)]:
        entry.member = members.get(entry.member.lower(), entry.member)
    for entry in [*analysis.ops, *analysis.modules]:
        if entry.owner:
            entry.owner = members.get(entry.owner.lower(), entry.owner)
    for item in analysis.items:
        if item.member:
            item.member = members.get(item.member.lower(), item.member)


# --- Reading notes with the model (F2) -------------------------------------------


def roster_text(workspace: Workspace) -> str:
    return "\n".join(
        f"- {m.id}: {m.name}"
        + (f" ({m.role})" if m.role else "")
        + (f"; also called {', '.join(m.aliases)}" if m.aliases else "")
        for m in workspace.members
    )


def registry_text(state: LineageState | None) -> str:
    """The ideas and modules so far, so the model links new notes to them."""
    if state is None:
        return "(empty: these are the first notes)"
    lines = ["Ideas:"]
    lines += [
        f"- {i.id} [{i.status}] {i.title} (owner: {i.owner or '?'}): {i.summary}"
        for i in state.ideas
    ] or ["- (none)"]
    lines.append("Modules:")
    for m in state.modules:
        if m.status == "removed":
            continue
        people = "; ".join(f"{c.member}: {c.approach}" for c in m.contributions)
        lines.append(
            f"- {m.id} [{m.status}] {m.title}: {m.note} (inputs: {', '.join(m.inputs) or '-'}"
            + (f"; inside: {m.group}" if m.group else "")
            + (f"; people: {people}" if people else "")
            + ")"
        )
    if len(lines) == 3:
        lines.append("- (none)")
    return "\n".join(lines)


def note_text(source: Source) -> str:
    when = f"{source.occurred_at:%Y-%m-%d %H:%M}"
    return f"=== SOURCE {source.id} · {when} · {source.title} ===\n{source.text}"


def note_images(source: Source, assets: Callable[[str], Asset | None]) -> list[dict]:
    """The images a note embeds, each introduced by its name, as content parts."""
    parts: list[dict] = []
    for line in source.text.splitlines():
        match = ingest.IMAGE_LINE.match(line.strip())
        if match and match["url"].startswith("assets/"):
            asset = assets(match["url"].removeprefix("assets/"))
            if asset:
                parts += [
                    {"type": "text", "text": f"Image {match['url']} in SOURCE {source.id}:"},
                    llm.image_part(asset),
                ]
    return parts


def text_hash(source: Source) -> str:
    return hashlib.sha256(source.text.encode()).hexdigest()[:16]


def batches(sources: list[Source]) -> list[list[Source]]:
    groups: list[list[Source]] = [[]]
    for source in sources:
        if groups[-1] and sum(len(s.text) for s in groups[-1]) + len(source.text) > BATCH_CHARS:
            groups.append([])
        groups[-1].append(source)
    return [group for group in groups if group]


def read_notes(
    workspace: Workspace,
    notes: list[Source],
    state: LineageState | None,
    assets: Callable[[str], Asset | None],
) -> tuple[list[SourceAnalysis], llm.Usage]:
    """One model call: analyses of `notes`, linked to the registry in `state`."""
    content: list[dict] = [
        {
            "type": "text",
            "text": f"Team roster:\n{roster_text(workspace)}\n\nRegistry so far:\n"
            f"{registry_text(state)}\n\nNotes, oldest first:\n\n"
            + "\n\n".join(note_text(note) for note in notes),
        }
    ]
    for note in notes:
        content += note_images(note, assets)
    result, usage = llm.complete(
        [{"role": "system", "content": EXTRACT_PROMPT}, {"role": "user", "content": content}],
        Extraction,
        purpose=f"extract {workspace.id} {notes[0].id}..{notes[-1].id}",
        effort="low",
        max_tokens=60000,
    )
    by_source = {analysis.source: analysis for analysis in result.analyses}
    members = member_index(workspace)
    analyses = []
    for index, note in enumerate(notes):
        # The model sometimes shortens source ids; fall back to the position in its answer.
        analysis = by_source.get(note.id) or (
            result.analyses[index] if index < len(result.analyses) else None
        )
        analysis = (analysis or SourceAnalysis(source=note.id)).model_copy(
            update={"source": note.id}
        )
        repair_analysis(analysis, note.text, members)
        analyses.append(analysis)
    return analyses, usage


def find_roster(
    sources: list[Source], known: list[Member] | None = None
) -> tuple[list[Member], llm.Usage]:
    """The people in `sources`; with `known`, only those not on that roster yet."""
    text = "\n\n".join(note_text(s) for s in sources)[:200_000]
    if known:
        listed = "\n".join(f"- {m.name} ({', '.join(m.aliases)})" for m in known)
        text = f"Roster so far:\n{listed}\n\nNotes:\n\n{text}"
    roster, usage = llm.complete(
        [{"role": "system", "content": ROSTER_PROMPT}, {"role": "user", "content": text}],
        Roster,
        purpose="roster",
        effort="low",
    )
    seen: set[str] = set()
    members = []
    for member in roster.members:
        member.id = slug(member.id) or slug(member.name)
        if member.id not in seen:
            seen.add(member.id)
            members.append(member)
    return members, usage


def merge_roster(known: list[Member], found: list[Member]) -> list[Member]:
    """`known` plus the people in `found` it does not have yet. A person counts as known when
    their name or an alias matches one on the roster (ids are first names, so two people can
    share one); a new person whose id is taken gets a numbered one."""
    names = {n.casefold() for m in known for n in [m.name, *m.aliases]}
    ids = {m.id for m in known}
    merged = list(known)
    for member in found:
        if {n.casefold() for n in [member.name, *member.aliases]} & names:
            continue
        base, number = member.id, 2
        while member.id in ids:
            member = member.model_copy(update={"id": f"{base}-{number}"})
            number += 1
        ids.add(member.id)
        names |= {n.casefold() for n in [member.name, *member.aliases]}
        merged.append(member)
    return merged


# --- Briefing --------------------------------------------------------------------


def state_text(state: LineageState) -> str:
    """The replayed state in the words the briefing model needs."""
    titles = {i.id: i.title for i in state.ideas}
    lines = [registry_text(state), "Stances (latest per member and idea):"]
    lines += [f"- {c.member} {c.latest} {titles.get(c.idea, c.idea)}" for c in state.stances]
    open_items = [i for i in state.items if i.kind in ("question", "action", "decision")][-30:]
    lines.append("Recent questions, decisions, and actions (oldest first):")
    lines += [
        f"- [{i.source}] {i.kind}{f' ({i.member})' if i.member else ''}: {i.quote}"
        for i in open_items
    ]
    return "\n".join(lines)


def checked_evidence(
    found: list[Evidence], texts: dict[str, str], issues: list[str]
) -> list[Evidence]:
    """The evidence whose quotes occur in their sources, pointed at the exact text."""
    kept = []
    for e in found:
        quote = repair_quote(texts[e.source], e.quote) if e.source in texts else None
        if quote:
            kept.append(Evidence(source=e.source, quote=quote))
        else:
            issues.append(f"quote not found in {e.source}: {e.quote!r}")
    return kept


def check_board(
    board: Board, workspace: Workspace, sources: list[Source], modules: set[str]
) -> tuple[Board, list[str]]:
    """Keep only claims whose quotes check out against their sources ("no quote, no claim")."""
    texts = {s.id: s.text for s in sources}
    members = member_index(workspace)
    issues: list[str] = []

    def evidence(found: list[Evidence]) -> list[Evidence]:
        return checked_evidence(found, texts, issues)

    def claim(found: Claim | None, label: str) -> Claim | None:
        if found is None:
            return None
        checked = evidence(found.evidence)
        if not checked:
            issues.append(f"{label} dropped: no quote checks out ({found.text!r})")
            return None
        return Claim(text=found.text, evidence=checked)

    contests = []
    kept: dict[int, int] = {}  # the model's contest index -> its index after checking
    for index, contest in enumerate(board.contests):
        checked = evidence(contest.evidence)
        sides = [
            ContestSide(
                members=[members[m.lower()] for m in side.members if m.lower() in members],
                position=side.position,
            )
            for side in contest.sides
        ]
        if not checked:
            issues.append(f"contest dropped: no quote checks out ({contest.question!r})")
            continue
        kept[index] = len(contests)
        contests.append(
            Contest(
                question=contest.question,
                stakes=contest.stakes,
                module=contest.module if contest.module in modules else None,
                sides=[side for side in sides if side.members],
                decide_by=contest.decide_by,
                evidence=checked,
            )
        )

    def step_of(found: Step, label: str) -> Step | None:
        checked = evidence(found.evidence)
        if not checked:
            issues.append(f"{label} dropped: no quote checks out ({found.text!r})")
            return None
        contest = kept.get(found.contest) if found.contest is not None else None
        return found.model_copy(update={"evidence": checked, "contest": contest})

    people = []
    for person in board.people:
        member = members.get(person.member.lower())
        if member is None:
            issues.append(f"unknown member {person.member!r}")
            continue
        steps = [step_of(step, f"{member}'s next step") for step in person.next]
        people.append(
            PersonFocus(
                member=member,
                idea=claim(person.idea, f"{member}'s idea"),
                next=[step for step in steps if step],
                modules=[m for m in person.modules if m in modules],
            )
        )
    order = {m.id: i for i, m in enumerate(workspace.members)}
    people.sort(key=lambda p: order.get(p.member, len(order)))
    agreed = [claim(point, "agreed point") for point in board.agreed]
    checked_board = Board(
        headline=claim(board.headline, "headline"),
        problem=claim(board.problem, "problem"),
        status=claim(board.status, "status"),
        agreed=[point for point in agreed if point],
        people=people,
        contests=contests,
    )
    return checked_board, issues


def write_board(
    workspace: Workspace,
    sources: list[Source],
    analyses: dict[str, SourceAnalysis],
    origin: str,
) -> tuple[BoardState, llm.Usage]:
    state = build_state(workspace, sources, analyses)
    timeline = "\n".join(f"- {s.id} · {s.occurred_at:%Y-%m-%d} · {s.title}" for s in sources)
    recent = "\n\n".join(note_text(s) for s in sources[-RECENT_NOTES:])
    board, usage = llm.complete(
        [
            {"role": "system", "content": BOARD_PROMPT},
            {
                "role": "user",
                "content": f"Team roster:\n{roster_text(workspace)}\n\nAll notes, oldest "
                f"first:\n{timeline}\n\nState after the latest note:\n{state_text(state)}"
                f"\n\nThe latest notes in full:\n\n{recent}",
            },
        ],
        Board,
        purpose=f"board {workspace.id} {state.as_of}",
        effort="medium",
    )
    modules = {m.id for m in state.modules if m.status != "removed"}
    checked, issues = check_board(board, workspace, sources, modules)
    return (
        BoardState(
            **checked.model_dump(),
            workspace=workspace.id,
            origin=origin,
            as_of=state.as_of,
            written_at=datetime.now().replace(microsecond=0),
            issues=issues,
        ),
        usage,
    )


# --- The Update button -------------------------------------------------------------


def update_workspace(
    workspace_id: str, report: Callable[..., None], fresh: bool = False
) -> BoardState:
    """Import the workspace's Notion page (when linked), read every new or changed note with
    the model (every note when `fresh`, e.g. after a prompt change), and write the briefing.
    `report(step=..., done=..., total=..., cost=...)` is called as the work progresses."""
    spent = 0.0

    def charge(usage: llm.Usage) -> None:
        nonlocal spent
        spent += usage.cost
        report(cost=round(spent, 4))

    with store.connect() as conn:
        workspace = store.get_workspace(conn, workspace_id)
    if workspace is None:
        raise ValueError(f"no workspace {workspace_id!r}")

    if workspace.notion_page_id:
        report(step="Reading the Notion page")
        notes, images = ingest.import_notion(workspace.notion_page_id)
        with store.connect() as conn:
            store.save_sources(conn, workspace.id, notes)
            store.save_assets(conn, workspace.id, images)

    with store.connect() as conn:
        sources = store.list_sources(conn, workspace.id)
        analyses = store.list_analyses(conn, workspace.id, "llm")
        bases = store.list_bases(conn, workspace.id, "llm")
    if not sources:
        raise ValueError("the page has no notes yet")
    notes = [s for s in sources if s.kind != "session"]

    # A changed note changes what every later note links to, so read from the first change on.
    stale = [i for i, s in enumerate(notes) if fresh or bases.get(s.id) != text_hash(s)]
    todo = notes[stale[0] :] if stale else []

    # New notes can bring new people; they join the roster before the notes are read, so the
    # model attributes their words to them.
    if not workspace.members or todo:
        report(step="Finding the team in the notes")
        found, usage = find_roster(todo or notes, workspace.members)
        charge(usage)
        members = merge_roster(workspace.members, found)
        if members != workspace.members:
            workspace = workspace.model_copy(update={"members": members})
            with store.connect() as conn:
                store.save_workspace(conn, workspace)
    done = len(notes) - len(todo)

    def asset(name: str) -> Asset | None:
        with store.connect() as conn:
            return store.get_asset(conn, workspace.id, name)

    for batch in batches(todo):
        report(
            step=f"Reading notes {done + 1}-{done + len(batch)} of {len(notes)}",
            done=done,
            total=len(notes),
        )
        known = [s for s in sources if s.occurred_at < batch[0].occurred_at]
        before = build_state(workspace, known, analyses) if known else None
        results, usage = read_notes(workspace, batch, before, asset)
        charge(usage)
        analyses.update({a.source: a for a in results})
        with store.connect() as conn:
            store.save_analyses(
                conn, workspace.id, "llm", results, {s.id: text_hash(s) for s in batch}
            )
        done += len(batch)

    report(step="Writing the briefing", done=len(notes), total=len(notes))
    with store.connect() as conn:
        analyses.update(store.list_analyses(conn, workspace.id, "user"))
    board, usage = write_board(workspace, sources, analyses, "llm")
    charge(usage)
    with store.connect() as conn:
        store.save_board(conn, board)
    return board
