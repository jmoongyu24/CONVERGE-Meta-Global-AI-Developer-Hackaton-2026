// Who is working on what, and where the team agrees or splits, at the current point in time.
// Positions are shown with glyphs, fill, and outline rather than hue, so they never get
// confused with the member colors.

import type { ReactNode } from "react";
import type { IdeaView, LineageState, LocatedItem, Member, StanceCell } from "./api";
import {
  columnLabel,
  Dot,
  FOR_STANCES,
  lookup,
  NEUTRAL,
  STANCE_GLYPH,
  StatusChip,
  truncate,
} from "./ui";

interface Props {
  state: LineageState;
  colors: Record<string, string>;
  selected: string | null;
  onSelect: (ideaId: string) => void;
  onOpenQuote: (sourceId: string, line: number, quote?: string) => void;
}

const isOpen = (idea: IdeaView | undefined) =>
  !!idea && idea.status !== "parked" && idea.status !== "dropped";

/** How the team stands on one idea. Split ideas rank first because they need a decision. */
function agreement(cells: StanceCell[]) {
  const backers = cells.filter((c) => FOR_STANCES.includes(c.latest)).length;
  const challengers = cells.filter((c) => c.latest === "challenge").length;
  if (backers && challengers) return { label: `Split ${backers}–${challengers}`, rank: 0 };
  if (challengers) return { label: "Opposed", rank: 1 };
  if (backers >= 2) return { label: "Backed", rank: 3 };
  return { label: "Open", rank: 2 };
}

export function TeamView({ state, colors, selected, onSelect, onOpenQuote }: Props) {
  const { name, date } = lookup(state);
  const order = new Map(state.sources.map((s, i) => [s.id, i]));
  const rows = state.ideas
    .map((idea) => {
      const cells = state.stances.filter((c) => c.idea === idea.id);
      return { idea, cells, agreement: agreement(cells) };
    })
    .sort(
      (a, b) =>
        Number(!isOpen(a.idea)) - Number(!isOpen(b.idea)) ||
        a.agreement.rank - b.agreement.rank ||
        (order.get(b.idea.last_active) ?? 0) - (order.get(a.idea.last_active) ?? 0),
    );
  const ofKind = (kind: LocatedItem["kind"]) =>
    state.items.filter((item) => item.kind === kind).reverse();
  const quoteFor = (cell: StanceCell) =>
    [...state.mentions]
      .reverse()
      .find(
        (m) =>
          m.member === cell.member &&
          m.idea === cell.idea &&
          m.source === cell.source &&
          m.stance === cell.latest,
      );

  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="Active ideas"
          value={state.ideas.filter((i) => i.status === "active").length}
        />
        <Stat
          label="Contested ideas"
          value={rows.filter((r) => isOpen(r.idea) && r.agreement.rank === 0).length}
        />
        <Stat label="Team agreements" value={ofKind("consensus").length} />
        <Stat label="Open questions" value={ofKind("question").length} />
      </dl>

      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
        {state.workspace.members.map((member) => (
          <MemberCard
            key={member.id}
            member={member}
            state={state}
            colors={colors}
            onSelect={onSelect}
            onOpenQuote={onOpenQuote}
          />
        ))}
      </div>

      <section>
        <Heading>Where the team stands</Heading>
        <p className="mb-2 text-xs text-muted">
          Each cell is a member's latest position. Click it to read the quote behind it. Split ideas
          come first.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-ink-2">
              <tr>
                <th className="py-2 pr-3 font-medium">Idea</th>
                {state.workspace.members.map((member) => (
                  <th key={member.id} className="py-2 pr-3 font-medium">
                    <span className="flex items-center gap-1.5 whitespace-nowrap">
                      <Dot color={colors[member.id]} />
                      {member.name}
                    </span>
                  </th>
                ))}
                <th className="py-2 font-medium">Team</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ idea, cells, agreement }) => (
                <tr
                  key={idea.id}
                  className={`border-t border-hairline ${idea.id === selected ? "bg-wash" : ""} ${
                    isOpen(idea) ? "" : "opacity-50"
                  }`}
                >
                  <td className="py-2 pr-3">
                    <button
                      type="button"
                      onClick={() => onSelect(idea.id)}
                      className="flex items-center gap-1.5 text-left hover:underline"
                    >
                      <Dot color={(idea.owner && colors[idea.owner]) || NEUTRAL} />
                      <span className="font-medium leading-snug">{idea.title}</span>
                    </button>
                  </td>
                  {state.workspace.members.map((member) => {
                    const cell = cells.find((c) => c.member === member.id);
                    if (!cell) {
                      return (
                        <td key={member.id} className="py-2 pr-3 text-muted">
                          ·
                        </td>
                      );
                    }
                    const quote = quoteFor(cell);
                    return (
                      <td key={member.id} className="py-2 pr-3">
                        <Position
                          cell={cell}
                          title={`${name(cell.member)}, ${cell.latest}, ${date(cell.source)}: “${quote?.quote ?? ""}”`}
                          onOpen={() => quote && onOpenQuote(quote.source, quote.line, quote.quote)}
                        />
                      </td>
                    );
                  })}
                  <td className="whitespace-nowrap py-2 text-xs font-medium text-ink-2">
                    {isOpen(idea) ? agreement.label : <StatusChip status={idea.status} />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="grid gap-6 md:grid-cols-3">
        <ItemList
          title="Agreed by the team"
          items={ofKind("consensus")}
          empty="No team-wide agreement yet."
          date={date}
          onOpenQuote={onOpenQuote}
        />
        <ItemList
          title="Decided"
          items={ofKind("decision")}
          empty="No decisions yet."
          date={date}
          onOpenQuote={onOpenQuote}
        />
        <ItemList
          title="Open questions"
          items={ofKind("question")}
          empty="No open questions."
          date={date}
          onOpenQuote={onOpenQuote}
        />
      </div>
    </div>
  );
}

