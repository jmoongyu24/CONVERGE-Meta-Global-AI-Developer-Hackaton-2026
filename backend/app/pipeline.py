"""Replay per-source analyses into the team's idea lineage and architecture.

Each SourceAnalysis works like a commit: replaying them in time order rebuilds the idea
registry and the architecture diagram as they stood after any source, which is what the
UI's time slider shows. The LLM extraction (F2) will produce these analyses; until then the
hand-labelled gold drives it. Edits made in the UI become analyses of a session note.
"""

import re
from collections import Counter, defaultdict

from app.models import (
    Digest,
    Edge,
    EditRequest,
    IdeaOp,
    IdeaVersion,
    IdeaView,
    Item,
    LineageState,
    LocatedItem,
    LocatedMention,
    LocatedModuleOp,
    Mention,
    ModuleOp,
    ModuleView,
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
    op: LocatedModuleOp, modules: dict[str, ModuleView], members: set[str], ideas: set[str]
) -> list[str]:
    """Apply a valid module op. References that do not resolve are left out and reported."""
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
    return problems


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
                idea.versions.append(
                    IdeaVersion(source=source.id, title=idea.title, summary=idea.summary)
                )
            else:
                ideas[op.idea].status = "parked" if op.op == "park" else "dropped"
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
            for problem in apply_module_op(located_op, modules, members, set(ideas)):
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
