// The first screen, built to be understood in five seconds: the team's situation in one
// sentence, then three answers side by side (what is agreed, what is still open, what I do
// next), then the work as one map, then who pushes what. Every line links to its quote.

import { ArrowRight, Check, MapPin, Quote, RefreshCw, Swords, X } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { ArchitectureCanvas, Legend, shortName } from "./ArchitectureView";
import {
  type Board,
  type Contest,
  type Evidence,
  fetchConverge,
  type LineageState,
  type ModuleView,
  type UpdateStatus,
} from "./api";
import { ConvergeRoom } from "./ConvergeView";
import { Dot, formatDate, lookup, remember, remembered } from "./ui";

const MUTED = "var(--color-muted)";
const INTRO_KEY = "converge.intro.dismissed";
const clock = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });

interface Props {
  state: LineageState;
  board: Board | null;
  colors: Record<string, string>;
  me: string;
  onMe: (memberId: string) => void;
  update: UpdateStatus;
  onUpdate: () => void;
  onOpenQuote: (sourceId: string, line: number, quote?: string) => void;
  onDetails: (moduleId: string | null) => void;
  onAdopted: (message: string) => void;
  openRoom?: string | null; // a CONVERGE Room session to reopen, from a shared link
  compact: boolean; // the narrow side panel next to Notion
}

