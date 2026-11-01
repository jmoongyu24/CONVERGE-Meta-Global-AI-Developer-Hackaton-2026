// Types mirror backend/app/models.py; helpers call the FastAPI endpoints.

export type Stance = "propose" | "support" | "refine" | "question" | "challenge" | "inform";
export type OpKind = "new" | "update" | "branch" | "merge" | "park" | "drop";
export type ItemKind = "question" | "decision" | "action" | "consensus";
export type IdeaStatus = "active" | "dormant" | "parked" | "dropped";

export interface Member {
  id: string;
  name: string;
  role: string;
  aliases: string[];
}

export interface Workspace {
  id: string;
  name: string;
  description: string;
  members: Member[];
}

export interface SourceMeta {
  id: string;
  kind: "meeting" | "note";
  occurred_at: string;
  title: string;
  url: string | null;
}

export interface Source extends SourceMeta {
  text: string;
}

export interface IdeaOp {
  op: OpKind;
  idea: string;
  parents: string[];
  title: string | null;
  summary: string | null;
  owner: string | null;
  quote: string;
}

export interface IdeaVersion {
  source: string;
  title: string;
  summary: string;
}

export interface IdeaView {
  id: string;
  title: string;
  summary: string;
  owner: string | null;
  status: IdeaStatus;
  parents: string[];
  born: string;
  last_active: string;
  versions: IdeaVersion[];
}

export interface Edge {
  parent: string;
  child: string;
  kind: "branch" | "merge";
  source: string;
}

export interface LocatedMention {
  idea: string;
  member: string;
  stance: Stance;
  quote: string;
  source: string;
  line: number;
}

export interface LocatedItem {
  kind: ItemKind;
  quote: string;
  member: string | null;
  idea: string | null;
  source: string;
  line: number;
}

export interface StanceCell {
  member: string;
  idea: string;
  latest: Stance;
  source: string;
  counts: Partial<Record<Stance, number>>;
}

export interface StanceShift {
  member: string;
  idea: string;
  before: Stance;
  after: Stance;
}

export interface Digest {
  source: string;
  ops: IdeaOp[];
  shifts: StanceShift[];
  items: LocatedItem[];
}

export interface LineageState {
  workspace: Workspace;
  sources: SourceMeta[];
  as_of: string;
  ideas: IdeaView[];
  edges: Edge[];
  mentions: LocatedMention[];
  items: LocatedItem[];
  stances: StanceCell[];
  digest: Digest;
  issues: string[];
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText} for ${path}`);
  return (await response.json()) as T;
}

export const fetchWorkspaces = () => get<Workspace[]>("/api/workspaces");

export const fetchSource = (workspaceId: string, sourceId: string) =>
  get<Source>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/sources/${encodeURIComponent(sourceId)}`,
  );

/** The lineage after every source, in time order, so moving the slider never waits. */
export async function fetchTimeline(workspaceId: string): Promise<LineageState[]> {
  const base = `/api/workspaces/${encodeURIComponent(workspaceId)}/state`;
  const latest = await get<LineageState>(base);
  return Promise.all(
    latest.sources.map((source) =>
      source.id === latest.as_of
        ? latest
        : get<LineageState>(`${base}?as_of=${encodeURIComponent(source.id)}`),
    ),
  );
}
