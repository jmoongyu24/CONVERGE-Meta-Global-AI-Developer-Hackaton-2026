// The team's architecture at the current point in time, redrawn from the notes: each module
// is tinted with the color of the member who builds it, solid once adopted and dashed while
// proposed, and whatever the current meeting changed is badged on the diagram. ELK's layered
// algorithm places the boxes, so any architecture the notes describe gets a readable layout.
// The module panel and form below turn the diagram into a shared, editable workspace.

import type { ELK, ElkExtendedEdge, ElkNode } from "elkjs/lib/elk.bundled.js";
import { Plus } from "lucide-react";
import { type KeyboardEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import type { EditRequest, LineageState, ModuleView } from "./api";
import { Dot, FOR_STANCES, lookup, NEUTRAL, StanceChip, truncate } from "./ui";

// ELK is large, so it loads with the first diagram instead of with the app.
let engine: Promise<ELK> | null = null;
const layoutEngine = () => {
  engine ??= import("elkjs/lib/elk.bundled.js").then(({ default: Elk }) => new Elk());
  return engine;
};
const BOX_HEIGHT = 46;
const LAYOUT_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.hierarchyHandling": "INCLUDE_CHILDREN",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.spacing.nodeNodeBetweenLayers": "40",
  "elk.spacing.nodeNode": "20",
  "elk.padding": "[top=22,left=12,bottom=12,right=26]",
  // Absolute coordinates for every box and wire, whatever block they sit in.
  "elk.json.shapeCoords": "ROOT",
  "elk.json.edgeCoords": "ROOT",
};
const MUTED = "var(--color-muted)";

type Change = "new" | "adopted" | "owner" | "changed" | "removed";
const CHANGE_LABEL: Record<Change, string> = {
  new: "NEW",
  adopted: "ADOPTED",
  owner: "NEW OWNER",
  changed: "CHANGED",
  removed: "REMOVED",
};

interface Wire {
  key: string;
  from: string;
  to: string;
  kind: "kept" | "added" | "removed";
}
interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}
interface Layout {
  width: number;
  height: number;
  boxes: Map<string, Box>;
  paths: Map<string, { x: number; y: number }[]>;
}

const live = (state: LineageState | undefined) =>
  (state?.modules ?? []).filter((m) => m.status !== "removed");

/** What the diagram shows at `state`, compared with the meeting before it. */
function diagram(state: LineageState, previous: LineageState | undefined) {
  const now = live(state);
  const before = new Map(live(previous).map((m) => [m.id, m]));
  const ghosts = state.modules.filter((m) => m.status === "removed" && before.has(m.id));
  const changes = new Map<string, Change>();
  for (const module of now) {
    const old = before.get(module.id);
    if (!old) changes.set(module.id, "new");
    else if (module.status === "adopted" && old.status !== "adopted")
      changes.set(module.id, "adopted");
    else if (module.owner !== old.owner) changes.set(module.id, "owner");
    else if (
      module.title !== old.title ||
      module.note !== old.note ||
      module.group !== old.group ||
      module.inputs.join() !== old.inputs.join()
    )
      changes.set(module.id, "changed");
  }
  for (const ghost of ghosts) changes.set(ghost.id, "removed");

  const modules = [...now, ...ghosts];
  const shown = new Map(modules.map((m) => [m.id, m]));
  const inside = (id: string, ancestor: string) => {
    for (let group = shown.get(id)?.group; group; group = shown.get(group)?.group) {
      if (group === ancestor) return true;
    }
    return false;
  };
  const wiresOf = (list: ModuleView[]) =>
    new Map(list.flatMap((m) => m.inputs.map((i) => [`${i}>${m.id}`, [i, m.id]] as const)));
  const current = wiresOf(now);
  const earlier = wiresOf([...before.values()]);
  const wires: Wire[] = [];
  for (const [key, [from, to]] of [...current, ...earlier]) {
    if (wires.some((w) => w.key === key) || !shown.has(from) || !shown.has(to)) continue;
    if (inside(from, to) || inside(to, from)) continue; // a block's own layers wire inside it
    const kind = !current.has(key) ? "removed" : previous && !earlier.has(key) ? "added" : "kept";
    wires.push({ key, from, to, kind });
  }
  return { modules, changes, wires };
}

