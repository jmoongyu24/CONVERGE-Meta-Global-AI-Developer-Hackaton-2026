"""Converge Room: settle an open question the way a good meeting would, from the record.

Each member involved gets a delegate that speaks only from what that member said in the
notes and must quote it. Delegates state their position, then answer each other. A
facilitator turns the exchange into proposals that end in who does what next, each member's
likely acceptance, and the risk. Adopting one writes it to the session note (and to Notion),
so the picture, the next steps, and the briefing follow the decision.
"""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from app import llm, pipeline, store
from app.models import (
    Claim,
    Contribution,
    ConvergeRequest,
    ConvergeSession,
    Item,
    LineageState,
    ModuleOp,
    Position,
    Proposal,
    Source,
    SourceAnalysis,
    Synthesis,
    Turn,
)

DELEGATE_PROMPT = """You are the delegate of {name} in a research meeting that has to settle \
this question: {question}

You know only {name}'s own record from the team's notes, listed below with source ids. Speak \
as {name}'s delegate, in two to four plain sentences: the position {name} holds, the \
strongest reason in their own words, and what it would take for them to accept another \
option. Back it with one to three quotes copied exactly from the record, each with its \
source id. Never give {name} an opinion the record does not show; when the record is thin, \
say what you would need to ask {name}. Write in the language of the notes."""

REPLY_PROMPT = """Here is what every delegate said in the first round. Answer as {name}'s \
delegate in two or three sentences: where you can meet the others, what you still cannot \
accept, and on what condition you would. Quote {name}'s record as before."""

FACILITATOR_PROMPT = """You facilitate a research team's decision on one open question. From \
the delegates' exchange and the team's state, propose two different ways to settle it.

For each proposal: a short title; the decision in one sentence; why, with one to three \
quotes copied exactly from the quotes listed in the exchange or the state, with their source \
ids; who does what next, giving every \
involved member one concrete step (name the module id when the step changes who works on a \
module); each involved member's likely acceptance (yes, maybe, or no) with a short reason \
drawn from their delegate; and the main risk.

Prefer proposals that keep each member's strongest contribution and that the team could \
start this week. Use member ids and module ids exactly as given. Write in the language of \
the notes."""

sessions: dict[str, ConvergeSession] = {}
sessions_lock = threading.Lock()


def record_of(state: LineageState, member: str) -> str:
    """Everything the replayed notes attribute to one member, for their delegate."""
    titles = {i.id: i.title for i in state.ideas}
    lines = [
        f'- [{m.source}] {m.stance} on "{titles.get(m.idea, m.idea)}": "{m.quote}"'
        for m in state.mentions
        if m.member == member
    ]
    lines += [
        f'- [{c.source}] works on {module.title}: "{c.quote}"'
        for module in state.modules
        for c in module.contributions
        if c.member == member
    ]
    lines += [f'- [{i.source}] {i.kind}: "{i.quote}"' for i in state.items if i.member == member]
    return "\n".join(lines[-60:]) or "(nothing recorded)"


def start(
    workspace_id: str, request: ConvergeRequest, load: Callable[[], tuple]
) -> ConvergeSession:
    """Open a session and run it in the background; `load` returns (state, sources)."""
    session = ConvergeSession(
        id=uuid4().hex[:12],
        workspace=workspace_id,
        question=request.question,
        members=request.members,
        module=request.module,
        state="running",
        step="Preparing the delegates",
    )
    with sessions_lock:
        sessions[session.id] = session
    threading.Thread(target=run, args=(session.id, load), daemon=True).start()
    return session


def get(workspace_id: str, session_id: str) -> ConvergeSession | None:
    with sessions_lock:
        found = sessions.get(session_id)
    if found is None:
        with store.connect() as conn:
            found = store.get_converge(conn, workspace_id, session_id)
    return found if found and found.workspace == workspace_id else None


def update(session_id: str, **changes: object) -> ConvergeSession:
    with sessions_lock:
        sessions[session_id] = sessions[session_id].model_copy(update=changes)
        return sessions[session_id]


def run(session_id: str, load: Callable[[], tuple]) -> None:
    try:
        state, sources = load()
        converge(session_id, state, sources)
    except Exception as error:  # any failure must reach the people waiting in the room
        update(session_id, state="failed", error=str(error) or type(error).__name__)
    with store.connect() as conn:
        store.save_converge(conn, sessions[session_id])


