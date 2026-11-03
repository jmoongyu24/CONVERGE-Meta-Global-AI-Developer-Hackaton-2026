import { Pause, Play } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ArchitectureView, ModuleForm, ModulePanel } from "./ArchitectureView";
import {
  type Board,
  connectNotion,
  type EditRequest,
  fetchBoard,
  fetchRevision,
  fetchState,
  fetchTimeline,
  fetchUpdate,
  fetchWorkspaces,
  findNotionPage,
  type LineageState,
  postEdit,
  startUpdate,
  type UpdateStatus,
  type Workspace,
} from "./api";
import { BoardView } from "./BoardView";
import { LineageView } from "./LineageView";
import { TeamView } from "./TeamView";
import {
  DigestList,
  formatDate,
  IdeaPanel,
  isPi,
  MemberLegend,
  memberColors,
  remember,
  remembered,
  SourceViewer,
} from "./ui";

const PLAY_STEP_MS = 1400;
const POLL_MS = 5000; // how often open browsers look for a teammate's edits
const UPDATE_POLL_MS = 1500;
const TABS = [
  { id: "lineage", label: "Lineage" },
  { id: "team", label: "Team" },
  { id: "architecture", label: "Architecture" },
] as const;
type Tab = (typeof TABS)[number]["id"];
const IDLE: UpdateStatus = { state: "idle", step: "", done: 0, total: 0, error: null, cost: 0 };