export function BoardView(props: Props) {
  const { state, board, colors, compact } = props;
  const { name } = lookup(state);
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [room, setRoom] = useState<{ contest: Contest; session?: string } | null>(null);
  const [intro, setIntro] = useState(() => !remembered(INTRO_KEY));
  const map = useRef<HTMLElement>(null);
  const modules = new Map(state.modules.map((m) => [m.id, m]));
  const notes = state.sources.filter((s) => s.kind !== "session");
  const latest = notes[notes.length - 1];

  // biome-ignore lint/correctness/useExhaustiveDependencies: a shared link opens its room once
  useEffect(() => {
    if (!props.openRoom) return;
    fetchConverge(state.workspace.id, props.openRoom).then(
      (session) =>
        setRoom({
          session: session.id,
          contest: board?.contests.find((c) => c.question === session.question) ?? {
            question: session.question,
            stakes: "",
            module: session.module,
            sides: [{ members: session.members, position: "" }],
            decide_by: null,
            evidence: [],
          },
        }),
      () => undefined,
    );
  }, []);

  const locate = (moduleId: string) => {
    setSelected(moduleId);
    map.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="space-y-8 pb-24">
      {intro && (
        <Intro
          onClose={() => {
            setIntro(false);
            remember(INTRO_KEY, "1");
          }}
        />
      )}

      <section>
        <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">
          Team status
          {latest && ` · ${notes.length} meetings, latest ${formatDate(latest.occurred_at)}`}
          <span className="font-normal normal-case tracking-normal">
            {" · "}
            <Quote size={11} className="inline translate-y-[-1px]" aria-label="quote icon" /> opens
            the meeting note behind a line
          </span>
        </p>
        <h2 className="mt-1.5 text-xl font-semibold leading-snug sm:text-2xl">
          {board?.headline?.text ?? "Not analyzed yet: press Update to read the meeting notes."}
          {board?.headline && <Cite claim={board.headline} onOpen={props.onOpenQuote} />}
        </h2>
        {board?.problem && (
          <p className="mt-1.5 truncate text-sm text-muted" title={board.problem.text}>
            Research problem: {board.problem.text}
          </p>
        )}
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <Answer icon={<Check size={16} />} title="Agreed" count={board?.agreed.length}>
          {board?.agreed.length ? (
            <ul className="space-y-2">
              {board.agreed.map((point) => (
                <li key={point.text} className="flex gap-2 text-sm leading-snug">
                  <Check size={14} className="mt-0.5 shrink-0 text-ink-2" aria-hidden />
                  <span>
                    {point.text}
                    <Cite claim={point} onOpen={props.onOpenQuote} />
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>{board ? "Nothing settled by the whole team yet." : "Press Update."}</Empty>
          )}
        </Answer>

        <Answer icon={<Swords size={16} />} title="Still open" count={board?.contests.length}>
          <OpenIssues
            {...props}
            modules={modules}
            onSettle={(contest) => setRoom({ contest })}
            onLocate={locate}
          />
        </Answer>

        <Answer
          icon={<ArrowRight size={16} />}
          title="Your next step"
          strong
          aside={
            <label className="flex items-center gap-1 text-xs text-ink-2">
              I am
              <select
                value={props.me}
                onChange={(event) => props.onMe(event.target.value)}
                className="max-w-28 rounded-md border border-hairline bg-surface px-1 py-0.5 text-xs text-ink"
              >
                {state.workspace.members.map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.name}
                  </option>
                ))}
              </select>
            </label>
          }
        >
          <NextSteps {...props} />
        </Answer>
      </div>

      <section ref={map} className="scroll-mt-4">
        <h3 className="text-sm font-semibold">The work map</h3>
        <p className="text-xs text-muted">
          Each part of the work, colored by who works on it. Click a box or a person to highlight
          related work.
        </p>
        <div className="mt-2 mb-2">
          <Legend
            removed={false}
            people={state.workspace.members.map((m) => ({
              id: m.id,
              name: shortName(m.name),
              color: colors[m.id] ?? MUTED,
            }))}
          />
        </div>
        {(focus || (selected && modules.get(selected))) && (
          <p
            role="status"
            className="mb-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-md bg-wash px-3 py-1.5 text-xs text-ink-2"
          >
            <span>
              {focus
                ? `Showing ${name(focus)}'s work. The rest is dimmed, not finished.`
                : `Showing ${modules.get(selected ?? "")?.title} and what connects to it. The rest is dimmed.`}
            </span>
            <button
              type="button"
              onClick={() => {
                setFocus(null);
                setSelected(null);
              }}
              className="font-medium text-ink underline underline-offset-2"
            >
              Show everything
            </button>
          </p>
        )}
        <ArchitectureCanvas
          state={state}
          colors={colors}
          selected={selected}
          focus={focus}
          direction={compact ? "DOWN" : "RIGHT"}
          onSelect={(id) => setSelected((current) => (current === id ? null : id))}
        />
        {selected && modules.get(selected) && (
          <ModuleCard
            module={modules.get(selected) as ModuleView}
            state={state}
            contests={board?.contests ?? []}
            colors={colors}
            onSelect={setSelected}
            onClose={() => setSelected(null)}
            onOpenQuote={props.onOpenQuote}
            onDetails={props.onDetails}
          />
        )}
      </section>

      <section>
        <h3 className="mb-1 text-sm font-semibold">Who's working on what</h3>
        <p className="mb-2 text-xs text-muted">
          Click a person to highlight their work on the map.
        </p>
        <ul className="divide-y divide-hairline rounded-lg border border-hairline bg-surface">
          <li
            aria-hidden="true"
            className="hidden gap-x-4 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted sm:grid sm:grid-cols-[150px_minmax(0,1fr)_minmax(0,1fr)]"
          >
            <span>Person</span>
            <span>Current idea</span>
            <span>Next step</span>
          </li>
          {state.workspace.members.map((member) => {
            const person = board?.people.find((p) => p.member === member.id);
            const active = focus === member.id;
            return (
              <li key={member.id}>
                <button
                  type="button"
                  aria-pressed={active}
                  onClick={() => setFocus(active ? null : member.id)}
                  className={`grid w-full gap-x-4 gap-y-0.5 px-3 py-2.5 text-left text-sm hover:bg-wash sm:grid-cols-[150px_minmax(0,1fr)_minmax(0,1fr)] ${
                    active ? "bg-wash" : ""
                  }`}
                >
                  <span className="flex min-w-0 items-center gap-2 font-medium">
                    <Dot color={colors[member.id] ?? MUTED} size={9} />
                    <span className="truncate">{member.name}</span>
                  </span>
                  <span className="truncate" title={person?.idea?.text}>
                    {person?.idea?.text ?? <span className="text-muted">—</span>}
                  </span>
                  <span className="truncate text-ink-2" title={person?.next[0]?.text}>
                    {person?.next[0]?.text ?? ""}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      <UpdateBar {...props} />
      {room && (
        <ConvergeRoom
          state={state}
          contest={room.contest}
          sessionId={room.session}
          colors={colors}
          me={props.me}
          onClose={() => setRoom(null)}
          onOpenQuote={props.onOpenQuote}
          onAdopted={props.onAdopted}
        />
      )}
    </div>
  );
}

/** First visit only: what CONVERGE is, in three steps. */
function Intro({ onClose }: { onClose: () => void }) {
  const steps = [
    "Connect your Notion meeting notes",
    "Press Update: AI reads every meeting",
    "See what's agreed, what's open, and your next step",
  ];
  return (
    <aside className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-hairline bg-surface px-4 py-3 text-sm">
      <span className="font-semibold">How CONVERGE works</span>
      <ol className="flex flex-wrap gap-x-5 gap-y-1 text-ink-2">
        {steps.map((step, index) => (
          <li key={step} className="flex items-center gap-1.5">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-ink text-[11px] font-semibold text-surface">
              {index + 1}
            </span>
            {step}
          </li>
        ))}
      </ol>
      <button
        type="button"
        onClick={onClose}
        className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-ink-2 hover:bg-wash"
      >
        <X size={13} aria-hidden /> Got it
      </button>
    </aside>
  );
}

function Answer({
  icon,
  title,
  count,
  aside,
  strong = false,
  children,
}: {
  icon: ReactNode;
  title: string;
  count?: number;
  aside?: ReactNode;
  strong?: boolean; // the card that speaks to the reader directly
  children: ReactNode;
}) {
  return (
    <section
      className={`flex flex-col rounded-xl border bg-surface p-4 ${
        strong ? "border-ink/30 shadow-sm" : "border-hairline"
      }`}
    >
      <header className="mb-3 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-wash text-ink">
            {icon}
          </span>
          {title}
          {count ? <span className="text-xs font-normal text-muted">{count}</span> : null}
        </h3>
        {aside}
      </header>
      {children}
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted">{children}</p>;
}

/** A small quote button after a sentence: opens the note line the sentence rests on. */
function Cite({
  claim,
  onOpen,
}: {
  claim: { evidence: Evidence[] };
  onOpen: (sourceId: string, line: number, quote?: string) => void;
}) {
  const first = claim.evidence[0];
  if (!first) return null;
  return (
    <button
      type="button"
      onClick={() => onOpen(first.source, 0, first.quote)}
      title={`From the notes: “${first.quote}”`}
      aria-label={`Show the note this comes from: ${first.quote}`}
      className="ml-1 inline-flex translate-y-[-1px] items-center rounded p-0.5 align-middle text-muted hover:bg-wash hover:text-ink"
    >
      <Quote size={12} />
    </button>
  );
}

function Sides({ contest, state, colors }: { contest: Contest } & Pick<Props, "state" | "colors">) {
  const { name } = lookup(state);
  return (
    <ul className="mt-2 space-y-1">
      {contest.sides.map((side) => (
        <li key={side.position} className="flex items-start gap-2 text-sm">
          <span className="flex shrink-0 items-center gap-0.5 pt-1.5">
            {side.members.map((member) => (
              <Dot key={member} color={colors[member] ?? MUTED} />
            ))}
          </span>
          <span className="min-w-0">
            <span className="font-medium">
              {side.members.map((member) => shortName(name(member))).join(", ")}
            </span>
            {side.position && <span className="text-ink-2">: {side.position}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

function OpenIssues({
  board,
  state,
  colors,
  modules,
  onOpenQuote,
  onSettle,
  onLocate,
}: Props & {
  modules: Map<string, ModuleView>;
  onSettle: (contest: Contest) => void;
  onLocate: (moduleId: string) => void;
}) {
  const [open, setOpen] = useState(0);
  if (!board?.contests.length) {
    return <Empty>{board ? "No open disagreements." : "Press Update."}</Empty>;
  }
  const contest = board.contests[open] ?? board.contests[0];
  return (
    <div className="space-y-3">
      <div>
        {open === 0 && (
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-2">
            Most urgent
          </p>
        )}
        <p className="text-sm font-semibold leading-snug">
          {contest.question}
          <Cite claim={contest} onOpen={onOpenQuote} />
        </p>
        {contest.stakes && <p className="mt-0.5 text-[13px] text-ink-2">{contest.stakes}</p>}
        <Sides contest={contest} state={state} colors={colors} />
        {contest.decide_by && (
          <p className="mt-1.5 text-xs text-ink-2">
            <span className="font-semibold">Decided:</span> {contest.decide_by}
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
          <button
            type="button"
            onClick={() => onSettle(contest)}
            title="AI delegates argue each side from what each person said, then propose a decision with next steps."
            className="inline-flex items-center gap-1.5 rounded-lg bg-ink px-3 py-1.5 text-xs font-medium text-surface hover:bg-ink-2"
          >
            <Swords size={13} aria-hidden /> Settle with an AI debate
          </button>
          {contest.module && modules.get(contest.module) && (
            <button
              type="button"
              onClick={() => contest.module && onLocate(contest.module)}
              className="inline-flex items-center gap-1 text-xs text-ink-2 hover:text-ink hover:underline"
            >
              <MapPin size={13} aria-hidden /> On the map
            </button>
          )}
        </div>
      </div>
      {board.contests.length > 1 && (
        <ul className="space-y-1 border-t border-hairline pt-2">
          {board.contests.map((other, index) =>
            index === open ? null : (
              <li key={other.question}>
                <button
                  type="button"
                  onClick={() => setOpen(index)}
                  className="flex w-full items-start gap-1.5 text-left text-[13px] text-ink-2 hover:text-ink"
                >
                  <Swords size={12} className="mt-1 shrink-0" aria-hidden />
                  <span className="min-w-0 truncate">{other.question}</span>
                </button>
              </li>
            ),
          )}
        </ul>
      )}
    </div>
  );
}

function NextSteps({ board, me, state, onOpenQuote }: Props) {
  const [all, setAll] = useState(false);
  const { name } = lookup(state);
  const steps = board?.people.find((p) => p.member === me)?.next ?? [];
  if (!steps.length) {
    return (
      <Empty>
        {board ? `The notes give ${shortName(name(me))} no open step right now.` : "Press Update."}
      </Empty>
    );
  }
  const shown = all ? steps : steps.slice(0, 1);
  return (
    <div className="space-y-4">
      {shown.map((step, index) => {
        const contest = step.contest !== null ? board?.contests[step.contest] : undefined;
        return (
          <div key={step.text} className={index ? "border-t border-hairline pt-3" : ""}>
            <p className="text-base font-semibold leading-snug">
              {step.text}
              <Cite claim={step} onOpen={onOpenQuote} />
            </p>
            <dl className="mt-2 space-y-1 text-[13px] leading-snug text-ink-2">
              {step.why && (
                <div className="flex gap-2">
                  <dt className="w-16 shrink-0 font-semibold text-muted">Why</dt>
                  <dd>{step.why}</dd>
                </div>
              )}
              {step.due && (
                <div className="flex gap-2">
                  <dt className="w-16 shrink-0 font-semibold text-muted">Due</dt>
                  <dd>{step.due}</dd>
                </div>
              )}
              {contest && (
                <div className="flex gap-2">
                  <dt className="w-16 shrink-0 font-semibold text-muted">Resolves</dt>
                  <dd>{contest.question}</dd>
                </div>
              )}
            </dl>
          </div>
        );
      })}
      {steps.length > 1 && (
        <button
          type="button"
          onClick={() => setAll(!all)}
          className="text-xs font-medium text-ink-2 underline underline-offset-2"
        >
          {all ? "Show less" : `+${steps.length - 1} more`}
        </button>
      )}
    </div>
  );
}

function ModuleCard({
  module,
  state,
  contests,
  colors,
  onSelect,
  onClose,
  onOpenQuote,
  onDetails,
}: {
  module: ModuleView;
  state: LineageState;
  contests: Contest[];
  colors: Record<string, string>;
  onSelect: (id: string) => void;
  onClose: () => void;
  onOpenQuote: (sourceId: string, line: number, quote?: string) => void;
  onDetails: (moduleId: string | null) => void;
}) {
  const { name, title: ideaTitle } = lookup(state);
  const titles = new Map(state.modules.map((m) => [m.id, m.title]));
  const feeds = state.modules.filter((m) => m.status !== "removed" && m.inputs.includes(module.id));
  const rows = module.contributions.length
    ? module.contributions
    : module.owner
      ? [
          {
            member: module.owner,
            approach: module.note,
            idea: module.idea,
            source: "",
            line: 0,
            quote: "",
          },
        ]
      : [];
  const link = (id: string) => (
    <button key={id} type="button" onClick={() => onSelect(id)} className="hover:underline">
      {titles.get(id) ?? id}
    </button>
  );
  return (
    <article className="appear mt-3 rounded-lg border border-ink/20 bg-surface p-4">
      <header className="flex items-start justify-between gap-3">
        <h4 className="text-base font-semibold">
          {module.title}
          <span className="ml-2 text-xs font-normal text-ink-2">
            {module.status === "adopted" ? "✓ In use" : "Proposed"}
            {module.contested && " · ⚔ competing approaches"}
          </span>
        </h4>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded-md p-0.5 text-ink-2 hover:bg-wash"
        >
          <X size={16} />
        </button>
      </header>
      {contests
        .filter((c) => c.module === module.id)
        .map((c) => (
          <p key={c.question} className="mt-1 flex gap-1.5 text-[13px] text-ink-2">
            <Swords size={13} className="mt-0.5 shrink-0" aria-hidden />
            Open question: {c.question}
          </p>
        ))}
      <ul className="mt-3 space-y-2.5">
        {rows.map((c) => (
          <li key={c.member} className="flex gap-2.5 text-sm leading-snug">
            <span className="pt-1.5">
              <Dot color={colors[c.member] ?? MUTED} size={9} />
            </span>
            <span className="min-w-0">
              <span className="font-semibold">{shortName(name(c.member))}</span>
              {c.idea && <span className="text-xs text-muted"> · {ideaTitle(c.idea)}</span>}
              <span className="block text-ink-2">
                {c.approach || "Works on this part"}
                {c.quote && (
                  <Cite
                    claim={{ evidence: [{ source: c.source, quote: c.quote }] }}
                    onOpen={onOpenQuote}
                  />
                )}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-hairline pt-3 text-xs text-ink-2">
        <span className="flex flex-wrap gap-x-3 gap-y-1">
          {module.inputs.length > 0 && <span>From: {module.inputs.map(link)}</span>}
          {feeds.length > 0 && <span>Into: {feeds.map((m) => link(m.id))}</span>}
        </span>
        <button
          type="button"
          onClick={() => onDetails(module.id)}
          className="font-medium text-ink underline decoration-hairline underline-offset-2 hover:decoration-ink"
        >
          History and editing →
        </button>
      </div>
    </article>
  );
}

function UpdateBar({ board, update, onUpdate, state }: Props) {
  const running = update.state === "running";
  const notes = state.sources.filter((s) => s.kind !== "session").length;
  return (
    <div className="fixed inset-x-0 bottom-0 z-10 border-t border-hairline bg-surface/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] items-center gap-3 px-4 py-3 sm:px-6">
        <p className="min-w-0 flex-1 truncate text-xs text-ink-2" role="status">
          {running
            ? `${update.step}${update.total ? ` · ${update.done}/${update.total}` : ""}…`
            : update.state === "failed"
              ? `Update failed: ${update.error}`
              : board
                ? `Updated ${formatDate(board.written_at)}, ${clock.format(new Date(board.written_at))} from ${notes} meeting notes · read by Muse Spark`
                : `${notes} meeting notes · not analyzed yet`}
        </p>
        <button
          type="button"
          onClick={onUpdate}
          disabled={running}
          title="Read new meeting notes and refresh this page"
          className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-ink px-4 py-2 text-sm font-medium text-surface hover:bg-ink-2 disabled:opacity-70"
        >
          <RefreshCw size={15} className={running ? "animate-spin" : ""} aria-hidden />
          {running ? "Updating…" : "Update"}
        </button>
      </div>
    </div>
  );
}