const boxWidth = (module: ModuleView, subtitle: string) =>
  Math.round(
    Math.min(224, Math.max(116, 24 + Math.max(6.6 * module.title.length, 5.5 * subtitle.length))),
  );

function toElk(modules: ModuleView[], wires: Wire[], subtitle: (m: ModuleView) => string) {
  const ids = new Set(modules.map((m) => m.id));
  const blocks = new Set(modules.flatMap((m) => (m.group && ids.has(m.group) ? [m.group] : [])));
  const nodes = new Map<string, ElkNode>();
  for (const module of modules) {
    nodes.set(
      module.id,
      blocks.has(module.id)
        ? {
            id: module.id,
            children: [],
            layoutOptions: {
              "elk.padding": "[top=40,left=12,bottom=12,right=12]",
              "elk.nodeSize.constraints": "MINIMUM_SIZE",
              "elk.nodeSize.minimum": `(${boxWidth(module, subtitle(module)) + 20}, 80)`,
            },
          }
        : { id: module.id, width: boxWidth(module, subtitle(module)), height: BOX_HEIGHT },
    );
  }
  const root: ElkNode = { id: "root", layoutOptions: LAYOUT_OPTIONS, children: [] };
  for (const module of modules) {
    const node = nodes.get(module.id);
    const parent = (module.group && nodes.get(module.group)) || root;
    if (node) parent.children?.push(node);
  }
  root.edges = wires.map(
    (wire): ElkExtendedEdge => ({ id: wire.key, sources: [wire.from], targets: [wire.to] }),
  );
  return root;
}

function readLayout(root: ElkNode): Layout {
  const boxes = new Map<string, Box>();
  const visit = (node: ElkNode) => {
    for (const child of node.children ?? []) {
      boxes.set(child.id, {
        x: child.x ?? 0,
        y: child.y ?? 0,
        width: child.width ?? 0,
        height: child.height ?? 0,
      });
      visit(child);
    }
  };
  visit(root);
  const paths = new Map(
    (root.edges ?? []).map((edge) => [
      edge.id,
      (edge as ElkExtendedEdge).sections?.flatMap((s) => [
        s.startPoint,
        ...(s.bendPoints ?? []),
        s.endPoint,
      ]) ?? [],
    ]),
  );
  return { width: root.width ?? 0, height: root.height ?? 0, boxes, paths };
}

