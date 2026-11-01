// The idea lineage as a git-style graph: one lane per idea, one column per meeting, with
// branch and merge curves. A table view carries the same data for readers who cannot use color.

import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import type { IdeaView, LineageState, LocatedMention, StanceCell } from "./api";
import {
  columnLabel,
  Dot,
  FOR_STANCES,
  formatDate,
  lookup,
  NEUTRAL,
  StatusChip,
  truncate,
} from "./ui";

const GUTTER = 240; // left column holding the lane labels
const TOP = 36; // column headers
const ROW = 46;
const PAD = 20;
const MIN_COLUMN = 56;
const MAX_COLUMN = 112;
const RING = "var(--color-surface)"; // 2px surface ring keeps dots legible where lines cross

interface Props {
  state: LineageState;
  colors: Record<string, string>;
  selected: string | null;
  onSelect: (ideaId: string) => void;
}

export function LineageView(props: Props) {
  const [view, setView] = useState<"graph" | "table">("graph");
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-4">
        <p className="text-xs text-muted">
          Dot size shows how much an idea was discussed in that meeting. Click an idea to see its
          evidence.
        </p>
        <div className="inline-flex shrink-0 rounded-lg border border-hairline p-0.5 text-xs">
          {(["graph", "table"] as const).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={view === option}
              onClick={() => setView(option)}
              className={`rounded-md px-2.5 py-1 capitalize ${
                view === option ? "bg-ink text-surface" : "text-ink-2 hover:bg-wash"
              }`}
            >
              {option}
            </button>
          ))}
        </div>
      </div>
      {view === "graph" ? <LineageGraph {...props} /> : <LineageTable {...props} />}
    </div>
  );
}

// --- Graph ---------------------------------------------------------------------

interface Hover {
  left: number;
  top: number;
  above: boolean;
  heading: string;
  detail: string;
  lines: string[];
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

const range = (from: number, to: number) =>
  Array.from({ length: Math.max(0, to - from) }, (_, i) => from + i);

/** Columns where each idea was born, refined, or discussed, with that meeting's mentions. */
function activity(state: LineageState, columnOf: Map<string, number>) {
  const byIdea = new Map<string, Map<number, LocatedMention[]>>();
  const touch = (ideaId: string, sourceId: string) => {
    const columns = byIdea.get(ideaId) ?? new Map<number, LocatedMention[]>();
    byIdea.set(ideaId, columns);
    const column = columnOf.get(sourceId) ?? 0;
    const mentions = columns.get(column) ?? [];
    columns.set(column, mentions);
    return mentions;
  };
  for (const idea of state.ideas) {
    touch(idea.id, idea.born);
    for (const version of idea.versions) touch(idea.id, version.source);
  }
  for (const mention of state.mentions) touch(mention.idea, mention.source).push(mention);
  return byIdea;
}

const isClosed = (idea: IdeaView) => idea.status === "parked" || idea.status === "dropped";

function LineageGraph({ state, colors, selected, onSelect }: Props) {
  const { ref, width: available } = useWidth();
  const [hover, setHover] = useState<Hover | null>(null);
  const { name } = lookup(state);
  const sources = state.sources;
  const columnOf = useMemo(() => new Map(sources.map((s, i) => [s.id, i])), [sources]);
  const nodes = useMemo(() => activity(state, columnOf), [state, columnOf]);

  const fitted = (available - GUTTER - PAD) / sources.length;
  const column = Math.max(MIN_COLUMN, Math.min(MAX_COLUMN, fitted || MIN_COLUMN));
  const width = GUTTER + column * sources.length + PAD;
  const height = TOP + ROW * state.ideas.length + PAD;
  const cutoff = columnOf.get(state.as_of) ?? sources.length - 1;
  const spansYears = new Set(sources.map((s) => s.occurred_at.slice(0, 4))).size > 1;
  const laneOf = new Map(state.ideas.map((idea, i) => [idea.id, i]));
  const ideaById = new Map(state.ideas.map((idea) => [idea.id, idea]));
  const x = (col: number) => GUTTER + column * (col + 0.5);
  const y = (ideaId: string) => TOP + ROW * ((laneOf.get(ideaId) ?? 0) + 0.5);
  const colorOf = (idea: IdeaView) => (idea.owner && colors[idea.owner]) || NEUTRAL;
  const laneEnd = (idea: IdeaView) =>
    isClosed(idea) ? (columnOf.get(idea.last_active) ?? cutoff) : cutoff;

  const activate = (event: KeyboardEvent, ideaId: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(ideaId);
    }
  };

