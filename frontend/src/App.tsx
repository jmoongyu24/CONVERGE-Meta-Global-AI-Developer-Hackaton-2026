import { Pause, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArchitectureView, ModuleForm, ModulePanel } from "./ArchitectureView";
import {
  type EditRequest,
  fetchRevision,
  fetchTimeline,
  fetchWorkspaces,
  type LineageState,
  postEdit,
  type Workspace,
} from "./api";
import { LineageView } from "./LineageView";
import { TeamView } from "./TeamView";
import { DigestList, formatDate, IdeaPanel, MemberLegend, memberColors, SourceViewer } from "./ui";

const PLAY_STEP_MS = 1400;
const POLL_MS = 5000; // how often open browsers look for a teammate's edits
const VIEWS = [
  { id: "lineage", label: "Lineage" },
  { id: "team", label: "Team" },
  { id: "architecture", label: "Architecture" },
] as const;
type View = (typeof VIEWS)[number]["id"];

// Deep links: ?ws=<workspace>&at=<source>&idea=<idea>&module=<module>&view=architecture
// reopen the same screen, and &quote=<source>:<line> opens the note behind a claim.
const initial = new URLSearchParams(window.location.search);
const initialView = VIEWS.find((v) => v.id === initial.get("view"))?.id ?? "lineage";
const initialQuote = /^(.+):(\d+)$/.exec(initial.get("quote") ?? "");

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState(initial.get("ws"));
  const [timeline, setTimeline] = useState<LineageState[]>([]);
  const [position, setPosition] = useState(0);
  const [selected, setSelected] = useState(initial.get("idea"));
  const [module, setModule] = useState(initial.get("module"));
  const [adding, setAdding] = useState(false);
  const [view, setView] = useState<View>(initialView);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [quote, setQuote] = useState<{ source: string; line: number } | null>(
    initialQuote && { source: initialQuote[1], line: Number(initialQuote[2]) },
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [actingAs, setActingAs] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  const linkedSource = useRef(initial.get("at"));
  const revision = useRef<string | null>(null);

  useEffect(() => {
    fetchWorkspaces().then(
      (list) => {
        setWorkspaces(list);
        setWorkspaceId((id) => (list.some((w) => w.id === id) ? id : (list[0]?.id ?? null)));
      },
      (e: unknown) => setError(String(e)),
    );
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `reloads` asks for a fresh fetch
  useEffect(() => {
    if (!workspaceId) return;
    let stale = false;
    setLoading(true);
    Promise.all([fetchTimeline(workspaceId), fetchRevision(workspaceId)]).then(
      ([states, latest]) => {
        if (stale) return;
        const linked = states.findIndex((s) => s.as_of === linkedSource.current);
        linkedSource.current = null;
        revision.current = latest;
        setTimeline(states);
        setPosition(linked >= 0 ? linked : states.length - 1);
        setLoading(false);
      },
      (e: unknown) => setError(String(e)),
    );
    return () => {
      stale = true;
    };
  }, [workspaceId, reloads]);

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
    if (!workspaceId) return;
    const timer = window.setInterval(async () => {
      const latest = await fetchRevision(workspaceId).catch(() => revision.current);
      if (latest && revision.current && latest !== revision.current) {
        revision.current = latest;
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
    if (!workspaceId) return;
    const stored = window.localStorage.getItem(`converge.actingAs.${workspaceId}`);
    const members = workspaces.find((w) => w.id === workspaceId)?.members ?? [];
    setActingAs(members.some((m) => m.id === stored) ? (stored ?? "") : (members[0]?.id ?? ""));
  }, [workspaceId, workspaces]);

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
    if (!state || state.workspace.id !== workspaceId) return;
    const params = new URLSearchParams({ ws: state.workspace.id, at: state.as_of });
    if (selected) params.set("idea", selected);
    if (module) params.set("module", module);
    if (view !== "lineage") params.set("view", view);
    if (quote) params.set("quote", `${quote.source}:${quote.line}`);
    window.history.replaceState(null, "", `?${params}`);
  }, [workspaceId, state, selected, module, view, quote]);

  if (error) return <main className="p-8 text-sm">Could not reach the API: {error}</main>;
  if (!state) return <main className="p-8 text-sm text-muted">Loading lineage…</main>;

  const colors = memberColors(state.workspace);
  const current = state.sources[position];
  const editable = position === last;
  const openQuote = (source: string, line: number) => setQuote({ source, line });
  const selectIdea = (id: string) => {
    setSelected(id);
    setModule(null);
    setAdding(false);
  };
  const selectModule = (id: string) => {
    setModule(id);
    setAdding(false);
    setView("architecture");
  };
  const chooseActingAs = (memberId: string) => {
    setActingAs(memberId);
    window.localStorage.setItem(`converge.actingAs.${state.workspace.id}`, memberId);
  };
  const edit = async (change: Omit<EditRequest, "member">) => {
    try {
      const result = await postEdit(state.workspace.id, { ...change, member: actingAs });
      setNotice(
        `Saved to the session note${
          result.notion === "synced"
            ? " and written to Notion"
            : result.notion === "failed"
              ? " (Notion write failed)"
              : ""
        }.`,
      );
      setAdding(false);
      setModule(change.action === "remove" ? null : result.module || null);
      revision.current = null; // our own change: the reload records the new revision
      reload(false);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : String(e);
    }
  };
  const togglePlay = () => {
    if (playing) {
      setPlaying(false);
    } else {
      if (position >= last) setPosition(0);
      setPlaying(true);
    }
  };
  const collaboration = { editable, actingAs, onActingAs: chooseActingAs, onEdit: edit };

  let panel = (
    <div className="space-y-3 text-sm text-ink-2">
      <h2 className="text-base font-semibold text-ink">
        {view === "architecture" ? "Pick a module" : "Pick an idea"}
      </h2>
      <p>
        {view === "architecture"
          ? "Click a box to see who builds it, how it is wired, what the team says about it, and every change with its quote. At the latest meeting you can reassign it, comment, or propose new modules."
          : "Click a lane or a dot to see who backs the idea, who challenges it, the open questions, and the original quote behind every claim."}
      </p>
      <p className="text-muted">
        {state.ideas.length} ideas so far ·{" "}
        {state.ideas.filter((i) => i.status === "active").length} active ·{" "}
        {state.modules.filter((m) => m.status !== "removed").length} modules ·{" "}
        {state.mentions.length} attributed quotes
      </p>
    </div>
  );
  if (view === "architecture" && adding) {
    panel = <ModuleForm state={state} onCancel={() => setAdding(false)} {...collaboration} />;
  } else if (view === "architecture" && module) {
    panel = (
      <ModulePanel
        key={module}
        state={state}
        moduleId={module}
        colors={colors}
        onSelectModule={selectModule}
        onSelectIdea={selectIdea}
        onOpenQuote={openQuote}
        {...collaboration}
      />
    );
  } else if (selected) {
    panel = (
      <IdeaPanel
        state={state}
        ideaId={selected}
        colors={colors}
        onSelectIdea={selectIdea}
        onOpenQuote={openQuote}
      />
    );
  }

  return (
    <div className="min-h-screen">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-hairline bg-surface px-8 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Converge</h1>
          <p className="text-xs text-muted">
            Who is working on what, how each idea evolved, and where the team should converge.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-ink-2">
          Team
          <select
            value={workspaceId ?? ""}
            onChange={(event) => {
              setWorkspaceId(event.target.value);
              setSelected(null);
              setModule(null);
              setAdding(false);
              setPlaying(false);
            }}
            className="rounded-lg border border-hairline bg-surface px-3 py-1.5 text-sm text-ink"
          >
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </label>
      </header>

      <main
        className={`grid gap-6 p-6 transition-opacity lg:grid-cols-[minmax(0,1fr)_400px] ${
          loading ? "opacity-60" : ""
        }`}
      >
        <section className="min-w-0 rounded-xl border border-hairline bg-surface p-5">
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">{state.workspace.name}</h2>
              <p className="text-xs text-muted">{state.workspace.description}</p>
            </div>
            <MemberLegend workspace={state.workspace} colors={colors} />
          </div>

          <div className="mb-4 inline-flex rounded-lg border border-hairline p-0.5 text-sm">
            {VIEWS.map((option) => (
              <button
                key={option.id}
                type="button"
                aria-pressed={view === option.id}
                onClick={() => setView(option.id)}
                className={`rounded-md px-3 py-1 ${
                  view === option.id ? "bg-ink text-surface" : "text-ink-2 hover:bg-wash"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>

          {view === "lineage" && (
            <LineageView state={state} colors={colors} selected={selected} onSelect={selectIdea} />
          )}
          {view === "team" && (
            <TeamView
              state={state}
              colors={colors}
              selected={selected}
              onSelect={selectIdea}
              onOpenQuote={openQuote}
            />
          )}
          {view === "architecture" && (
            <ArchitectureView
              state={state}
              previous={timeline[position - 1]}
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
              onClick={togglePlay}
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
          {panel}
        </aside>
      </main>

      {notice && (
        <p
          role="status"
          className="fixed right-6 bottom-6 rounded-lg bg-ink px-4 py-2 text-sm text-surface shadow-lg"
        >
          {notice}
        </p>
      )}

      {quote && workspaceId && (
        <SourceViewer
          workspaceId={workspaceId}
          sourceId={quote.source}
          line={quote.line}
          onClose={() => setQuote(null)}
        />
      )}
    </div>
  );
}