/** An orthogonal route with its corners rounded off. */
function routePath(points: { x: number; y: number }[]) {
  if (points.length < 2) return "";
  let d = `M${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const [a, b, c] = [points[i - 1], points[i], points[i + 1]];
    const r = Math.min(
      8,
      Math.hypot(b.x - a.x, b.y - a.y) / 2,
      Math.hypot(c.x - b.x, c.y - b.y) / 2,
    );
    const toward = (p: { x: number; y: number }) => {
      const length = Math.hypot(p.x - b.x, p.y - b.y) || 1;
      return `${b.x + ((p.x - b.x) / length) * r},${b.y + ((p.y - b.y) / length) * r}`;
    };
    d += ` L${toward(a)} Q${b.x},${b.y} ${toward(c)}`;
  }
  const last = points[points.length - 1];
  return `${d} L${last.x},${last.y}`;
}

const tint = (color: string, percent: number) =>
  `color-mix(in oklab, ${color} ${percent}%, var(--color-surface))`;

interface Props {
  state: LineageState;
  previous: LineageState | undefined;
  colors: Record<string, string>;
  selected: string | null;
  editable: boolean;
  onSelect: (moduleId: string) => void;
  onAdd: () => void;
}

export function ArchitectureView({
  state,
  previous,
  colors,
  selected,
  editable,
  onSelect,
  onAdd,
}: Props) {
  const { name } = lookup(state);
  const { modules, changes, wires } = useMemo(() => diagram(state, previous), [state, previous]);
  const subtitle = (m: ModuleView) =>
    `${m.owner ? name(m.owner) : "Unassigned"} · ${m.status === "removed" ? "removed" : m.status}`;
  const colorOf = (m: ModuleView) => (m.owner ? colors[m.owner] : undefined) ?? MUTED;
  // Lay out again only when the structure changes, not on every render.
  const signature = JSON.stringify([
    modules.map((m) => [m.id, m.group, boxWidth(m, subtitle(m))]),
    wires.map((w) => w.key),
  ]);
  const [layout, setLayout] = useState<{
    signature: string;
    layout: Layout;
    version: number; // bumps on every new layout so wires fade in at their new place
  } | null>(null);
  const [failed, setFailed] = useState(false);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `signature` stands for modules and wires
  useEffect(() => {
    let stale = false;
    layoutEngine()
      .then((elk) => elk.layout(toElk(modules, wires, subtitle)))
      .then(
        (root) => {
          if (stale) return;
          setFailed(false);
          setLayout((last) => ({
            signature,
            layout: readLayout(root),
            version: (last?.version ?? 0) + 1,
          }));
        },
        () => setFailed(true),
      );
    return () => {
      stale = true;
    };
  }, [signature]);

  const blocks = new Set(modules.flatMap((m) => (m.group ? [m.group] : [])));
  const shown = layout?.layout;
  const activate = (event: KeyboardEvent, moduleId: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(moduleId);
    }
  };
  const owners = [...state.workspace.members.map((m) => m.id), null].map((owner) => ({
    owner,
    modules: live(state).filter((m) => m.owner === owner),
  }));
  const counts = {
    adopted: live(state).filter((m) => m.status === "adopted").length,
    proposed: live(state).filter((m) => m.status === "proposed").length,
    changed: changes.size,
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink-2">
          {counts.adopted} adopted · {counts.proposed} proposed
          {counts.changed > 0 && <> · {counts.changed} changed in this meeting</>}
        </p>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted">
            {state.workspace.notion_page_id
              ? "Edits are written back to Notion"
              : "Edits are saved as session notes"}
          </span>
          <button
            type="button"
            onClick={onAdd}
            disabled={!editable}
            title={editable ? undefined : "Move to the latest meeting to edit"}
            className="inline-flex items-center gap-1 rounded-lg border border-hairline px-2.5 py-1 text-sm text-ink hover:bg-wash disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Plus size={14} /> Add module
          </button>
        </div>
      </div>

      {failed && (
        <p className="text-sm text-muted">
          The diagram could not be laid out; the list below still holds every module.
        </p>
      )}
      {modules.length === 0 ? (
        <p className="rounded-lg border border-dashed border-hairline px-4 py-10 text-center text-sm text-muted">
          No architecture has been described in the notes yet at this meeting.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-hairline bg-page">
          {shown && (
            <svg
              viewBox={`0 0 ${shown.width} ${shown.height}`}
              width="100%"
              style={{ maxWidth: shown.width, minWidth: Math.min(shown.width, 640) }}
              className={`block ${layout.signature === signature ? "" : "opacity-60"}`}
              aria-label={`Architecture with ${modules.length} modules`}
            >
              <defs>
                {[
                  ["arch-arrow", "var(--color-ink-2)"],
                  ["arch-arrow-muted", MUTED],
                ].map(([id, color]) => (
                  <marker
                    key={id}
                    id={id}
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="7"
                    markerHeight="7"
                    orient="auto-start-reverse"
                  >
                    <path d="M0,1 L9,5 L0,9 z" fill={color} />
                  </marker>
                ))}
              </defs>

              {modules
                .filter((m) => blocks.has(m.id))
                .map((module) => {
                  const box = shown.boxes.get(module.id);
                  return (
                    box && (
                      <ModuleShape
                        key={module.id}
                        module={module}
                        box={box}
                        color={colorOf(module)}
                        subtitle={subtitle(module)}
                        change={changes.get(module.id)}
                        selected={module.id === selected}
                        block
                        onSelect={onSelect}
                        onKey={activate}
                      />
                    )
                  );
                })}

              {wires.map((wire) => {
                const points = shown.paths.get(wire.key);
                if (!points) return null;
                const ends = [wire.from, wire.to].map((id) => modules.find((m) => m.id === id));
                const tentative = ends.some((m) => m?.status !== "adopted");
                return (
                  <path
                    key={`${wire.key}@${layout.version}`}
                    d={routePath(points)}
                    fill="none"
                    stroke={wire.kind === "removed" ? MUTED : "var(--color-ink-2)"}
                    strokeWidth={wire.kind === "added" ? 2.25 : 1.25}
                    strokeDasharray={tentative ? "5 4" : undefined}
                    opacity={wire.kind === "removed" ? 0.55 : 1}
                    markerEnd={`url(#${wire.kind === "removed" ? "arch-arrow-muted" : "arch-arrow"})`}
                    className="appear"
                  />
                );
              })}

              {modules
                .filter((m) => !blocks.has(m.id))
                .map((module) => {
                  const box = shown.boxes.get(module.id);
                  return (
                    box && (
                      <ModuleShape
                        key={module.id}
                        module={module}
                        box={box}
                        color={colorOf(module)}
                        subtitle={subtitle(module)}
                        change={changes.get(module.id)}
                        selected={module.id === selected}
                        onSelect={onSelect}
                        onKey={activate}
                      />
                    )
                  );
                })}
            </svg>
          )}
        </div>
      )}

      <ul className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-ink-2">
        <li className="flex items-center gap-1.5">
          <LegendBox /> Adopted
        </li>
        <li className="flex items-center gap-1.5">
          <LegendBox dashed /> Proposed
        </li>
        <li className="flex items-center gap-1.5">
          <LegendBox dashed faded /> Removed in this meeting
        </li>
        <li>Box color = who builds it · thick arrow = new connection</li>
      </ul>

      <section>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">
          Who builds what
        </h3>
        <ul className="space-y-1.5">
          {owners
            .filter((row) => row.modules.length > 0)
            .map((row) => {
              const color = (row.owner && colors[row.owner]) || MUTED;
              return (
                <li key={row.owner ?? "none"} className="flex items-start gap-3 text-sm">
                  <span className="flex w-32 shrink-0 items-center gap-1.5 pt-0.5 text-ink-2">
                    <Dot color={color} />
                    <span className="truncate">{row.owner ? name(row.owner) : "Unassigned"}</span>
                  </span>
                  <span className="flex flex-wrap gap-1.5">
                    {row.modules.map((module) => (
                      <button
                        key={module.id}
                        type="button"
                        onClick={() => onSelect(module.id)}
                        title={module.status}
                        style={{
                          background: tint(color, 15),
                          borderColor: color,
                          borderStyle: module.status === "proposed" ? "dashed" : "solid",
                        }}
                        className={`rounded-md border px-2 py-0.5 text-xs text-ink hover:underline ${
                          module.id === selected ? "font-semibold ring-2 ring-ink" : ""
                        }`}
                      >
                        {module.title}
                      </button>
                    ))}
                  </span>
                </li>
              );
            })}
        </ul>
      </section>
    </div>
  );
}

