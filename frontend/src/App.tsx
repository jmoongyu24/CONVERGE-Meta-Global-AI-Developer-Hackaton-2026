import { Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { fetchTimeline, fetchWorkspaces, type LineageState, type Workspace } from "./api";
import { LineageView } from "./LineageView";
import { DigestList, formatDate, IdeaPanel, MemberLegend, memberColors, SourceViewer } from "./ui";

const PLAY_STEP_MS = 1400;

// Deep links: ?ws=<workspace>&at=<source>&idea=<idea> reopen the same view.
const initial = new URLSearchParams(window.location.search);

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState(initial.get("ws"));
  const [timeline, setTimeline] = useState<LineageState[]>([]);
  const [position, setPosition] = useState(0);
  const [selected, setSelected] = useState(initial.get("idea"));
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [quote, setQuote] = useState<{ source: string; line: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const linkedSource = useRef(initial.get("at"));

  useEffect(() => {
    fetchWorkspaces().then(
      (list) => {
        setWorkspaces(list);
        setWorkspaceId((id) => (list.some((w) => w.id === id) ? id : (list[0]?.id ?? null)));
      },
      (e: unknown) => setError(String(e)),
    );
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    let stale = false;
    setLoading(true);
    fetchTimeline(workspaceId).then(
      (states) => {
        if (stale) return;
        const linked = states.findIndex((s) => s.as_of === linkedSource.current);
        linkedSource.current = null;
        setTimeline(states);
        setPosition(linked >= 0 ? linked : states.length - 1);
        setLoading(false);
      },
      (e: unknown) => setError(String(e)),
    );
    return () => {
      stale = true;
    };
  }, [workspaceId]);

  const last = timeline.length - 1;
  useEffect(() => {
    if (!playing) return;
    if (position >= last) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => setPosition((p) => p + 1), PLAY_STEP_MS);
    return () => window.clearTimeout(timer);
  }, [playing, position, last]);

  const state = timeline[position];

  useEffect(() => {
    if (!state || state.workspace.id !== workspaceId) return;
    const params = new URLSearchParams({ ws: state.workspace.id, at: state.as_of });
    if (selected) params.set("idea", selected);
    window.history.replaceState(null, "", `?${params}`);
  }, [workspaceId, state, selected]);

  if (error) return <main className="p-8 text-sm">Could not reach the API: {error}</main>;
  if (!state) return <main className="p-8 text-sm text-muted">Loading lineage…</main>;

  const colors = memberColors(state.workspace);
  const current = state.sources[position];
  const togglePlay = () => {
    if (playing) {
      setPlaying(false);
    } else {
      if (position >= last) setPosition(0);
      setPlaying(true);
    }
  };

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

          <LineageView state={state} colors={colors} selected={selected} onSelect={setSelected} />

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
                {formatDate(current.occurred_at)} · meeting {position + 1} of {timeline.length}
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
            <DigestList state={state} onSelectIdea={setSelected} />
          </div>
          {state.issues.length > 0 && (
            <p className="mt-3 text-xs text-muted">
              {state.issues.length} claims were dropped because their quote or references did not
              check out.
            </p>
          )}
        </section>

        <aside className="rounded-xl border border-hairline bg-surface p-5 lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)] lg:self-start lg:overflow-y-auto">
          {selected ? (
            <IdeaPanel
              state={state}
              ideaId={selected}
              colors={colors}
              onSelectIdea={setSelected}
              onOpenQuote={(source, line) => setQuote({ source, line })}
            />
          ) : (
            <div className="space-y-3 text-sm text-ink-2">
              <h2 className="text-base font-semibold text-ink">Pick an idea</h2>
              <p>
                Click a lane or a dot to see who backs the idea, who challenges it, the open
                questions, and the original quote behind every claim.
              </p>
              <p className="text-muted">
                {state.ideas.length} ideas so far ·{" "}
                {state.ideas.filter((i) => i.status === "active").length} active ·{" "}
                {state.mentions.length} attributed quotes
              </p>
            </div>
          )}
        </aside>
      </main>

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
