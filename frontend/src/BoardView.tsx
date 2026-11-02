// The first screen: where the team stands, what I should do next, the shared work as one
// picture, what is being contested, and who is doing what. Everything comes from the latest
// update; every sentence links to the quote behind it.

import { Quote, RefreshCw, Swords } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { ArchitectureCanvas, Legend, peopleOf, shortName } from "./ArchitectureView";
import {
  type Board,
  type Claim,
  type Contest,
  fetchConverge,
  type LineageState,
  type ModuleView,
  type UpdateStatus,
} from "./api";
import { ConvergeRoom } from "./ConvergeView";
import { Dot, formatDate, lookup } from "./ui";

const MUTED = "var(--color-muted)";
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
  openRoom?: string | null; // a Converge Room session to reopen, from a shared link
  compact: boolean; // the narrow side panel next to Notion
}

export function BoardView(props: Props) {
  const { state, board, colors, compact } = props;
  const { name } = lookup(state);
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [room, setRoom] = useState<{ contest: Contest; session?: string } | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: a shared link opens its room once
  useEffect(() => {
    if (!props.openRoom) return;
    fetchConverge(state.workspace.id, props.openRoom).then(
      (session) =>
        setRoom({
          session: session.id,
          contest: board?.contests.find((c) => c.question === session.question) ?? {
            question: session.question,
            module: session.module,
            sides: [{ members: session.members, position: "" }],
            decide_by: null,
            evidence: [],
          },
        }),
      () => undefined,
    );
  }, []);
  const modules = new Map(state.modules.map((m) => [m.id, m]));
  const module = selected ? modules.get(selected) : undefined;
  const cite = (claim: Claim | null) => claim && <Cite claim={claim} onOpen={props.onOpenQuote} />;

  return (
    <div className={`space-y-6 ${compact ? "pb-24" : ""}`}>
      <section>
        <Heading>Where we are</Heading>
        <h2 className="text-lg font-semibold leading-snug sm:text-xl">
          {board?.problem?.text ?? state.workspace.name}
          {cite(board?.problem ?? null)}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-2">
          {board?.status ? (
            <>
              {board.status.text}
              {cite(board.status)}
            </>
          ) : (
            "Press Update to have Muse Spark read the notes and write this briefing."
          )}
        </p>
      </section>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-6 lg:order-2">
          <ForYou {...props} />
          {board && board.contests.length > 0 && (
            <section>
              <Heading>Competing now</Heading>
              <ul className="space-y-3">
                {board.contests.map((contest) => (
                  <li
                    key={contest.question}
                    className="rounded-lg border border-hairline bg-surface p-3"
                  >
                    <p className="flex gap-2 text-sm font-medium leading-snug">
                      <Swords size={15} className="mt-0.5 shrink-0 text-ink-2" aria-hidden />
                      <span>
                        {contest.question}
                        <Cite
                          claim={{ text: contest.question, evidence: contest.evidence }}
                          onOpen={props.onOpenQuote}
                        />
                      </span>
                    </p>
                    <ul className="mt-2 space-y-1 text-sm text-ink-2">
                      {contest.sides.map((side) => (
                        <li key={side.position} className="flex items-start gap-2">
                          <span className="flex shrink-0 items-center gap-0.5 pt-1.5">
                            {side.members.map((member) => (
                              <Dot key={member} color={colors[member] ?? MUTED} />
                            ))}
                          </span>
                          <span>
                            <span className="font-medium text-ink">
                              {side.members.map((member) => shortName(name(member))).join(", ")}
                            </span>{" "}
                            — {side.position}
                          </span>
                        </li>
                      ))}
                    </ul>
                    {contest.decide_by && (
                      <p className="mt-2 text-xs text-muted">Decided: {contest.decide_by}</p>
                    )}
                    <div className="mt-3 flex flex-wrap items-center gap-3">
                      <button
                        type="button"
                        onClick={() => setRoom({ contest })}
                        className="rounded-lg bg-ink px-3 py-1.5 text-xs font-medium text-surface hover:bg-ink-2"
                      >
                        Converge on this →
                      </button>
                      {contest.module && modules.get(contest.module) && (
                        <button
                          type="button"
                          onClick={() => setSelected(contest.module)}
                          className="text-xs font-medium text-ink underline decoration-hairline underline-offset-2 hover:decoration-ink"
                        >
                          Show in the picture
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        <div className="min-w-0 space-y-6 lg:order-1">
          <section>
            <div className="mb-2 flex items-center justify-between gap-3">
              <Heading>The work</Heading>
              {focus && (
                <button
                  type="button"
                  onClick={() => setFocus(null)}
                  className="text-xs text-ink-2 underline underline-offset-2"
                >
                  Show everyone
                </button>
              )}
            </div>
            <ArchitectureCanvas
              state={state}
              colors={colors}
              selected={selected}
              focus={focus}
              direction={compact ? "DOWN" : "RIGHT"}
              onSelect={(id) => setSelected((current) => (current === id ? null : id))}
            />
            <div className="mt-2">
              <Legend removed={false} />
            </div>
            {module && (
              <ModuleCard
                module={module}
                state={state}
                colors={colors}
                onSelect={setSelected}
                onClose={() => setSelected(null)}
                onOpenQuote={props.onOpenQuote}
                onDetails={props.onDetails}
              />
            )}
          </section>
          <section>
            <Heading>Who is doing what</Heading>
            <div className="grid gap-3 sm:grid-cols-2">
              {state.workspace.members.map((member) => {
                const person = board?.people.find((p) => p.member === member.id);
                const active = focus === member.id;
                return (
                  <button
                    key={member.id}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setFocus(active ? null : member.id)}
                    className={`rounded-lg border bg-surface p-3 text-left transition-colors hover:border-ink-2 ${
                      active ? "border-ink ring-1 ring-ink" : "border-hairline"
                    }`}
                    style={{ borderLeft: `4px solid ${colors[member.id] ?? MUTED}` }}
                  >
                    <p className="flex items-baseline gap-2">
                      <span className="text-sm font-semibold">{member.name}</span>
                      <span className="truncate text-xs text-muted">{member.role}</span>
                    </p>
                    <p className="mt-1.5 text-sm leading-snug">
                      {person?.idea?.text ?? <span className="text-muted">No position yet</span>}
                    </p>
                    {person?.next[0] && (
                      <p className="mt-1.5 text-xs leading-snug text-ink-2">
                        <span className="font-semibold text-muted">Next </span>
                        {person.next[0].text}
                      </p>
                    )}
                  </button>
                );
              })}
            </div>
            <p className="mt-2 text-xs text-muted">
              Click a person to light up the parts of the picture they work on.
            </p>
          </section>
        </div>
      </div>

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

function Heading({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">
      {children}
    </h3>
  );
}

/** A small quote button after a sentence: opens the note line the sentence rests on. */
function Cite({
  claim,
  onOpen,
}: {
  claim: Claim;
  onOpen: (sourceId: string, line: number, quote?: string) => void;
}) {
  const first = claim.evidence[0];
  if (!first) return null;
  return (
    <button
      type="button"
      onClick={() => onOpen(first.source, 0, first.quote)}
      title={`“${first.quote}”`}
      aria-label={`Show the quote behind this: ${first.quote}`}
      className="ml-1.5 inline-flex translate-y-[-1px] items-center rounded p-0.5 align-middle text-muted hover:bg-wash hover:text-ink"
    >
      <Quote size={12} />
    </button>
  );
}

function ForYou({ state, board, colors, me, onMe, onOpenQuote }: Props) {
  const { name, date } = lookup(state);
  const person = board?.people.find((p) => p.member === me);
  const contests = board?.contests.filter((c) => c.sides.some((s) => s.members.includes(me)));
  const mine = state.modules.filter((m) => m.status !== "removed" && peopleOf(m).includes(me));
  return (
    <section
      className="rounded-lg border border-ink/15 bg-surface p-4"
      style={{ borderTop: `3px solid ${colors[me] ?? MUTED}` }}
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted">
          Your next steps
        </h3>
        <label className="flex items-center gap-1.5 text-xs text-ink-2">
          I am
          <select
            value={me}
            onChange={(event) => onMe(event.target.value)}
            className="rounded-md border border-hairline bg-surface px-1.5 py-0.5 text-xs text-ink"
          >
            {state.workspace.members.map((member) => (
              <option key={member.id} value={member.id}>
                {member.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!person || person.next.length === 0 ? (
        <p className="text-sm text-muted">
          {board
            ? `The notes give ${name(me)} no open step right now.`
            : "Press Update to see your next steps."}
        </p>
      ) : (
        <ol className="space-y-2.5">
          {person.next.map((step, index) => (
            <li key={step.text} className="flex gap-2.5 text-sm leading-snug">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-ink text-[11px] font-semibold text-surface">
                {index + 1}
              </span>
              <span>
                {step.text}
                {step.evidence[0] && (
                  <button
                    type="button"
                    onClick={() => onOpenQuote(step.evidence[0].source, 0, step.evidence[0].quote)}
                    className="mt-0.5 block text-left text-xs text-muted hover:underline"
                  >
                    “{step.evidence[0].quote}” · {date(step.evidence[0].source)}
                  </button>
                )}
              </span>
            </li>
          ))}
        </ol>
      )}
      {contests && contests.length > 0 && (
        <p className="mt-3 border-t border-hairline pt-3 text-xs leading-relaxed text-ink-2">
          <span className="font-semibold">You are part of: </span>
          {contests.map((c) => c.question).join(" · ")}
        </p>
      )}
      {mine.length > 0 && (
        <p className="mt-2 text-xs text-ink-2">
          <span className="font-semibold">Your modules: </span>
          {mine.map((m) => m.title).join(", ")}
        </p>
      )}
    </section>
  );
}

function ModuleCard({
  module,
  state,
  colors,
  onSelect,
  onClose,
  onOpenQuote,
  onDetails,
}: {
  module: ModuleView;
  state: LineageState;
  colors: Record<string, string>;
  onSelect: (id: string) => void;
  onClose: () => void;
  onOpenQuote: (sourceId: string, line: number, quote?: string) => void;
  onDetails: (moduleId: string | null) => void;
}) {
  const { name, title: ideaTitle, date } = lookup(state);
  const titles = new Map(state.modules.map((m) => [m.id, m.title]));
  const feeds = state.modules.filter((m) => m.status !== "removed" && m.inputs.includes(module.id));
  const link = (id: string) => (
    <button key={id} type="button" onClick={() => onSelect(id)} className="hover:underline">
      {titles.get(id) ?? id}
    </button>
  );
  return (
    <article className="appear mt-4 rounded-lg border border-ink/20 bg-surface p-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-base font-semibold">{module.title}</h4>
          <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-ink-2">
            {module.status}
            {module.contested && (
              <span className="rounded-full bg-ink px-1.5 text-[10px] font-bold text-surface">
                VS · competing approaches
              </span>
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-xs text-ink-2 underline underline-offset-2"
        >
          Close
        </button>
      </header>
      {module.note && <p className="mt-2 text-sm text-ink-2">{module.note}</p>}
      <ul className="mt-3 space-y-3">
        {(module.contributions.length
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
            : []
        ).map((c) => (
          <li key={c.member} className="flex gap-2.5">
            <span className="pt-1.5">
              <Dot color={colors[c.member] ?? MUTED} size={10} />
            </span>
            <div className="min-w-0 text-sm leading-snug">
              <p>
                <span className="font-semibold">{name(c.member)}</span>
                {c.idea && <span className="text-xs text-muted"> · {ideaTitle(c.idea)}</span>}
              </p>
              <p className="text-ink-2">{c.approach || "Works on this module"}</p>
              {c.quote && (
                <button
                  type="button"
                  onClick={() => onOpenQuote(c.source, c.line)}
                  className="mt-0.5 text-left text-xs text-muted hover:underline"
                >
                  “{c.quote}” · {date(c.source)}
                </button>
              )}
            </div>
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

function UpdateBar({ board, update, onUpdate, compact, state }: Props) {
  const running = update.state === "running";
  const notes = state.sources.filter((s) => s.kind !== "session").length;
  return (
    <div
      className={`flex flex-wrap items-center gap-3 border-t border-hairline bg-surface pt-4 ${
        compact
          ? "fixed inset-x-0 bottom-0 z-10 px-4 pb-4 shadow-[0_-4px_12px_rgb(0_0_0/0.04)]"
          : ""
      }`}
    >
      <button
        type="button"
        onClick={onUpdate}
        disabled={running}
        className="inline-flex items-center gap-2 rounded-lg bg-ink px-4 py-2 text-sm font-medium text-surface hover:bg-ink-2 disabled:opacity-70"
      >
        <RefreshCw size={15} className={running ? "animate-spin" : ""} aria-hidden />
        {running ? "Updating…" : "Update"}
      </button>
      <p className="min-w-0 flex-1 text-xs text-ink-2" role="status">
        {running
          ? `${update.step}${update.total ? ` · ${update.done}/${update.total}` : ""}`
          : update.state === "failed"
            ? `Update failed: ${update.error}`
            : board
              ? `Updated ${formatDate(board.written_at)}, ${clock.format(new Date(board.written_at))} · ${notes} notes read by Muse Spark`
              : `${notes} notes · not analyzed yet`}
      </p>
    </div>
  );
}