function LegendBox({ dashed = false, faded = false }: { dashed?: boolean; faded?: boolean }) {
  return (
    <svg width="22" height="14" aria-hidden="true">
      <rect
        x="1"
        y="1"
        width="20"
        height="12"
        rx="3"
        fill={faded ? "none" : tint(NEUTRAL, 14)}
        stroke={faded ? MUTED : NEUTRAL}
        strokeWidth="1.5"
        strokeDasharray={dashed ? "3 2" : undefined}
      />
    </svg>
  );
}

function ModuleShape({
  module,
  box,
  color,
  subtitle,
  change,
  selected,
  block = false,
  onSelect,
  onKey,
}: {
  module: ModuleView;
  box: Box;
  color: string;
  subtitle: string;
  change: Change | undefined;
  selected: boolean;
  block?: boolean;
  onSelect: (id: string) => void;
  onKey: (event: KeyboardEvent, id: string) => void;
}) {
  const removed = module.status === "removed";
  const dashed = removed || module.status === "proposed" ? "5 4" : undefined;
  const label = change && CHANGE_LABEL[change];
  return (
    // biome-ignore lint/a11y/useSemanticElements: SVG has no <button>; role + key handlers make the box operable
    <g
      role="button"
      tabIndex={0}
      aria-label={`${module.title}, ${subtitle}${label ? `, ${label.toLowerCase()} in this meeting` : ""}`}
      onClick={() => onSelect(module.id)}
      onKeyDown={(event) => onKey(event, module.id)}
      className="cursor-pointer outline-none"
      style={{
        transform: `translate(${box.x}px, ${box.y}px)`,
        transition: "transform 450ms ease-out",
      }}
    >
      <title>{module.note ? `${module.title}: ${module.note}` : module.title}</title>
      {selected && (
        <rect
          x={-4}
          y={-4}
          width={box.width + 8}
          height={box.height + 8}
          rx={block ? 15 : 11}
          fill="none"
          stroke="var(--color-ink)"
          strokeWidth={2}
        />
      )}
      <rect
        width={box.width}
        height={box.height}
        rx={block ? 12 : 8}
        style={{ fill: removed ? "var(--color-surface)" : tint(color, block ? 6 : 15) }}
        stroke={color}
        strokeWidth={1.5}
        strokeDasharray={dashed}
        opacity={removed ? 0.55 : 1}
      />
      <text
        x={12}
        y={block ? 18 : 19}
        textDecoration={removed ? "line-through" : undefined}
        className={`fill-ink font-semibold ${block ? "text-[11px] uppercase tracking-wide" : "text-[12px]"}`}
        opacity={removed ? 0.6 : 1}
      >
        {truncate(module.title, 34)}
      </text>
      <text
        x={12}
        y={block ? 32 : 35}
        className="fill-ink-2 text-[10.5px]"
        opacity={removed ? 0.6 : 1}
      >
        {subtitle}
      </text>
      {label && (
        <g transform={`translate(${box.width - 4}, -11)`}>
          <rect
            x={-(label.length * 6.2 + 12)}
            width={label.length * 6.2 + 12}
            height={15}
            rx={7.5}
            fill={removed ? MUTED : "var(--color-ink)"}
          />
          <text
            x={-6}
            y={10.5}
            textAnchor="end"
            className="fill-surface text-[9px] font-bold tracking-wide"
          >
            {label}
          </text>
        </g>
      )}
    </g>
  );
}

