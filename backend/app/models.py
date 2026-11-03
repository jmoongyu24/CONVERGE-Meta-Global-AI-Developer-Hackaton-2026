"""Data shapes shared by ingest, the analysis pipeline, the API, and the gold labels."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Stance = Literal["propose", "support", "refine", "question", "challenge", "inform"]
OpKind = Literal["new", "update", "branch", "merge", "park", "drop"]
ItemKind = Literal["question", "decision", "action", "consensus"]
IdeaStatus = Literal["active", "dormant", "parked", "dropped"]
ModuleOpKind = Literal["add", "update", "remove"]
ModuleStatus = Literal["proposed", "adopted"]


class Member(BaseModel):
    id: str
    name: str
    role: str = ""
    aliases: list[str] = Field(default_factory=list)  # how notes refer to them, e.g. "Prof", "D"


class Workspace(BaseModel):
    """One team: its members and every record they wrote are analysed together."""

    id: str
    name: str
    description: str = ""
    members: list[Member]
    notion_page_id: str | None = None  # Notion page that edits made in CONVERGE are written to


class SourceMeta(BaseModel):
    """A record without its text: enough for timelines and lists."""

    id: str
    kind: Literal["meeting", "note", "session"] = "meeting"  # session: edits made in CONVERGE
    occurred_at: datetime
    title: str
    url: str | None = None


class Source(SourceMeta):
    """One record the team wrote, e.g. a meeting note. Images in it are Markdown image lines
    pointing at `assets/<name>`, stored next to the text as Assets."""

    text: str


class Asset(BaseModel):
    """An image from a record, e.g. a whiteboard photo pasted into a Notion page."""

    name: str
    media_type: str
    data: bytes


# --- Analysis of one source ----------------------------------------------------
# The LLM pipeline must emit exactly this shape, and gold.json uses it too, so the
# lineage engine and the evaluation treat model output and gold labels the same way.
# Every entry carries a verbatim quote from the source ("no quote, no claim").


class IdeaOp(BaseModel):
    """A change to the team's idea registry."""

    op: OpKind
    idea: str  # idea id; new ids are introduced by new / branch / merge
    parents: list[str] = Field(default_factory=list)  # branch: 1 parent, merge: 2 or more
    title: str | None = None  # required when the op introduces an idea
    summary: str | None = None
    owner: str | None = None  # member id
    quote: str


class Mention(BaseModel):
    """A member's stance on an idea."""

    idea: str
    member: str
    stance: Stance
    quote: str


class Item(BaseModel):
    """An open question, decision, action item, or point the whole team agreed on."""

    kind: ItemKind
    quote: str
    member: str | None = None
    idea: str | None = None
    module: str | None = None


class Contribution(BaseModel):
    """What one member builds or proposes for a module. Members on one module collaborate when
    their approaches serve the same idea and compete when they pursue different ideas."""

    member: str
    approach: str  # one line in the notes' terms; "" means the member stopped working on it
    idea: str | None = None  # the research idea this approach belongs to
    quote: str


class ModuleOp(BaseModel):
    """A change to the team's architecture: one module (a layer, component, or stage) and the
    modules that feed into it. On update, None keeps a field and "" clears it."""

    op: ModuleOpKind
    module: str  # module id; `add` introduces it
    title: str | None = None  # required for add
    note: str | None = None  # what the module does, in one line
    owner: str | None = None  # member in charge of building it
    status: ModuleStatus | None = None  # add defaults to "proposed"
    inputs: list[str] | None = None  # modules that feed this one; replaces the list when given
    group: str | None = None  # enclosing module, e.g. the encoder block holding its layers
    idea: str | None = None  # the research idea this module implements
    contributions: list[Contribution] = Field(default_factory=list)  # changes per member
    quote: str


class SourceAnalysis(BaseModel):
    source: str  # Source.id
    ops: list[IdeaOp] = Field(default_factory=list)
    mentions: list[Mention] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    modules: list[ModuleOp] = Field(default_factory=list)