function Heading({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">
      {children}
    </h3>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-hairline px-4 py-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold">{value}</dd>
    </div>
  );
}

/** A member's position: backing is filled, challenging is outlined, undecided is muted. */
function Position({
  cell,
  title,
  onOpen,
}: {
  cell: StanceCell;
  title: string;
  onOpen: () => void;
}) {
  const tone = FOR_STANCES.includes(cell.latest)
    ? "border-ink bg-ink text-surface"
    : cell.latest === "challenge"
      ? "border-ink text-ink"
      : "border-hairline text-muted";
  return (
    <button
      type="button"
      onClick={onOpen}
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium ${tone}`}
    >
      <span aria-hidden="true">{STANCE_GLYPH[cell.latest]}</span>
      {cell.latest}
    </button>
  );
}

function MemberCard({
  member,
  state,
  colors,
  onSelect,
  onOpenQuote,
}: { member: Member } & Omit<Props, "selected">) {
  const { date } = lookup(state);
  const ideaOf = new Map(state.ideas.map((idea) => [idea.id, idea]));
  const sourceOf = new Map(state.sources.map((s, i) => [s.id, { ...s, index: i }]));
  const spansYears = new Set(state.sources.map((s) => s.occurred_at.slice(0, 4))).size > 1;
  // Newest positions first, each with its date, so a member changing sides is visible.
  const recentFirst = (a: StanceCell, b: StanceCell) =>
    (sourceOf.get(b.source)?.index ?? 0) - (sourceOf.get(a.source)?.index ?? 0);
  const cells = state.stances
    .filter((c) => c.member === member.id && isOpen(ideaOf.get(c.idea)))
    .sort(recentFirst);
  const leads = state.ideas.filter((idea) => idea.owner === member.id && isOpen(idea));
  const backs = cells.filter(
    (c) => FOR_STANCES.includes(c.latest) && ideaOf.get(c.idea)?.owner !== member.id,
  );
  const challenges = cells.filter((c) => c.latest === "challenge");
  const when = (sourceId: string) =>
    columnLabel(sourceOf.get(sourceId)?.occurred_at ?? "", spansYears);
  const next = state.items
    .filter((item) => item.kind === "action" && item.member === member.id)
    .slice(-2)
    .reverse();
  const lastSeen = [...state.mentions].reverse().find((m) => m.member === member.id);

  const ideaButton = (id: string, since?: string) => {
    const idea = ideaOf.get(id);
    return (
      <button
        key={id}
        type="button"
        onClick={() => onSelect(id)}
        className="inline-flex items-center gap-1 text-left text-xs hover:underline"
      >
        <Dot color={(idea?.owner && colors[idea.owner]) || NEUTRAL} size={6} />
        {truncate(idea?.title ?? id, 26)}
        {since && <span className="text-muted">· {when(since)}</span>}
      </button>
    );
  };

  return (
    <article className="rounded-lg border border-hairline p-4">
      <header className="mb-3 flex items-center gap-2">
        <Dot color={colors[member.id]} size={10} />
        <h3 className="text-sm font-semibold">{member.name}</h3>
        <span className="truncate text-xs text-muted">{member.role}</span>
      </header>
      <dl className="space-y-2">
        <Row label="Leads">{leads.map((idea) => ideaButton(idea.id))}</Row>
        <Row label="Backs">{backs.map((c) => ideaButton(c.idea, c.source))}</Row>
        <Row label="Challenges">{challenges.map((c) => ideaButton(c.idea, c.source))}</Row>
        <Row label="Next">
          {next.map((item) => (
            <button
              key={`${item.source}-${item.line}`}
              type="button"
              onClick={() => onOpenQuote(item.source, item.line, item.quote)}
              className="text-left text-xs text-ink-2 hover:underline"
            >
              {truncate(item.quote, 70)}
            </button>
          ))}
        </Row>
      </dl>
      <p className="mt-3 text-[11px] text-muted">
        {lastSeen ? `Last spoke ${date(lastSeen.source)}` : "No recorded position yet"}
      </p>
    </article>
  );
}

function Row({ label, children }: { label: string; children: ReactNode[] }) {
  return (
    <div>
      <dt className="text-[10px] font-semibold uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-0.5 flex flex-col items-start gap-1">
        {children.length ? children : <span className="text-xs text-muted">—</span>}
      </dd>
    </div>
  );
}

function ItemList({
  title,
  items,
  empty,
  date,
  onOpenQuote,
}: {
  title: string;
  items: LocatedItem[];
  empty: string;
  date: (sourceId: string) => string;
  onOpenQuote: (sourceId: string, line: number, quote?: string) => void;
}) {
  return (
    <section>
      <Heading>{title}</Heading>
      {items.length === 0 ? (
        <p className="text-sm text-muted">{empty}</p>
      ) : (
        <ul className="space-y-2 text-sm">
          {items.slice(0, 4).map((item) => (
            <li key={`${item.source}-${item.line}`}>
              <button
                type="button"
                onClick={() => onOpenQuote(item.source, item.line, item.quote)}
                className="text-left leading-snug text-ink-2 hover:underline"
              >
                “{item.quote}”
              </button>
              <p className="text-[11px] text-muted">{date(item.source)}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