  const showNode = (idea: IdeaView, col: number, mentions: LocatedMention[]) => {
    const source = sources[col];
    const tally = new Map<string, number>();
    for (const mention of mentions) {
      const key = `${name(mention.member)} · ${mention.stance}`;
      tally.set(key, (tally.get(key) ?? 0) + 1);
    }
    setHover({
      left: Math.min(Math.max(x(col), 140), width - 140),
      top: y(idea.id),
      above: y(idea.id) > height / 2,
      heading: idea.title,
      detail: `${formatDate(source.occurred_at)} · ${source.title}`,
      lines: tally.size
        ? [...tally].map(([key, count]) => (count > 1 ? `${key} ×${count}` : key))
        : ["Created or refined here"],
    });
  };

  return (
    <div ref={ref} className="relative overflow-x-auto">
      <svg
        width={width}
        height={height}
        className="block select-none"
        aria-labelledby="lineage-title"
      >
        <title id="lineage-title">{`Idea lineage across ${sources.length} meetings`}</title>

        {sources.map((source, col) => (
          <g key={source.id}>
            {col === cutoff && (
              <rect
                x={x(col) - column / 2 + 3}
                y={6}
                width={column - 6}
                height={height - 10}
                rx={8}
                className="fill-wash"
              />
            )}
            <line
              x1={x(col)}
              x2={x(col)}
              y1={TOP}
              y2={height - PAD / 2}
              className="stroke-hairline"
            />
            <text
              x={x(col)}
              y={TOP - 14}
              textAnchor="middle"
              className={`text-[11px] ${
                col === cutoff
                  ? "fill-ink font-semibold"
                  : col < cutoff
                    ? "fill-ink-2"
                    : "fill-muted"
              }`}
            >
              {columnLabel(source.occurred_at, spansYears)}
            </text>
          </g>
        ))}

        {selected && laneOf.has(selected) && (
          <rect
            x={4}
            y={y(selected) - ROW / 2 + 4}
            width={width - 8}
            height={ROW - 8}
            rx={8}
            className="fill-wash"
          />
        )}

        {state.edges.map((edge) => {
          const parent = ideaById.get(edge.parent);
          const child = ideaById.get(edge.child);
          if (!parent || !child) return null;
          const at = columnOf.get(edge.source) ?? 0;
          // Leave the parent lane one meeting earlier and arrive at the child's first dot.
          const x0 = x(Math.max(at - 1, columnOf.get(parent.born) ?? 0));
          const [y0, x1, y1] = [y(parent.id), x(at), y(child.id)];
          const mid = (y0 + y1) / 2;
          return (
            <path
              key={`${edge.parent}>${edge.child}`}
              className="appear"
              d={`M ${x0} ${y0} C ${x0} ${mid}, ${x1} ${mid}, ${x1} ${y1}`}
              fill="none"
              stroke={colorOf(child)}
              strokeWidth={2}
              strokeLinecap="round"
              opacity={isClosed(child) ? 0.4 : 1}
            />
          );
        })}

        {state.ideas.map((idea) => {
          const color = colorOf(idea);
          const ly = y(idea.id);
          const closed = isClosed(idea);
          const end = laneEnd(idea);
          const quietFrom = columnOf.get(idea.last_active) ?? end;
          const marker = x(end) + column * 0.38;
          return (
            <g key={idea.id}>
              {range(columnOf.get(idea.born) ?? 0, end).map((col) => (
                <line
                  key={col}
                  className="appear"
                  x1={x(col)}
                  x2={x(col + 1)}
                  y1={ly}
                  y2={ly}
                  stroke={color}
                  strokeWidth={2}
                  strokeLinecap="round"
                  opacity={closed ? 0.4 : idea.status === "dormant" && col >= quietFrom ? 0.25 : 1}
                />
              ))}

              {closed && (
                <g className="appear" opacity={0.75}>
                  <line x1={x(end)} x2={marker} y1={ly} y2={ly} stroke={color} strokeWidth={2} />
                  {idea.status === "parked" ? (
                    <circle cx={marker} cy={ly} r={5} fill={RING} stroke={color} strokeWidth={2} />
                  ) : (
                    <path
                      d={`M ${marker - 4} ${ly - 4} L ${marker + 4} ${ly + 4} M ${marker + 4} ${ly - 4} L ${marker - 4} ${ly + 4}`}
                      stroke={color}
                      strokeWidth={2}
                      strokeLinecap="round"
                    />
                  )}
                </g>
              )}

              {[...(nodes.get(idea.id) ?? [])].map(([col, mentions]) => (
                <g key={`node-${col}`} className="appear">
                  <circle
                    cx={x(col)}
                    cy={ly}
                    r={4 + Math.min(mentions.length, 4)}
                    fill={color}
                    stroke={RING}
                    strokeWidth={2}
                    opacity={closed ? 0.55 : 1}
                  />
                  {/* biome-ignore lint/a11y/useSemanticElements: SVG has no <button>; role + key handlers make the 24px hit target operable */}
                  <circle
                    cx={x(col)}
                    cy={ly}
                    r={12}
                    fill="transparent"
                    className="cursor-pointer outline-none"
                    role="button"
                    tabIndex={0}
                    aria-label={`${idea.title}, ${formatDate(sources[col].occurred_at)}, ${mentions.length} quotes`}
                    onMouseEnter={() => showNode(idea, col, mentions)}
                    onMouseLeave={() => setHover(null)}
                    onFocus={() => showNode(idea, col, mentions)}
                    onBlur={() => setHover(null)}
                    onClick={() => onSelect(idea.id)}
                    onKeyDown={(event) => activate(event, idea.id)}
                  />
                </g>
              ))}

              {/* biome-ignore lint/a11y/useSemanticElements: SVG has no <button>; the lane label behaves like one */}
              <g
                role="button"
                tabIndex={0}
                aria-label={`${idea.title}, owned by ${name(idea.owner)}, ${idea.status}`}
                className="cursor-pointer outline-none"
                onClick={() => onSelect(idea.id)}
                onKeyDown={(event) => activate(event, idea.id)}
              >
                <rect
                  x={0}
                  y={ly - ROW / 2 + 4}
                  width={GUTTER - 16}
                  height={ROW - 8}
                  fill="transparent"
                />
                <circle cx={16} cy={ly - 5} r={4} fill={color} />
                <text
                  x={28}
                  y={ly - 1}
                  opacity={closed ? 0.6 : 1}
                  className={`fill-ink text-[13px] ${idea.id === selected ? "font-semibold" : ""}`}
                >
                  {truncate(idea.title, 30)}
                </text>
                <text x={28} y={ly + 14} className="fill-muted text-[11px]">
                  {`${name(idea.owner)} · ${idea.status}`}
                </text>
              </g>
            </g>
          );
        })}
      </svg>

      {hover && (
        <div
          role="tooltip"
          className={`pointer-events-none absolute z-10 w-64 -translate-x-1/2 rounded-lg border border-hairline bg-surface px-3 py-2 text-xs shadow-lg ${
            hover.above ? "-translate-y-full" : ""
          }`}
          style={{ left: hover.left, top: hover.above ? hover.top - 16 : hover.top + 16 }}
        >
          <p className="font-semibold text-ink">{hover.heading}</p>
          <p className="text-muted">{hover.detail}</p>
          <ul className="mt-1 space-y-0.5 text-ink-2">
            {hover.lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-muted">
        <span>● active</span>
        <span>◌ dormant: lane fades after 3 meetings without discussion</span>
        <span>○ parked</span>
        <span>✕ dropped</span>
        <span>curves: branch or merge</span>
      </div>
    </div>
  );
}

// --- Table (the same data without relying on color) ----------------------------

function LineageTable({ state, colors, selected, onSelect }: Props) {
  const { name, date } = lookup(state);
  const stancesOn = (ideaId: string, pick: (cell: StanceCell) => boolean) =>
    state.stances.filter((cell) => cell.idea === ideaId && pick(cell)).length;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-[11px] uppercase tracking-wide text-muted">
          <tr>
            <th className="py-2 pr-3 font-semibold">Idea</th>
            <th className="py-2 pr-3 font-semibold">Owner</th>
            <th className="py-2 pr-3 font-semibold">Status</th>
            <th className="py-2 pr-3 font-semibold">Since</th>
            <th className="py-2 pr-3 font-semibold">Last discussed</th>
            <th className="py-2 pr-3 text-right font-semibold">Quotes</th>
            <th className="py-2 pr-3 text-right font-semibold">For</th>
            <th className="py-2 text-right font-semibold">Against</th>
          </tr>
        </thead>
        <tbody>
          {state.ideas.map((idea) => (
            <tr
              key={idea.id}
              className={`border-t border-hairline ${idea.id === selected ? "bg-wash" : ""}`}
            >
              <td className="py-2 pr-3">
                <button
                  type="button"
                  onClick={() => onSelect(idea.id)}
                  className="text-left font-medium hover:underline"
                >
                  {idea.title}
                </button>
              </td>
              <td className="py-2 pr-3">
                <span className="flex items-center gap-1.5 text-ink-2">
                  <Dot color={(idea.owner && colors[idea.owner]) || NEUTRAL} />
                  {name(idea.owner)}
                </span>
              </td>
              <td className="py-2 pr-3">
                <StatusChip status={idea.status} />
              </td>
              <td className="py-2 pr-3 tabular-nums text-ink-2">{date(idea.born)}</td>
              <td className="py-2 pr-3 tabular-nums text-ink-2">{date(idea.last_active)}</td>
              <td className="py-2 pr-3 text-right tabular-nums">
                {state.mentions.filter((m) => m.idea === idea.id).length}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums">
                {stancesOn(idea.id, (cell) => FOR_STANCES.includes(cell.latest))}
              </td>
              <td className="py-2 text-right tabular-nums">
                {stancesOn(idea.id, (cell) => cell.latest === "challenge")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
