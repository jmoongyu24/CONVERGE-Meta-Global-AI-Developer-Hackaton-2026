"""Data shapes shared by ingest, the analysis pipeline, the API, and the gold labels."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Stance = Literal["propose", "support", "refine", "question", "challenge", "inform"]
OpKind = Literal["new", "update", "branch", "merge", "park", "drop"]
ItemKind = Literal["question", "decision", "action", "consensus"]
IdeaStatus = Literal["active", "dormant", "parked", "dropped"]


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


class SourceMeta(BaseModel):
    """A record without its text: enough for timelines and lists."""

    id: str
    kind: Literal["meeting", "note"] = "meeting"
    occurred_at: datetime
    title: str
    url: str | None = None


class Source(SourceMeta):
    """One record the team wrote, e.g. a meeting note."""

    text: str


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


class SourceAnalysis(BaseModel):
    source: str  # Source.id
    ops: list[IdeaOp] = Field(default_factory=list)
    mentions: list[Mention] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)


class Gold(BaseModel):
    """Hand-labelled analyses for one demo workspace, one per source in time order."""

    workspace: str
    analyses: list[SourceAnalysis]


# --- Lineage: analyses replayed in time order (pipeline.build_state) ------------


class IdeaVersion(BaseModel):
    source: str
    title: str
    summary: str


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


class Digest(BaseModel):
    """What one source changed: applied idea ops, stance shifts, and its items."""

    source: str
    ops: list[IdeaOp] = Field(default_factory=list)
    shifts: list[StanceShift] = Field(default_factory=list)
    items: list[LocatedItem] = Field(default_factory=list)


class LineageState(BaseModel):
    workspace: Workspace
    sources: list[SourceMeta]  # the whole timeline, so the UI can draw the time slider
    as_of: str
    ideas: list[IdeaView]
    edges: list[Edge]
    mentions: list[LocatedMention]
    items: list[LocatedItem]
    stances: list[StanceCell]
    digest: Digest
    issues: list[str]  # entries dropped or repaired because a quote or reference did not check out
