// Types mirror backend/app/models.py; helpers call the FastAPI endpoints.

export type Stance = "propose" | "support" | "refine" | "question" | "challenge" | "inform";
export type OpKind = "new" | "update" | "branch" | "merge" | "park" | "drop";
export type ItemKind = "question" | "decision" | "action" | "consensus";
export type IdeaStatus = "active" | "dormant" | "parked" | "dropped";
export type ModuleOpKind = "add" | "update" | "remove";
export type ModuleStatus = "proposed" | "adopted";

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
  notion_page_id: string | null;
}

export interface SourceMeta {
  id: string;
  kind: "meeting" | "note" | "session";
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
  module: string | null;
  source: string;
  line: number;
}

export interface LocatedModuleOp {
  op: ModuleOpKind;
  module: string;
  title: string | null;
  note: string | null;
  owner: string | null;
  status: ModuleStatus | null;
  inputs: string[] | null;
  group: string | null;
  idea: string | null;
  quote: string;
  source: string;
  line: number;
}

export interface ModuleContribution {
  member: string;
  approach: string;
  idea: string | null;
  source: string;
  line: number;
  quote: string;
}

export interface ModuleView {
  id: string;
  title: string;
  note: string;
  owner: string | null;
  status: ModuleStatus | "removed";
  inputs: string[];
  group: string | null;
  idea: string | null;
  born: string;
  changed: string;
  history: LocatedModuleOp[];
  contributions: ModuleContribution[];
  contested: boolean;
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
  modules: LocatedModuleOp[];
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
  modules: ModuleView[];
  digest: Digest;
  issues: string[];
}

export interface Evidence {
  source: string;
  quote: string;
}

export interface Claim {
  text: string;
  evidence: Evidence[];
}

export interface PersonFocus {
  member: string;
  idea: Claim | null;
  next: Claim[];
  modules: string[];
}

export interface Contest {
  question: string;
  module: string | null;
  sides: { members: string[]; position: string }[];
  decide_by: string | null;
  evidence: Evidence[];
}

export interface Board {
  workspace: string;
  origin: string;
  as_of: string;
  written_at: string;
  problem: Claim | null;
  status: Claim | null;
  people: PersonFocus[];
  contests: Contest[];
  issues: string[];
}

export interface UpdateStatus {
  state: "idle" | "running" | "done" | "failed";
  step: string;
  done: number;
  total: number;
  error: string | null;
  cost: number;
}

export interface Turn {
  member: string;
  round: number;
  message: string;
  evidence: Evidence[];
  supported: boolean;
}

export interface Proposal {
  title: string;
  decision: string;
  why: Claim | null;
  assignments: { member: string; step: string; module: string | null }[];
  acceptance: { member: string; likely: "yes" | "maybe" | "no"; reason: string }[];
  risk: string;
}

export interface ConvergeSession {
  id: string;
  workspace: string;
  question: string;
  members: string[];
  module: string | null;
  state: "running" | "done" | "failed";
  step: string;
  turns: Turn[];
  proposals: Proposal[];
  adopted: number | null;
  error: string | null;
  cost: number;
}

export interface EditRequest {
  member: string;
  action: "add" | "update" | "remove" | "question" | "action" | "support" | "challenge";
  module?: string;
  title?: string;
  note?: string;
  owner?: string;
  status?: ModuleStatus;
  inputs?: string[];
  group?: string;
  idea?: string;
  text?: string;
}

export interface EditResult {
  source: string;
  line: string;
  module: string;
  notion: "synced" | "failed" | "off";
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText} for ${path}`);
  return (await response.json()) as T;
}

const workspacePath = (workspaceId: string) => `/api/workspaces/${encodeURIComponent(workspaceId)}`;

export const fetchWorkspaces = () => get<Workspace[]>("/api/workspaces");

export const fetchState = (workspaceId: string) =>
  get<LineageState>(`${workspacePath(workspaceId)}/state`);

/** The briefing from the last update, or null before the first one. */
export async function fetchBoard(workspaceId: string): Promise<Board | null> {
  const response = await fetch(`${workspacePath(workspaceId)}/board`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return (await response.json()) as Board;
}

async function send<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : response.statusText);
  }
  return data as T;
}

export const startUpdate = (workspaceId: string) =>
  send<UpdateStatus>(`${workspacePath(workspaceId)}/update`);

export const fetchUpdate = (workspaceId: string) =>
  get<UpdateStatus>(`${workspacePath(workspaceId)}/update`);

export const connectNotion = (url: string) => send<Workspace>("/api/workspaces/connect", { url });

/** The workspace a Notion page belongs to, for the browser extension's side panel. */
export async function findNotionPage(url: string) {
  const id = /[0-9a-f]{32}/.exec(url.replaceAll("-", ""))?.[0];
  return id ? (await get<{ workspace: string | null }>(`/api/notion/${id}`)).workspace : null;
}

export const fetchRevision = async (workspaceId: string) =>
  (await get<{ revision: string }>(`${workspacePath(workspaceId)}/revision`)).revision;

/** Where an image line's target is served: `assets/<name>` lives in the workspace. */
export const assetUrl = (workspaceId: string, target: string) =>
  target.startsWith("assets/")
    ? `${workspacePath(workspaceId)}/assets/${encodeURIComponent(target.slice(7))}`
    : target;

/** Record an edit; the server answers 400 with a reason when the edit does not fit. */
export const postEdit = (workspaceId: string, edit: EditRequest) =>
  send<EditResult>(`${workspacePath(workspaceId)}/edits`, edit);

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

export const openConverge = (
  workspaceId: string,
  request: { question: string; members: string[]; module: string | null },
) => send<ConvergeSession>(`${workspacePath(workspaceId)}/converge`, request);

export const fetchConverge = (workspaceId: string, sessionId: string) =>
  get<ConvergeSession>(`${workspacePath(workspaceId)}/converge/${sessionId}`);

export const adoptProposal = (
  workspaceId: string,
  sessionId: string,
  proposal: number,
  member: string,
) =>
  send<EditResult>(`${workspacePath(workspaceId)}/converge/${sessionId}/adopt`, {
    proposal,
    member,
  });
