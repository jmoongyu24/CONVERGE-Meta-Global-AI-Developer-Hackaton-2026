// Shared pieces: member colors, chips, the idea panel, the meeting digest, and the
// source viewer that shows a quote inside its original note.

import { X } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import {
  assetUrl,
  fetchSource,
  type IdeaStatus,
  type LineageState,
  type Member,
  type OpKind,
  type Source,
  type Stance,
  type Workspace,
} from "./api";

// --- Color ---------------------------------------------------------------------

const SERIES = [1, 2, 3, 4, 5].map((slot) => `var(--color-series-${slot})`);
export const NEUTRAL = "var(--color-ink-2)";

/** Whether a member leads the lab: their role says PI, or the notes call them "Prof.". */
export const isPi = (member: Member) =>
  /\bPI\b/.test(member.role) || /^Prof\.?\s/.test(member.name);

/** Owner color per member, in roster order and never cycled. The PI moderates rather than
 * competes, so PI-owned ideas take neutral ink and the validated hues go to the students. */
export function memberColors(workspace: Workspace): Record<string, string> {
  const colors: Record<string, string> = {};
  let slot = 0;
  for (const member of workspace.members) {
    colors[member.id] = isPi(member) || slot >= SERIES.length ? NEUTRAL : SERIES[slot++];
  }
  return colors;
}

// --- Formatting ----------------------------------------------------------------

const fullDate = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
});
const monthDay = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
const month = new Intl.DateTimeFormat("en-US", { month: "short" });

export const formatDate = (iso: string) => fullDate.format(new Date(iso));

/** Column header: "Sep 30" within one year; "Jun ’17" when the timeline spans years, since
 * "Jun 17" would read as a day. */
export function columnLabel(iso: string, spansYears: boolean) {
  const date = new Date(iso);
  return spansYears
    ? `${month.format(date)} ’${String(date.getFullYear()).slice(2)}`
    : monthDay.format(date);
}

export const truncate = (text: string, length: number) =>
  text.length > length ? `${text.slice(0, length - 1)}…` : text;

export function lookup(state: LineageState) {
  const members = new Map(state.workspace.members.map((m) => [m.id, m]));
  const ideas = new Map(state.ideas.map((i) => [i.id, i]));
  const sources = new Map(state.sources.map((s) => [s.id, s]));
  return {
    name: (memberId: string | null) => (memberId && members.get(memberId)?.name) || "Team",
    title: (ideaId: string) => ideas.get(ideaId)?.title ?? ideaId,
    date: (sourceId: string) => formatDate(sources.get(sourceId)?.occurred_at ?? ""),
  };
}

// --- Chips and dots ------------------------------------------------------------

// Stance and status never rely on color: each chip carries a glyph and a word.
export const STANCE_GLYPH: Record<Stance, string> = {
  propose: "◆",
  support: "▲",
  refine: "✎",
  question: "?",
  challenge: "▼",
  inform: "–",
};
const STATUS_GLYPH: Record<IdeaStatus, string> = {
  active: "●",
  dormant: "◌",
  parked: "‖",
  dropped: "✕",
};

function Chip({ glyph, label }: { glyph: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-hairline bg-page px-2 py-0.5 text-[11px] font-medium text-ink-2">
      <span aria-hidden="true">{glyph}</span>
      {label}
    </span>
  );
}

export const StanceChip = ({ stance }: { stance: Stance }) => (
  <Chip glyph={STANCE_GLYPH[stance]} label={stance} />
);

export const StatusChip = ({ status }: { status: IdeaStatus }) => (
  <Chip glyph={STATUS_GLYPH[status]} label={status} />
);

export function Dot({ color, size = 8 }: { color: string; size?: number }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block shrink-0 rounded-full"
      style={{ width: size, height: size, background: color }}
    />
  );
}

export function MemberLegend({
  workspace,
  colors,
}: {
  workspace: Workspace;
  colors: Record<string, string>;
}) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2">
      {workspace.members.map((member) => (
        <li key={member.id} className="flex items-center gap-1.5">
          <Dot color={colors[member.id]} />
          {member.name}
          <span className="text-muted">{member.role}</span>
        </li>
      ))}
    </ul>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">{title}</h3>
      {children}
    </section>
  );
}

function IdeaLink({
  id,
  label,
  onSelect,
}: {
  id: string;
  label: string;
  onSelect: (id: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(id)}
      className="font-medium text-ink underline decoration-hairline underline-offset-2 hover:decoration-ink"
    >
      {label}
    </button>
  );
}

// --- Meeting digest ------------------------------------------------------------

const OP_TAG: Record<OpKind, string> = {
  new: "NEW",
  update: "REFINED",
  branch: "BRANCH",
  merge: "MERGE",
  park: "PARKED",
  drop: "DROPPED",
};
const ITEM_TAG = { consensus: "AGREED", decision: "DECIDED", question: "QUESTION" } as const;