// Deep links reopen the same screen: ?ws=<workspace>&mode=history&view=architecture&at=<source>
// &idea=<idea>&module=<module>&quote=<source>:<line>. The browser extension opens
// ?panel=1&notion=<page url> beside Notion.
const initial = new URLSearchParams(window.location.search);
const initialTab = TABS.find((t) => t.id === initial.get("view"))?.id ?? "lineage";
const initialQuote = /^(.+):(\d+)$/.exec(initial.get("quote") ?? "");
const panel = initial.get("panel") === "1";
const sharedRoom = initial.get("converge"); // ?converge=<session id> reopens a CONVERGE Room
const notionUrl = initial.get("notion");

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState(initial.get("ws"));
  const [connecting, setConnecting] = useState(false);
  const [mode, setMode] = useState<"board" | "history">(
    initial.get("mode") === "history" ? "history" : "board",
  );
  const [latest, setLatest] = useState<LineageState | null>(null);
  const [board, setBoard] = useState<Board | null>(null);
  const [timeline, setTimeline] = useState<LineageState[]>([]);
  const [position, setPosition] = useState(0);
  const [selected, setSelected] = useState(initial.get("idea"));
  const [module, setModule] = useState(initial.get("module"));
  const [adding, setAdding] = useState(false);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [playing, setPlaying] = useState(false);
  const [update, setUpdate] = useState<UpdateStatus>(IDLE);
  const [quote, setQuote] = useState<{ source: string; line: number; quote?: string } | null>(
    initialQuote && { source: initialQuote[1], line: Number(initialQuote[2]) },
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [actingAs, setActingAs] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  const linkedSource = useRef(initial.get("at"));
  const revision = useRef<string | null>(null);

  useEffect(() => {
    Promise.all([fetchWorkspaces(), notionUrl ? findNotionPage(notionUrl) : null]).then(
      ([list, linked]) => {
        setWorkspaces(list);
        if (panel && notionUrl) {
          setWorkspaceId(linked);
          setConnecting(!linked);
          return;
        }
        setWorkspaceId((id) => (list.some((w) => w.id === id) ? id : (list[0]?.id ?? null)));
        setConnecting(list.length === 0);
      },
      (e: unknown) => setError(String(e)),
    );
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `reloads` asks for a fresh fetch
  useEffect(() => {
    if (!workspaceId) return;
    let stale = false;
    Promise.all([
      fetchState(workspaceId),
      fetchBoard(workspaceId),
      fetchRevision(workspaceId),
      fetchUpdate(workspaceId),
    ]).then(
      ([state, briefing, latestRevision, job]) => {
        if (stale) return;
        revision.current = latestRevision;
        setLatest(state);
        setBoard(briefing);
        setUpdate(job);
      },
      (e: unknown) => {
        // A connected page has no notes until its first update imports them: start (or join)
        // that update, whose poll reloads when it is done, instead of failing.
        if (String(e).startsWith("Error: 404")) {
          startUpdate(workspaceId).then(
            (job) => !stale && setUpdate(job),
            (problem: unknown) => setError(String(problem)),
          );
          return;
        }
        setError(String(e));
      },
    );
    return () => {
      stale = true;
    };
  }, [workspaceId, reloads]);

  // The history's time slider needs every meeting's state; fetch it only when opened.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `reloads` asks for a fresh fetch
  useEffect(() => {
    if (!workspaceId || mode !== "history") return;
    let stale = false;
    fetchTimeline(workspaceId).then(
      (states) => {
        if (stale) return;
        const linked = states.findIndex((s) => s.as_of === linkedSource.current);
        linkedSource.current = null;
        setTimeline(states);
        setPosition(linked >= 0 ? linked : states.length - 1);
      },
      (e: unknown) => setError(String(e)),
    );
    return () => {
      stale = true;
    };
  }, [workspaceId, mode, reloads]);

  const last = timeline.length - 1;
  const state = timeline[position];

  /** Fetch again, staying on the same meeting unless the latest one was showing. */
  const reload = useCallback(
    (stay: boolean) => {
      linkedSource.current = stay ? (state?.as_of ?? null) : null;
      setReloads((n) => n + 1);
    },
    [state],
  );

  useEffect(() => {
    if (!workspaceId || update.state !== "running") return;
    const timer = window.setInterval(async () => {
      const job = await fetchUpdate(workspaceId).catch(() => null);
      if (!job) return;
      setUpdate(job);
      if (job.state === "done") {
        revision.current = null; // our own change: the reload records the new revision
        setNotice("Up to date with the notes.");
        reload(false);
      } else if (job.state === "failed") {
        setNotice(`Update failed: ${job.error}`);
      }
    }, UPDATE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [workspaceId, update.state, reload]);

  useEffect(() => {
    if (!workspaceId) return;
    const timer = window.setInterval(async () => {
      const found = await fetchRevision(workspaceId).catch(() => revision.current);
      if (found && revision.current && found !== revision.current) {
        revision.current = found;
        setNotice("A teammate changed something: updated.");
        reload(position !== last);
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [workspaceId, reload, position, last]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!latest) return;
    const stored = remembered(`converge.actingAs.${latest.workspace.id}`);
    const members = latest.workspace.members;
    setActingAs((current) =>
      members.some((m) => m.id === current)
        ? current
        : members.some((m) => m.id === stored)
          ? (stored ?? "")
          : (members.find((m) => !isPi(m))?.id ?? members[0]?.id ?? ""),
    );
  }, [latest]);

  useEffect(() => {
    if (!playing) return;
    if (position >= last) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => setPosition((p) => p + 1), PLAY_STEP_MS);
    return () => window.clearTimeout(timer);
  }, [playing, position, last]);

  useEffect(() => {
    if (!workspaceId) return;
    const params = new URLSearchParams({ ws: workspaceId });
    if (panel) params.set("panel", "1");
    if (panel && notionUrl) params.set("notion", notionUrl);
    if (mode === "history") {
      params.set("mode", "history");
      if (state) params.set("at", state.as_of);
      if (tab !== "lineage") params.set("view", tab);
      if (selected) params.set("idea", selected);
      if (module) params.set("module", module);
    }
    if (quote?.line) params.set("quote", `${quote.source}:${quote.line}`);
    window.history.replaceState(null, "", `?${params}`);
  }, [workspaceId, mode, state, tab, selected, module, quote]);

  if (error) return <main className="p-8 text-sm">Could not reach the API: {error}</main>;

  const runUpdate = async (id = workspaceId) => {
    if (!id) return;
    try {
      setUpdate(await startUpdate(id));
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    }
  };

  if (connecting || !workspaceId) {
    return (
      <ConnectScreen
        initialUrl={notionUrl ?? ""}
        canCancel={!panel && workspaces.length > 0}
        onCancel={() => setConnecting(false)}
        onConnected={(workspace) => {
          setWorkspaces((list) => [...list.filter((w) => w.id !== workspace.id), workspace]);
          setWorkspaceId(workspace.id);
          setConnecting(false);
          runUpdate(workspace.id);
        }}
      />
    );
  }
  if (!latest) {
    return (
      <main className="p-8 text-sm text-muted">
        {update.state === "running" ? `${update.step}…` : "Loading…"}
      </main>
    );
  }

  const colors = memberColors(latest.workspace);
  const openQuote = (source: string, line: number, text?: string) =>
    setQuote({ source, line, quote: text });
  const chooseActingAs = (memberId: string) => {
    setActingAs(memberId);
    remember(`converge.actingAs.${latest.workspace.id}`, memberId);
  };

  return (
    <div className="min-h-screen">
      <header
        className={`flex flex-wrap items-center justify-between gap-3 border-b border-hairline bg-surface ${
          panel ? "px-4 py-3" : "px-8 py-4"
        }`}
      >
        <div className="min-w-0">
          <h1 className="truncate text-base font-semibold tracking-tight sm:text-lg">
            CONVERGE{panel && <span className="text-muted"> · {latest.workspace.name}</span>}
          </h1>
          {!panel && (
            <p className="text-xs text-ink-2">
              AI reads your team's meeting notes and shows what's agreed, what's still open, and who
              does what next.
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <div className="inline-flex rounded-lg border border-hairline p-0.5">
            {(["board", "history"] as const).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={mode === option}
                onClick={() => setMode(option)}
                className={`rounded-md px-3 py-1 capitalize ${
                  mode === option ? "bg-ink text-surface" : "text-ink-2 hover:bg-wash"
                }`}
              >
                {option === "board" ? "Now" : "History"}
              </button>
            ))}
          </div>
          {!panel && (
            <label className="flex items-center gap-2 text-ink-2">
              Team
              <select
                value={workspaceId}
                onChange={(event) => {
                  if (event.target.value === "+connect") {
                    setConnecting(true);
                    return;
                  }
                  setWorkspaceId(event.target.value);
                  setLatest(null);
                  setTimeline([]);
                  setSelected(null);
                  setModule(null);
                  setAdding(false);
                  setPlaying(false);
                }}
                className="max-w-64 rounded-lg border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
              >
                {workspaces.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
                <option value="+connect">+ Connect a Notion page…</option>
              </select>
            </label>
          )}
        </div>
      </header>

      {mode === "board" ? (
        <main className={panel ? "p-4" : "mx-auto max-w-[1400px] p-6"}>
          <BoardView
            state={latest}
            board={board}
            colors={colors}
            me={actingAs}
            onMe={chooseActingAs}
            update={update}
            onUpdate={() => runUpdate()}
            onOpenQuote={openQuote}
            onDetails={(moduleId) => {
              setModule(moduleId);
              setTab("architecture");
              setMode("history");
            }}
            openRoom={sharedRoom}
            onAdopted={(message) => {
              setNotice(message);
              revision.current = null;
              fetchUpdate(workspaceId).then(setUpdate, () => undefined);
              reload(false);
            }}
            compact={panel}
          />
        </main>
      ) : state ? (
        <History
          state={state}
          previous={timeline[position - 1]}
          timeline={timeline}
          position={position}
          setPosition={setPosition}
          playing={playing}
          setPlaying={setPlaying}
          tab={tab}
          setTab={setTab}
          selected={selected}
          setSelected={setSelected}
          module={module}
          setModule={setModule}
          adding={adding}
          setAdding={setAdding}
          actingAs={actingAs}
          onActingAs={chooseActingAs}
          onOpenQuote={openQuote}
          onEdited={(message) => {
            setNotice(message);
            revision.current = null;
            reload(false);
          }}
        />
      ) : (
        <main className="p-8 text-sm text-muted">Loading the history…</main>
      )}

      {notice && (
        <p
          role="status"
          className={`fixed right-6 rounded-lg bg-ink px-4 py-2 text-sm text-surface shadow-lg ${
            panel ? "bottom-24" : "bottom-6"
          }`}
        >
          {notice}
        </p>
      )}

      {quote && (
        <SourceViewer
          workspaceId={workspaceId}
          sourceId={quote.source}
          line={quote.line}
          quote={quote.quote}
          onClose={() => setQuote(null)}
        />
      )}
    </div>
  );
}

function ConnectScreen({
  initialUrl,
  canCancel,
  onCancel,
  onConnected,
}: {
  initialUrl: string;
  canCancel: boolean;
  onCancel: () => void;
  onConnected: (workspace: Workspace) => void;
}) {
  const [url, setUrl] = useState(initialUrl);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      onConnected(await connectNotion(url));
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="mx-auto max-w-xl space-y-5 p-6">
      <h1 className="text-lg font-semibold">Connect your team's Notion page</h1>
      <ol className="list-decimal space-y-2 pl-5 text-sm text-ink-2">
        <li>
          In Notion, open the page that holds your meeting notes (sub-pages or a database of notes
          both work).
        </li>
        <li>
          Open the page menu (•••) → <b>Connections</b> → add your CONVERGE integration.
        </li>
        <li>Paste the page link below. CONVERGE reads it and writes your team's briefing.</li>
      </ol>
      <form onSubmit={submit} className="space-y-3">
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://www.notion.so/…"
          required
          className="w-full rounded-lg border border-hairline bg-surface px-3 py-2 text-sm"
        />
        {problem && <p className="text-sm text-[#b42318]">{problem}</p>}
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={busy}
            className="rounded-lg bg-ink px-4 py-2 text-sm font-medium text-surface hover:bg-ink-2 disabled:opacity-60"
          >
            {busy ? "Connecting…" : "Connect and update"}
          </button>
          {canCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="rounded-lg border border-hairline px-4 py-2 text-sm"
            >
              Cancel
            </button>
          )}
        </div>
      </form>
    </main>
  );
}

interface HistoryProps {
  state: LineageState;
  previous: LineageState | undefined;
  timeline: LineageState[];
  position: number;
  setPosition: (position: number) => void;
  playing: boolean;
  setPlaying: (playing: boolean) => void;
  tab: Tab;
  setTab: (tab: Tab) => void;
  selected: string | null;
  setSelected: (idea: string | null) => void;
  module: string | null;
  setModule: (module: string | null) => void;
  adding: boolean;
  setAdding: (adding: boolean) => void;
  actingAs: string;
  onActingAs: (memberId: string) => void;
  onOpenQuote: (sourceId: string, line: number, quote?: string) => void;
  onEdited: (message: string) => void;
}

/** Every meeting on a time slider: idea lineage, team stances, and the architecture as it
 * stood, with what each meeting changed. */
function History(props: HistoryProps) {
  const { state, timeline, position, setPosition, playing, setPlaying, tab, setTab } = props;
  const { selected, setSelected, module, setModule, adding, setAdding, onOpenQuote } = props;
  const colors = memberColors(state.workspace);
  const last = timeline.length - 1;
  const current = state.sources[position];
  const editable = position === last;
  const selectIdea = (id: string) => {
    setSelected(id);
    setModule(null);
    setAdding(false);
  };
  const selectModule = (id: string) => {
    setModule(id);
    setAdding(false);
    setTab("architecture");
  };
  const edit = async (change: Omit<EditRequest, "member">) => {
    try {
      const result = await postEdit(state.workspace.id, { ...change, member: props.actingAs });
      setAdding(false);
      setModule(change.action === "remove" ? null : result.module || null);
      props.onEdited(
        `Saved to the session note${
          result.notion === "synced"
            ? " and written to Notion"
            : result.notion === "failed"
              ? " (Notion write failed)"
              : ""
        }.`,
      );
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : String(e);
    }
  };
  const collaboration = {
    editable,
    actingAs: props.actingAs,
    onActingAs: props.onActingAs,
    onEdit: edit,
  };

  let aside = (
    <div className="space-y-3 text-sm text-ink-2">
      <h2 className="text-base font-semibold text-ink">
        {tab === "architecture" ? "Pick a module" : "Pick an idea"}
      </h2>
      <p>
        {tab === "architecture"
          ? "Click a box to see who works on it, how it is wired, and every change with its quote. At the latest meeting you can reassign it, comment, or propose new modules."
          : "Click a lane or a dot to see who backs the idea, who challenges it, the open questions, and the original quote behind every claim."}
      </p>
    </div>
  );
  if (tab === "architecture" && adding) {
    aside = <ModuleForm state={state} onCancel={() => setAdding(false)} {...collaboration} />;
  } else if (tab === "architecture" && module) {
    aside = (
      <ModulePanel
        key={module}
        state={state}
        moduleId={module}
        colors={colors}
        onSelectModule={selectModule}
        onSelectIdea={selectIdea}
        onOpenQuote={onOpenQuote}
        {...collaboration}
      />
    );
  } else if (selected) {
    aside = (
      <IdeaPanel
        state={state}
        ideaId={selected}
        colors={colors}
        onSelectIdea={selectIdea}
        onOpenQuote={onOpenQuote}
      />
    );
  }

  return (
    <main className="grid gap-6 p-6 lg:grid-cols-[minmax(0,1fr)_400px]">
      <section className="min-w-0 rounded-xl border border-hairline bg-surface p-5">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">{state.workspace.name}</h2>
            <p className="text-xs text-muted">{state.workspace.description}</p>
          </div>
          <MemberLegend workspace={state.workspace} colors={colors} />
        </div>

        <div className="mb-4 inline-flex rounded-lg border border-hairline p-0.5 text-sm">
          {TABS.map((option) => (
            <button
              key={option.id}
              type="button"
              aria-pressed={tab === option.id}
              onClick={() => setTab(option.id)}
              className={`rounded-md px-3 py-1 ${
                tab === option.id ? "bg-ink text-surface" : "text-ink-2 hover:bg-wash"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>

        {tab === "lineage" && (
          <LineageView state={state} colors={colors} selected={selected} onSelect={selectIdea} />
        )}
        {tab === "team" && (
          <TeamView
            state={state}
            colors={colors}
            selected={selected}
            onSelect={selectIdea}
            onOpenQuote={onOpenQuote}
          />
        )}
        {tab === "architecture" && (
          <ArchitectureView
            state={state}
            previous={props.previous}
            colors={colors}
            selected={module}
            editable={editable}
            onSelect={selectModule}
            onAdd={() => {
              setAdding(true);
              setModule(null);
            }}
          />
        )}

        <div className="mt-4 flex items-center gap-4 border-t border-hairline pt-4">
          <button
            type="button"
            onClick={() => {
              if (!playing && position >= last) setPosition(0);
              setPlaying(!playing);
            }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-ink px-3 py-1.5 text-sm font-medium text-surface hover:bg-ink-2"
          >
            {playing ? <Pause size={14} /> : <Play size={14} />}
            {playing ? "Pause" : "Play"}
          </button>
          <input
            type="range"
            min={0}
            max={last}
            value={position}
            onChange={(event) => {
              setPlaying(false);
              setPosition(Number(event.target.value));
            }}
            aria-label="Meeting"
            aria-valuetext={`${formatDate(current.occurred_at)}, ${current.title}`}
            className="min-w-0 flex-1 accent-ink"
          />
          <div className="w-72 shrink-0 text-right">
            <p className="text-xs text-muted">
              {formatDate(current.occurred_at)} ·{" "}
              {current.kind === "session" ? "session" : "meeting"} {position + 1} of{" "}
              {timeline.length}
            </p>
            <p className="truncate text-sm font-medium" title={current.title}>
              {current.title}
            </p>
          </div>
        </div>

        <div className="mt-4">
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">
            What changed in this meeting
          </h3>
          <DigestList state={state} onSelectIdea={selectIdea} onSelectModule={selectModule} />
        </div>
        {state.issues.length > 0 && (
          <p className="mt-3 text-xs text-muted">
            {state.issues.length} claims were dropped because their quote or references did not
            check out.
          </p>
        )}
      </section>

      <aside className="rounded-xl border border-hairline bg-surface p-5 lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)] lg:self-start lg:overflow-y-auto">
        {aside}
      </aside>
    </main>
  );
}