// --- Module panel and form -----------------------------------------------------

type Edit = Omit<EditRequest, "member">;

interface Collaboration {
  editable: boolean;
  actingAs: string;
  onActingAs: (memberId: string) => void;
  /** Resolves to an error message, or null once the edit is saved. */
  onEdit: (edit: Edit) => Promise<string | null>;
}

function Heading({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">
      {children}
    </h3>
  );
}

function ActingAs({ state, actingAs, onActingAs }: { state: LineageState } & Collaboration) {
  return (
    <label className="flex items-center gap-2 text-xs text-ink-2">
      Acting as
      <select
        value={actingAs}
        onChange={(event) => onActingAs(event.target.value)}
        className="rounded-md border border-hairline bg-surface px-2 py-1 text-xs text-ink"
      >
        {state.workspace.members.map((member) => (
          <option key={member.id} value={member.id}>
            {member.name}
          </option>
        ))}
      </select>
    </label>
  );
}

const INPUT =
  "w-full rounded-md border border-hairline bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-muted";
const BUTTON =
  "rounded-md border border-hairline px-2.5 py-1 text-xs font-medium text-ink hover:bg-wash disabled:opacity-40";

interface PanelProps extends Collaboration {
  state: LineageState;
  moduleId: string;
  colors: Record<string, string>;
  onSelectModule: (id: string) => void;
  onSelectIdea: (id: string) => void;
  onOpenQuote: (sourceId: string, line: number) => void;
}