class Extraction(BaseModel):
    """What the model returns for a batch of notes: one analysis per note, in order."""

    analyses: list[SourceAnalysis]


class Roster(BaseModel):
    """The people the model finds in a team's notes, for workspaces without a team file."""

    members: list[Member]


class Gold(BaseModel):
    """Hand-labelled analyses for one demo workspace, one per source in time order."""

    workspace: str
    analyses: list[SourceAnalysis]


# --- Lineage: analyses replayed in time order (pipeline.build_state) ------------


class IdeaVersion(BaseModel):
    source: str
    title: str
    summary: str


class Located(BaseModel):
    """A quote and where it sits in its source."""

    source: str
    line: int
    quote: str


class IdeaView(BaseModel):
    id: str
    title: str
    summary: str
    owner: str | None
    status: IdeaStatus
    parents: list[str]
    born: str  # source where the idea first appeared
    last_active: str  # latest source with an op or mention on the idea
    versions: list[IdeaVersion]
    closed: Located | None = None  # why it was parked or dropped, in the notes' words


class Edge(BaseModel):
    parent: str
    child: str
    kind: Literal["branch", "merge"]
    source: str


class LocatedMention(Mention):
    source: str
    line: int  # 1-based line where the quote starts in the source text


class LocatedItem(Item):
    source: str
    line: int


class StanceCell(BaseModel):
    """A member's current position on an idea and how often they took each stance."""

    member: str
    idea: str
    latest: Stance  # "inform" only when the member never took a position on the idea
    source: str  # where the latest stance was taken
    counts: dict[Stance, int]


class StanceShift(BaseModel):
    member: str
    idea: str
    before: Stance
    after: Stance


class LocatedModuleOp(ModuleOp):
    source: str
    line: int


class ModuleContribution(BaseModel):
    member: str
    approach: str
    idea: str | None
    source: str  # where the member last stated it
    line: int
    quote: str


class ModuleView(BaseModel):
    """A module as it stands after replay; removed modules stay listed so the UI can show
    what a meeting took out."""

    id: str
    title: str
    note: str
    owner: str | None
    status: Literal["proposed", "adopted", "removed"]
    inputs: list[str]
    group: str | None
    idea: str | None
    born: str  # source where the module first appeared
    changed: str  # latest source that changed it
    history: list[LocatedModuleOp]
    contributions: list[ModuleContribution] = Field(default_factory=list)
    contested: bool = False  # contributors pursue two or more different ideas


class Digest(BaseModel):
    """What one source changed: applied idea ops, stance shifts, its items, and module changes."""

    source: str
    ops: list[IdeaOp] = Field(default_factory=list)
    shifts: list[StanceShift] = Field(default_factory=list)
    items: list[LocatedItem] = Field(default_factory=list)
    modules: list[LocatedModuleOp] = Field(default_factory=list)


class LineageState(BaseModel):
    workspace: Workspace
    sources: list[SourceMeta]  # the whole timeline, so the UI can draw the time slider
    as_of: str
    ideas: list[IdeaView]
    edges: list[Edge]
    mentions: list[LocatedMention]
    items: list[LocatedItem]
    stances: list[StanceCell]
    modules: list[ModuleView]
    digest: Digest
    issues: list[str]  # entries dropped or repaired because a quote or reference did not check out


# --- Edits made in the UI ------------------------------------------------------


class EditRequest(BaseModel):
    """One change a member makes in the architecture view. The server writes it as a line of
    the day's session note, so an edit is quoted evidence like anything said in a meeting."""

    member: str
    action: Literal["add", "update", "remove", "question", "action", "support", "challenge"]
    module: str | None = None  # target module; add derives a new id from the title
    title: str | None = None
    note: str | None = None
    owner: str | None = None
    status: ModuleStatus | None = None
    inputs: list[str] | None = None
    group: str | None = None
    idea: str | None = None
    text: str = ""  # the member's own words: a reason, question, or comment