/** What the selected meeting changed: idea ops, architecture changes, stance shifts,
 * agreements and decisions. */
export function DigestList({
  state,
  onSelectIdea,
  onSelectModule,
}: {
  state: LineageState;
  onSelectIdea: (id: string) => void;
  onSelectModule: (id: string) => void;
}) {
  const { name, title } = lookup(state);
  const link = (id: string) => <IdeaLink id={id} label={title(id)} onSelect={onSelectIdea} />;
  const entries: { key: string; tag: string; body: ReactNode }[] = [];
  const modules = new Map(state.modules.map((m) => [m.id, m]));

  for (const op of state.digest.ops) {
    const parents = op.parents.map((p) => title(p)).join(" + ");
    const body =
      op.op === "new" ? (
        <>
          {link(op.idea)} proposed by {name(op.owner)}
        </>
      ) : op.op === "branch" || op.op === "merge" ? (
        <>
          {link(op.idea)} {op.op === "branch" ? "branched from" : "merges"} {parents}
        </>
      ) : (
        link(op.idea)
      );
    entries.push({ key: `op-${op.op}-${op.idea}`, tag: OP_TAG[op.op], body });
  }
  // One entry per module, however many of its fields the meeting touched.
  for (const id of new Set(state.digest.modules.map((op) => op.module))) {
    const ops = state.digest.modules.filter((op) => op.module === id);
    const label = (
      <IdeaLink id={id} label={modules.get(id)?.title ?? id} onSelect={onSelectModule} />
    );
    const added = ops.find((op) => op.op === "add");
    const owner = [...ops].reverse().find((op) => op.owner)?.owner ?? null;
    const changes = [
      owner && !added ? `now built by ${name(owner)}` : "",
      ops.some((op) => op.op === "update" && op.status === "adopted") ? "adopted" : "",
      ops.some((op) => op.op === "update" && op.inputs) ? "rewired" : "",
      ops.some((op) => op.op === "update" && (op.title || op.note !== null)) ? "redescribed" : "",
    ].filter(Boolean);
    const [tag, body] = ops.some((op) => op.op === "remove")
      ? ["REMOVED", <>{label} left the architecture</>]
      : added
        ? [
            "MODULE",
            <>
              {label} added{owner ? `, built by ${name(owner)}` : ""}
            </>,
          ]
        : [
            changes.includes("adopted") ? "ADOPTED" : owner ? "OWNER" : "CHANGED",
            <>
              {label}: {changes.join(", ") || "updated"}
            </>,
          ];
    entries.push({ key: `module-${id}`, tag, body });
  }
  for (const shift of state.digest.shifts) {
    entries.push({
      key: `shift-${shift.member}-${shift.idea}`,
      tag: "SHIFT",
      body: (
        <>
          {name(shift.member)} went from {shift.before} to {shift.after} on {link(shift.idea)}
        </>
      ),
    });
  }
  for (const item of state.digest.items) {
    if (item.kind === "action") continue;
    entries.push({
      key: `item-${item.kind}-${item.line}`,
      tag: ITEM_TAG[item.kind],
      body: <>“{item.quote}”</>,
    });
  }

  if (!entries.length) {
    return <p className="text-sm text-muted">No idea changes were recorded in this meeting.</p>;
  }
  return (
    <ul className="space-y-1.5 text-sm text-ink-2">
      {entries.map((entry) => (
        <li key={entry.key} className="flex gap-3">
          <span className="w-16 shrink-0 pt-0.5 text-[10px] font-semibold tracking-wide text-muted">
            {entry.tag}
          </span>
          <span className="leading-snug">{entry.body}</span>
        </li>
      ))}
    </ul>
  );
}

// --- Idea panel ----------------------------------------------------------------

/** Stances that count as backing an idea. */
export const FOR_STANCES: Stance[] = ["propose", "support", "refine"];

const STANCE_GROUPS: { label: string; stances: Stance[] }[] = [
  { label: "For", stances: FOR_STANCES },
  { label: "Against", stances: ["challenge"] },
  { label: "Undecided", stances: ["question", "inform"] },
];

interface IdeaPanelProps {
  state: LineageState;
  ideaId: string;
  colors: Record<string, string>;
  onSelectIdea: (id: string) => void;
  onOpenQuote: (sourceId: string, line: number) => void;
}