def converge(session_id: str, state: LineageState, sources: list[Source]) -> None:
    session = sessions[session_id]
    names = {m.id: m.name for m in state.workspace.members}
    members = [m for m in session.members if m in names]
    texts = {s.id: s.text for s in sources}
    question = session.question
    spent = 0.0

    def speak(member: str, round_: int, previous: list[Turn]) -> Turn:
        nonlocal spent
        messages = [
            {
                "role": "system",
                "content": DELEGATE_PROMPT.format(name=names[member], question=question),
            },
            {"role": "user", "content": f"{names[member]}'s record:\n{record_of(state, member)}"},
        ]
        if previous:
            said = "\n".join(f"- {names[t.member]}'s delegate: {t.message}" for t in previous)
            messages.append(
                {
                    "role": "user",
                    "content": f"First round:\n{said}\n\n"
                    + REPLY_PROMPT.format(name=names[member]),
                }
            )
        position, usage = llm.complete(
            messages, Position, purpose=f"delegate {member} round {round_} {session_id}"
        )
        spent += usage.cost
        evidence = pipeline.checked_evidence(position.evidence, texts, [])
        return Turn(
            member=member,
            round=round_,
            message=position.message,
            evidence=evidence,
            supported=bool(evidence),
        )

    turns: list[Turn] = []
    for round_, label in [
        (1, "Delegates state their positions"),
        (2, "Delegates answer each other"),
    ]:
        update(session_id, step=label)
        first_round = [t for t in turns if t.round == 1]
        with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
            spoken = list(
                pool.map(speak, members, [round_] * len(members), [first_round] * len(members))
            )
        turns += spoken
        update(session_id, turns=list(turns), cost=round(spent, 4))

    update(session_id, step="The facilitator drafts proposals")
    exchange = "\n".join(
        f"- round {t.round}, {names[t.member]} ({t.member}): {t.message}"
        + "".join(f'\n    quote [{e.source}]: "{e.quote}"' for e in t.evidence)
        for t in turns
    )
    synthesis, usage = llm.complete(
        [
            {"role": "system", "content": FACILITATOR_PROMPT},
            {
                "role": "user",
                "content": f"Question: {question}\n\nInvolved members: "
                + ", ".join(f"{m} ({names[m]})" for m in members)
                + f"\n\nTeam state:\n{pipeline.state_text(state)}\n\nThe exchange:\n{exchange}",
            },
        ],
        Synthesis,
        purpose=f"facilitator {session_id}",
        effort="medium",
    )
    spent += usage.cost
    modules = {m.id for m in state.modules if m.status != "removed"}
    proposals = [checked_proposal(p, names, modules, texts) for p in synthesis.proposals]
    update(session_id, state="done", step="", proposals=proposals, cost=round(spent, 4))


def checked_proposal(
    proposal: Proposal, names: dict[str, str], modules: set[str], texts: dict[str, str]
) -> Proposal:
    """Keep the proposal's references to real members and modules and its quotes that check
    out; a "why" with no quote left is dropped ("no quote, no claim")."""
    why = None
    if proposal.why:
        evidence = pipeline.checked_evidence(proposal.why.evidence, texts, [])
        why = Claim(text=proposal.why.text, evidence=evidence) if evidence else None
    return proposal.model_copy(
        update={
            "why": why,
            "assignments": [
                a.model_copy(update={"module": a.module if a.module in modules else None})
                for a in proposal.assignments
                if a.member in names
            ],
            "acceptance": [a for a in proposal.acceptance if a.member in names],
        }
    )


def adoption(
    state: LineageState, session: ConvergeSession, index: int, member: str, clock: str
) -> tuple[list[str], SourceAnalysis]:
    """The session-note lines an adopted proposal writes, and what they change: a decision,
    an action per member, and who works on which module."""
    names = {m.id: m.name for m in state.workspace.members}
    if member not in names:
        raise ValueError(f"unknown member {member!r}")
    if not 0 <= index < len(session.proposals):
        raise ValueError("no such proposal")
    proposal = session.proposals[index]
    decided = (
        f"{clock} {names[member]} adopted in the Converge Room: "
        f"{proposal.title}. {proposal.decision}"
    )
    lines = [decided]
    change = SourceAnalysis(source="")
    change.items.append(Item(kind="decision", quote=decided, module=session.module))
    for assignment in proposal.assignments:
        line = f"{clock} Next for {names[assignment.member]}: {assignment.step}"
        lines.append(line)
        change.items.append(
            Item(kind="action", quote=line, member=assignment.member, module=assignment.module)
        )
        if assignment.module:
            change.modules.append(
                ModuleOp(
                    op="update",
                    module=assignment.module,
                    quote=line,
                    contributions=[
                        Contribution(member=assignment.member, approach=assignment.step, quote=line)
                    ],
                )
            )
    return lines, change