class EditResult(BaseModel):
    source: str  # the session note the edit was written to
    line: str
    module: str  # the module the edit changed or added
    notion: Literal["synced", "failed", "off"]


# --- Briefing: what the team sees first (pipeline.write_board) -----------------


class Evidence(BaseModel):
    source: str  # Source.id
    quote: str  # copied exactly from that source


class Claim(BaseModel):
    """A sentence the model wrote, kept only if at least one quote checks out."""

    text: str
    evidence: list[Evidence]


class Step(BaseModel):
    """A member's next step, with why it matters now and how the team will know it is done."""

    text: str
    why: str  # how it helps the team, as a short natural sentence
    due: str  # when it is due, as the notes give it; "" when they give no date
    contest: int | None  # index of the contest it helps settle
    evidence: list[Evidence]


class PersonFocus(BaseModel):
    member: str
    idea: Claim | None  # the approach or position the member is pushing now
    next: list[Step]  # their next steps, most important first
    modules: list[str]  # modules they work on


class ContestSide(BaseModel):
    members: list[str]
    position: str  # what this side wants and why, in one short line


class Contest(BaseModel):
    """An open disagreement that still needs a decision."""

    question: str
    stakes: str  # what the decision settles for the team, so its weight is plain
    module: str | None  # where the competing approaches meet in the architecture, if anywhere
    sides: list[ContestSide]
    decide_by: str | None  # when or how the notes say it gets decided
    evidence: list[Evidence]


class Board(BaseModel):
    headline: (
        Claim | None
    )  # the team's situation in one short sentence: what a newcomer reads first
    problem: Claim | None  # the research problem the team works on now
    status: Claim | None  # where things stand: what is settled, what is pending, what is next
    agreed: list[Claim]  # what the whole team has settled, newest first
    people: list[PersonFocus]
    contests: list[Contest]


class BoardState(Board):
    """A board with its evidence located, as the API serves it."""

    workspace: str
    origin: str  # whose analyses it was written from: "gold" or "llm"
    as_of: str  # latest source it covers
    written_at: datetime
    issues: list[str]  # claims and quotes dropped because they did not check out


class UpdateStatus(BaseModel):
    """Progress of the Update button's job: import, analysis, briefing."""

    state: Literal["idle", "running", "done", "failed"]
    step: str = ""
    done: int = 0
    total: int = 0
    error: str | None = None
    cost: float = 0.0  # dollars spent by this run


# --- CONVERGE Room (converge.py) -----------------------------------------------


class Position(BaseModel):
    """What a member's delegate says in one round, backed by the member's own words."""

    message: str
    evidence: list[Evidence]


class Assignment(BaseModel):
    member: str
    step: str  # their next concrete step under this proposal
    module: str | None  # the module whose people this changes, if any


class Acceptance(BaseModel):
    member: str
    likely: Literal["yes", "maybe", "no"]
    reason: str


class Proposal(BaseModel):
    title: str
    decision: str
    why: Claim | None  # kept only if a quote backs it
    assignments: list[Assignment]
    acceptance: list[Acceptance]
    risk: str


class Synthesis(BaseModel):
    """The facilitator's answer: different ways to settle the question."""

    proposals: list[Proposal]


class Turn(BaseModel):
    member: str
    round: int
    message: str
    evidence: list[Evidence]
    supported: bool  # at least one of its quotes checked out


class ConvergeRequest(BaseModel):
    question: str
    members: list[str]
    module: str | None = None


class ConvergeSession(BaseModel):
    id: str
    workspace: str
    question: str
    members: list[str]
    module: str | None
    state: Literal["running", "done", "failed"]
    step: str = ""
    turns: list[Turn] = Field(default_factory=list)
    proposals: list[Proposal] = Field(default_factory=list)
    adopted: int | None = None
    error: str | None = None
    cost: float = 0.0


class AdoptRequest(BaseModel):
    proposal: int  # index into the session's proposals
    member: str  # who adopted it