/** One module: who builds it, how it is wired, what the team says about it, and its history,
 * every entry backed by a quote. At the latest meeting it is also where teammates edit it. */
export function ModulePanel(props: PanelProps) {
  const { state, moduleId, colors, editable, onEdit } = props;
  const { onSelectModule, onSelectIdea, onOpenQuote } = props;
  const { name, title: ideaTitle, date } = lookup(state);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const modules = new Map(state.modules.map((m) => [m.id, m]));
  const module = modules.get(moduleId);

  if (!module || module.status === "removed") {
    return (
      <p className="text-sm text-muted">
        {module
          ? `${module.title} was removed from the architecture on ${date(module.changed)}.`
          : "This module does not exist yet at this point in the timeline."}
      </p>
    );
  }

  const run = async (edit: Edit) => {
    setBusy(true);
    const error = await onEdit(edit);
    setBusy(false);
    setMessage(error);
    if (!error) setText("");
  };
  const link = (id: string) => (
    <button
      key={id}
      type="button"
      onClick={() => onSelectModule(id)}
      className="font-medium text-ink underline decoration-hairline underline-offset-2 hover:decoration-ink"
    >
      {modules.get(id)?.title ?? id}
    </button>
  );
  const others = state.modules.filter((m) => m.status !== "removed");
  const feeds = others.filter((m) => m.inputs.includes(module.id)).map((m) => m.id);
  const layers = others.filter((m) => m.group === module.id).map((m) => m.id);
  const wiring: [string, string[]][] = [
    ["Fed by", module.inputs],
    ["Feeds", feeds],
    ["Inside", module.group ? [module.group] : []],
    ["Contains", layers],
  ];
  const cells = module.idea ? state.stances.filter((c) => c.idea === module.idea) : [];
  const discussion = state.items.filter((i) => i.module === module.id).reverse();
  const color = (module.owner && colors[module.owner]) || MUTED;

  return (
    <div className="space-y-6">
      <header>
        <div className="flex items-start gap-2">
          <span className="pt-1.5">
            <Dot color={color} size={10} />
          </span>
          <h2 className="text-base font-semibold leading-snug">{module.title}</h2>
        </div>
        <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-2">
          {module.owner ? name(module.owner) : "Unassigned"}
          <span className="rounded-full border border-hairline bg-page px-2 py-0.5 text-[11px] font-medium">
            {module.status}
          </span>
          <span className="text-muted">since {date(module.born)}</span>
        </p>
        {module.note && <p className="mt-3 text-sm leading-relaxed">{module.note}</p>}
        <dl className="mt-3 space-y-1 text-xs text-ink-2">
          {wiring
            .filter(([, ids]) => ids.length > 0)
            .map(([label, ids]) => (
              <div key={label} className="flex gap-2">
                <dt className="w-16 shrink-0 text-muted">{label}</dt>
                <dd className="flex flex-wrap gap-x-3">{ids.map(link)}</dd>
              </div>
            ))}
        </dl>
      </header>

      {module.idea && (
        <section>
          <Heading>Implements</Heading>
          <button
            type="button"
            onClick={() => module.idea && onSelectIdea(module.idea)}
            className="text-sm font-medium underline decoration-hairline underline-offset-2 hover:decoration-ink"
          >
            {ideaTitle(module.idea)}
          </button>
          <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-2">
            {cells.map((cell) => (
              <li key={cell.member} className="flex items-center gap-1.5">
                <Dot color={colors[cell.member]} />
                {name(cell.member)}
                <StanceChip stance={cell.latest} />
              </li>
            ))}
          </ul>
          {cells.length > 0 && (
            <p className="mt-1 text-[11px] text-muted">
              {cells.filter((c) => FOR_STANCES.includes(c.latest)).length} for ·{" "}
              {cells.filter((c) => c.latest === "challenge").length} against
            </p>
          )}
        </section>
      )}

      {discussion.length > 0 && (
        <section>
          <Heading>Discussion</Heading>
          <ul className="space-y-1.5 text-sm text-ink-2">
            {discussion.map((item) => (
              <li key={`${item.source}-${item.line}`} className="flex gap-2">
                <span className="w-16 shrink-0 pt-0.5 text-[10px] font-semibold tracking-wide text-muted">
                  {item.kind.toUpperCase()}
                </span>
                <button
                  type="button"
                  onClick={() => onOpenQuote(item.source, item.line)}
                  className="text-left leading-snug hover:underline"
                >
                  {item.quote}
                  <span className="text-muted"> · {date(item.source)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <Heading>History · who changed what</Heading>
        <ol className="space-y-2.5 border-l border-hairline pl-3 text-sm">
          {module.history
            .map((op, order) => ({ op, order })) // history only grows, so order is a stable key
            .reverse()
            .map(({ op, order }) => (
              <li key={order}>
                <p className="text-xs text-muted">
                  {date(op.source)} · {describeOp(op, name, (id) => modules.get(id)?.title ?? id)}
                </p>
                <button
                  type="button"
                  onClick={() => onOpenQuote(op.source, op.line)}
                  className="text-left leading-snug text-ink-2 hover:underline"
                  title="Show this quote in the original note"
                >
                  “{op.quote}”
                </button>
              </li>
            ))}
        </ol>
      </section>

      <section className="space-y-3 rounded-lg border border-hairline p-3">
        <div className="flex items-center justify-between gap-2">
          <Heading>Work on it together</Heading>
          {editable && <ActingAs {...props} />}
        </div>
        {!editable ? (
          <p className="text-xs text-muted">Move the slider to the latest meeting to edit.</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <label className="flex items-center gap-1.5 text-ink-2">
                Owner
                <select
                  value={module.owner ?? ""}
                  disabled={busy}
                  onChange={(event) =>
                    run({ action: "update", module: module.id, owner: event.target.value })
                  }
                  className="rounded-md border border-hairline bg-surface px-2 py-1 text-xs text-ink"
                >
                  <option value="">Unassigned</option>
                  {state.workspace.members.map((member) => (
                    <option key={member.id} value={member.id}>
                      {member.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={busy}
                className={BUTTON}
                onClick={() =>
                  run({
                    action: "update",
                    module: module.id,
                    status: module.status === "adopted" ? "proposed" : "adopted",
                  })
                }
              >
                {module.status === "adopted" ? "Back to proposed" : "Mark adopted"}
              </button>
              <button
                type="button"
                disabled={busy}
                className={BUTTON}
                onClick={() => {
                  if (window.confirm(`Remove "${module.title}" from the architecture?`)) {
                    run({ action: "remove", module: module.id, text });
                  }
                }}
              >
                Remove
              </button>
            </div>
            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={2}
              placeholder="A question, a next step, or why you back or doubt it"
              className={INPUT}
            />
            <div className="flex flex-wrap gap-2">
              {(
                [
                  ["question", "Ask"],
                  ["action", "I'll take it on"],
                  ...(module.idea
                    ? [
                        ["support", "Support"],
                        ["challenge", "Challenge"],
                      ]
                    : []),
                ] as [Edit["action"], string][]
              ).map(([action, label]) => (
                <button
                  key={action}
                  type="button"
                  disabled={
                    busy || ((action === "question" || action === "action") && !text.trim())
                  }
                  className={BUTTON}
                  onClick={() => run({ action, module: module.id, text })}
                >
                  {label}
                </button>
              ))}
            </div>
            {message && <p className="text-xs text-[#b42318]">{message}</p>}
            <p className="text-[11px] text-muted">
              Each change is written as one line of today's session note, so it is quoted evidence
              like anything said in a meeting.
            </p>
          </>
        )}
      </section>
    </div>
  );
}

function describeOp(
  op: ModuleView["history"][number],
  name: (id: string | null) => string,
  title: (id: string) => string,
) {
  if (op.op === "add") return `added${op.owner ? ` for ${name(op.owner)}` : ""}`;
  if (op.op === "remove") return "removed";
  const parts = [];
  if (op.title) parts.push(`renamed to ${op.title}`);
  if (op.owner !== null) parts.push(op.owner ? `owner → ${name(op.owner)}` : "unassigned");
  if (op.status) parts.push(`now ${op.status}`);
  if (op.inputs) parts.push(`fed by ${op.inputs.map(title).join(", ") || "nothing"}`);
  if (op.group !== null) parts.push(op.group ? `moved into ${title(op.group)}` : "moved out");
  if (op.idea) parts.push("linked to an idea");
  if (op.note !== null) parts.push("described");
  return parts.join(" · ") || "updated";
}

interface FormProps extends Collaboration {
  state: LineageState;
  onCancel: () => void;
}

/** Propose a new module: it joins the diagram as "proposed", owned and wired as given. */
export function ModuleForm(props: FormProps) {
  const { state, actingAs, onEdit, onCancel } = props;
  const modules = live(state);
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [owner, setOwner] = useState(actingAs);
  const [inputs, setInputs] = useState<string[]>([]);
  const [group, setGroup] = useState("");
  const [idea, setIdea] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    const error = await onEdit({
      action: "add",
      title,
      note: note || undefined,
      owner: owner || undefined,
      inputs,
      group: group || undefined,
      idea: idea || undefined,
      text,
    });
    setBusy(false);
    setMessage(error);
  };

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <header className="flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Propose a module</h2>
        <ActingAs {...props} />
      </header>
      <label className="block space-y-1 text-xs text-ink-2">
        Name
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="e.g. Shift detector"
          required
          className={INPUT}
        />
      </label>
      <label className="block space-y-1 text-xs text-ink-2">
        What it does
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="One line"
          className={INPUT}
        />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="block space-y-1 text-xs text-ink-2">
          Who builds it
          <select
            value={owner}
            onChange={(event) => setOwner(event.target.value)}
            className={INPUT}
          >
            <option value="">Unassigned</option>
            {state.workspace.members.map((member) => (
              <option key={member.id} value={member.id}>
                {member.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block space-y-1 text-xs text-ink-2">
          Inside block
          <select
            value={group}
            onChange={(event) => setGroup(event.target.value)}
            className={INPUT}
          >
            <option value="">None</option>
            {modules.map((module) => (
              <option key={module.id} value={module.id}>
                {module.title}
              </option>
            ))}
          </select>
        </label>
      </div>
      <fieldset className="space-y-1 text-xs text-ink-2">
        <legend className="mb-1">Fed by</legend>
        <div className="flex max-h-40 flex-col gap-1 overflow-y-auto rounded-md border border-hairline p-2">
          {modules.map((module) => (
            <label key={module.id} className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={inputs.includes(module.id)}
                onChange={(event) =>
                  setInputs((list) =>
                    event.target.checked
                      ? [...list, module.id]
                      : list.filter((i) => i !== module.id),
                  )
                }
              />
              {module.title}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="block space-y-1 text-xs text-ink-2">
        Implements idea
        <select value={idea} onChange={(event) => setIdea(event.target.value)} className={INPUT}>
          <option value="">None</option>
          {state.ideas
            .filter((i) => i.status !== "dropped")
            .map((i) => (
              <option key={i.id} value={i.id}>
                {truncate(i.title, 48)}
              </option>
            ))}
        </select>
      </label>
      <label className="block space-y-1 text-xs text-ink-2">
        Why
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={2}
          placeholder="Shown to the team with the change"
          className={INPUT}
        />
      </label>
      {message && <p className="text-xs text-[#b42318]">{message}</p>}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={busy || !title.trim()}
          className="rounded-lg bg-ink px-3 py-1.5 text-sm font-medium text-surface hover:bg-ink-2 disabled:opacity-40"
        >
          Propose
        </button>
        <button type="button" onClick={onCancel} className={BUTTON}>
          Cancel
        </button>
      </div>
    </form>
  );
}