/** Everything known about one idea at the current point in time, each claim backed by a quote. */
export function IdeaPanel({ state, ideaId, colors, onSelectIdea, onOpenQuote }: IdeaPanelProps) {
  const idea = state.ideas.find((i) => i.id === ideaId);
  if (!idea) {
    return (
      <p className="text-sm text-muted">
        This idea does not exist yet at this point in the timeline. Move the slider forward to see
        it appear.
      </p>
    );
  }
  const { name, title, date } = lookup(state);
  const cells = state.stances.filter((c) => c.idea === idea.id);
  const followUps = state.items
    .filter((i) => i.idea === idea.id && (i.kind === "question" || i.kind === "action"))
    .reverse();
  const evidence = state.mentions.filter((m) => m.idea === idea.id).reverse();
  const children = state.edges.filter((e) => e.parent === idea.id);
  const links = (ids: string[]) => (
    <span className="inline-flex flex-wrap gap-x-3">
      {ids.map((id) => (
        <IdeaLink key={id} id={id} label={title(id)} onSelect={onSelectIdea} />
      ))}
    </span>
  );

  return (
    <div className="space-y-6">
      <header>
        <div className="flex items-start gap-2">
          <span className="pt-1.5">
            <Dot color={(idea.owner && colors[idea.owner]) || NEUTRAL} size={10} />
          </span>
          <h2 className="text-base font-semibold leading-snug">{idea.title}</h2>
        </div>
        <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-2">
          {name(idea.owner)}
          <StatusChip status={idea.status} />
          <span className="text-muted">since {date(idea.born)}</span>
        </p>
        <p className="mt-3 text-sm leading-relaxed">{idea.summary}</p>
        {(idea.parents.length > 0 || children.length > 0) && (
          <div className="mt-3 space-y-1 text-xs text-ink-2">
            {idea.parents.length > 0 && <p>From: {links(idea.parents)}</p>}
            {children.length > 0 && <p>Led to: {links(children.map((e) => e.child))}</p>}
          </div>
        )}
      </header>

      <Section title="Where the team stands">
        <dl className="space-y-2 text-sm">
          {STANCE_GROUPS.map((group) => {
            const members = cells.filter((c) => group.stances.includes(c.latest));
            return (
              <div key={group.label} className="flex gap-3">
                <dt className="w-20 shrink-0 text-xs text-muted">{group.label}</dt>
                <dd className="flex flex-wrap gap-x-3 gap-y-1">
                  {members.length === 0 && <span className="text-muted">—</span>}
                  {members.map((cell) => (
                    <span key={cell.member} className="flex items-center gap-1.5 text-ink-2">
                      <Dot color={colors[cell.member]} />
                      {name(cell.member)}
                      <StanceChip stance={cell.latest} />
                    </span>
                  ))}
                </dd>
              </div>
            );
          })}
        </dl>
      </Section>

      {followUps.length > 0 && (
        <Section title="Open questions & next steps">
          <ul className="space-y-1.5 text-sm text-ink-2">
            {followUps.map((item) => (
              <li key={`${item.source}-${item.line}-${item.kind}`} className="flex gap-2">
                <span className="w-16 shrink-0 text-[10px] font-semibold tracking-wide text-muted">
                  {item.kind === "question" ? "QUESTION" : "ACTION"}
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
        </Section>
      )}

      <Section title="How it evolved">
        <ol className="space-y-2 border-l border-hairline pl-3 text-sm">
          {[...idea.versions].reverse().map((version) => (
            <li key={version.source}>
              <p className="text-xs text-muted">{date(version.source)}</p>
              <p className="leading-snug text-ink-2">{version.summary}</p>
            </li>
          ))}
        </ol>
      </Section>

      <Section title={`Evidence · ${evidence.length} quotes`}>
        <ul className="space-y-2">
          {evidence.map((mention) => (
            <li
              key={`${mention.source}-${mention.line}-${mention.member}-${mention.stance}`}
              className="rounded-lg border border-hairline p-3"
            >
              <div className="mb-1 flex items-center gap-2 text-xs text-ink-2">
                <Dot color={colors[mention.member]} />
                {name(mention.member)}
                <StanceChip stance={mention.stance} />
                <span className="ml-auto text-muted">{date(mention.source)}</span>
              </div>
              <button
                type="button"
                onClick={() => onOpenQuote(mention.source, mention.line)}
                className="text-left text-sm leading-snug hover:underline"
                title="Show this quote in the original note"
              >
                “{mention.quote}”
              </button>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

// --- Source viewer -------------------------------------------------------------

interface SourceViewerProps {
  workspaceId: string;
  sourceId: string;
  line: number; // 0 when only the quote is known
  quote?: string;
  onClose: () => void;
}

const TABLE_SEPARATOR = /^:?-{3,}:?$/;
const IMAGE_LINE = /^!\[(.*)\]\(([^()\s]+)\)$/;
const cellsOf = (row: string) =>
  row
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split(/(?<!\\)\|/)
    .map((cell) => cell.trim().replaceAll("\\|", "|"));
const isSeparator = (row: string) =>
  row.startsWith("|") && cellsOf(row).every((cell) => TABLE_SEPARATOR.test(cell));

/** One line of a note, drawn the way Notion shows it: tables, images, toggles, sub-pages. */
function NoteLine({
  text,
  next,
  workspaceId,
}: {
  text: string;
  next: string | undefined;
  workspaceId: string;
}) {
  const content = text.trimStart();
  const indent = { paddingLeft: `${(text.length - content.length) * 9}px` };
  const image = IMAGE_LINE.exec(content);
  if (image) {
    return (
      <figure style={indent} className="py-1">
        <img
          src={assetUrl(workspaceId, image[2])}
          alt={image[1]}
          className="max-h-80 rounded-lg border border-hairline bg-surface"
        />
        <figcaption className="mt-1 text-xs text-muted">{image[1]}</figcaption>
      </figure>
    );
  }
  if (content.startsWith("|")) {
    const cells = cellsOf(content);
    const header = next !== undefined && isSeparator(next.trim());
    return (
      <div
        style={{ ...indent, gridTemplateColumns: `repeat(${cells.length}, minmax(0, 1fr))` }}
        className={`grid border-b border-hairline ${header ? "font-semibold" : ""}`}
      >
        {cells.map((cell, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: cells have no identity but their column
          <span key={index} className="px-2 py-0.5">
            {cell}
          </span>
        ))}
      </div>
    );
  }
  const heading = /^(#{1,3}) (.*)$/.exec(content);
  if (heading) {
    const size = ["text-lg", "text-base", "text-[14px]"][heading[1].length - 1];
    return <p className={`font-semibold ${size}`}>{heading[2]}</p>;
  }
  const marks: [RegExp, string, string][] = [
    [/^▸ /, "▸", "font-medium"],
    [/^📄 /, "📄", "font-semibold"],
    [/^- \[ \] /, "☐", ""],
    [/^- \[x\] /, "☑", ""],
    [/^- /, "•", ""],
    [/^> /, "│", "italic text-ink-2"],
  ];
  const mark = marks.find(([pattern]) => pattern.test(content));
  return (
    <p style={indent} className={`whitespace-pre-wrap ${mark?.[2] ?? ""}`}>
      {mark && <span className="mr-2 text-muted">{mark[1]}</span>}
      {mark ? content.replace(mark[0], "") : content || "\u00a0"}
    </p>
  );
}

/** The original note with the quoted line highlighted: every claim can be checked at its source. */
export function SourceViewer({ workspaceId, sourceId, line, quote, onClose }: SourceViewerProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const target = useRef<HTMLLIElement>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  useEffect(() => {
    fetchSource(workspaceId, sourceId).then(setSource, (e: unknown) => setError(String(e)));
  }, [workspaceId, sourceId]);

  useEffect(() => {
    if (source) target.current?.scrollIntoView({ block: "center" });
  }, [source]);

  const lines = source ? source.text.split("\n").map((text, i) => ({ number: i + 1, text })) : [];
  if (!line && quote) {
    const start = quote.slice(0, 40);
    line = lines.find((l) => l.text.includes(start))?.number ?? 0;
  }

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      className="m-auto w-full max-w-3xl rounded-xl bg-surface p-0 text-ink shadow-xl backdrop:bg-ink/30"
    >
      <header className="flex items-start justify-between gap-4 border-b border-hairline px-5 py-4">
        <div>
          <p className="text-xs text-muted">
            {source ? formatDate(source.occurred_at) : "Loading…"}
          </p>
          <h2 className="text-base font-semibold">{source?.title ?? sourceId}</h2>
        </div>
        <div className="flex items-center gap-3">
          {source?.url && (
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-ink-2 underline"
            >
              Open in Notion
            </a>
          )}
          <button
            type="button"
            onClick={() => dialog.current?.close()}
            className="rounded-md p-1 text-ink-2 hover:bg-wash"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>
      </header>
      {error && <p className="px-5 py-4 text-sm">{error}</p>}
      <ol className="max-h-[70vh] overflow-auto py-3 text-[13.5px] leading-6">
        {lines.map(({ number, text }) =>
          isSeparator(text.trim()) ? (
            <li key={number} aria-hidden="true" className="h-1" />
          ) : (
            <li
              key={number}
              ref={number === line ? target : undefined}
              className={`flex gap-4 px-5 ${number === line ? "bg-amber-100" : ""}`}
            >
              <span className="w-8 shrink-0 select-none pt-px text-right font-mono text-[11px] text-muted">
                {number}
              </span>
              <div className="min-w-0 flex-1">
                <NoteLine text={text} next={lines[number]?.text} workspaceId={workspaceId} />
              </div>
            </li>
          ),
        )}
      </ol>
    </dialog>
  );
}
