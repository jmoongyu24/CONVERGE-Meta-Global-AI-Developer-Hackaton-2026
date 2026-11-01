"""Data shapes shared by ingest, the analysis pipeline, the API, and the gold labels."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Stance = Literal["propose", "support", "refine", "question", "challenge", "inform"]
OpKind = Literal["new", "update", "branch", "merge", "park", "drop"]
ItemKind = Literal["question", "decision", "action", "consensus"]


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


class Source(BaseModel):
    """One record the team wrote, e.g. a meeting note."""

    id: str
    kind: Literal["meeting", "note"] = "meeting"
    occurred_at: datetime
    title: str
    text: str
    url: str | None = None


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
