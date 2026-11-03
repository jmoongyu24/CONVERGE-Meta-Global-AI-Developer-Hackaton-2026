// The Converge Room: one open question, a delegate per member arguing only from that member's
// own words (every quote checked), and a facilitator's proposals that end in who does what
// next. Adopting one writes it to the session note and Notion, and the board follows.

import { Check, Loader2, Minus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { shortName } from "./ArchitectureView";
import {
  adoptProposal,
  type Contest,
  type ConvergeSession,
  fetchConverge,
  type LineageState,
  openConverge,
  type Turn,
} from "./api";
import { Dot, lookup } from "./ui";

const POLL_MS = 1500;
const MUTED = "var(--color-muted)";
const LIKELY = {
  yes: { icon: Check, label: "likely yes" },
  maybe: { icon: Minus, label: "maybe" },
  no: { icon: X, label: "likely no" },
} as const;

interface Props {
  state: LineageState;
  contest: Contest;
  sessionId?: string; // reopen a finished room instead of starting a new one
  colors: Record<string, string>;
  me: string;
  onClose: () => void;
  onOpenQuote: (sourceId: string, line: number, quote?: string) => void;
  onAdopted: (message: string) => void;
}

export function ConvergeRoom({
  state,
  contest,
  sessionId,
  colors,
  me,
  onClose,
  onOpenQuote,
  onAdopted,
}: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [session, setSession] = useState<ConvergeSession | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [adopting, setAdopting] = useState<number | null>(null);
  const { name } = lookup(state);
  const workspace = state.workspace.id;
  const modules = new Map(state.modules.map((m) => [m.id, m.title]));

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: one session per opening of the room
  useEffect(() => {
    const opening = sessionId
      ? fetchConverge(workspace, sessionId)
      : openConverge(workspace, {
          question: contest.question,
          members: [...new Set(contest.sides.flatMap((side) => side.members))],
          module: contest.module,
        });
    opening.then(setSession, (e: unknown) => setProblem(String(e)));
  }, []);

  useEffect(() => {
    if (session?.state !== "running") return;
    const timer = window.setTimeout(
      () => fetchConverge(workspace, session.id).then(setSession, () => undefined),
      POLL_MS,
    );
    return () => window.clearTimeout(timer);
  }, [session, workspace]);

  const adopt = async (index: number) => {
    if (!session) return;
    setAdopting(index);
    try {
      const result = await adoptProposal(workspace, session.id, index, me);
      onAdopted(
        `Adopted. Next steps are in the session note${
          result.notion === "synced" ? " and in Notion" : ""
        }; the board is updating.`,
      );
      dialog.current?.close();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
      setAdopting(null);
    }
  };

  const rounds = [1, 2].map((round) => ({
    round,
    turns: session?.turns.filter((t) => t.round === round) ?? [],
  }));

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      className="m-auto max-h-[92vh] w-full max-w-4xl overflow-y-auto rounded-xl bg-page p-0 text-ink shadow-xl backdrop:bg-ink/30"
    >
      <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-hairline bg-surface px-5 py-4">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">
            Converge Room
          </p>
          <h2 className="mt-1 text-base font-semibold leading-snug">{contest.question}</h2>
          {contest.stakes && <p className="mt-0.5 text-sm text-ink-2">{contest.stakes}</p>}
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
            {contest.sides.map((side) => (
              <li key={side.position} className="flex items-center gap-1.5">
                {side.members.map((member) => (
                  <Dot key={member} color={colors[member] ?? MUTED} />
                ))}
                {side.members.map((member) => shortName(name(member))).join(", ")}: {side.position}
              </li>
            ))}
          </ul>
        </div>
        <button
          type="button"
          onClick={() => dialog.current?.close()}
          className="rounded-md p-1 text-ink-2 hover:bg-wash"
          aria-label="Close"
        >
          <X size={18} />
        </button>
      </header>

      <div className="space-y-6 px-5 py-5">
        <p className="text-xs text-muted">
          Each delegate speaks only from what that person said in the notes, and every quote is
          checked against them. A delegate without a matching quote is shown as unsupported.
        </p>

        {rounds.map(({ round, turns }) =>
          turns.length ? (
            <section key={round}>
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">
                {round === 1 ? "Round 1 · positions" : "Round 2 · answers"}
              </h3>
              <ul className="grid gap-3 md:grid-cols-2">
                {turns.map((turn) => (
                  <TurnCard
                    key={turn.member}
                    turn={turn}
                    name={name(turn.member)}
                    color={colors[turn.member] ?? MUTED}
                    onOpenQuote={onOpenQuote}
                  />
                ))}
              </ul>
            </section>
          ) : null,
        )}

        {session?.state === "running" && (
          <p className="flex items-center gap-2 text-sm text-ink-2" role="status">
            <Loader2 size={15} className="animate-spin" aria-hidden />
            {session.step}…
          </p>
        )}
        {(problem || session?.state === "failed") && (
          <p className="text-sm text-[#b42318]">{problem ?? session?.error}</p>
        )}

        {session?.state === "done" && (
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">
              Ways to settle it
            </h3>
            <div className="grid gap-4 md:grid-cols-2">
              {session.proposals.map((proposal, index) => (
                <article
                  key={proposal.title}
                  className="flex flex-col rounded-lg border border-hairline bg-surface p-4"
                >
                  <h4 className="text-sm font-semibold">{proposal.title}</h4>
                  <p className="mt-1 text-sm leading-snug">{proposal.decision}</p>
                  {proposal.why?.evidence[0] && (
                    <button
                      type="button"
                      onClick={() =>
                        onOpenQuote(
                          proposal.why?.evidence[0].source ?? "",
                          0,
                          proposal.why?.evidence[0].quote,
                        )
                      }
                      className="mt-1 text-left text-xs text-muted hover:underline"
                    >
                      Why: {proposal.why.text} · “{proposal.why.evidence[0].quote}”
                    </button>
                  )}
                  <p className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-muted">
                    Who does what next
                  </p>
                  <ul className="mt-1 space-y-1.5 text-sm">
                    {proposal.assignments.map((assignment) => {
                      const acceptance = proposal.acceptance.find(
                        (a) => a.member === assignment.member,
                      );
                      const likely = acceptance && LIKELY[acceptance.likely];
                      return (
                        <li key={assignment.member} className="flex gap-2">
                          <span className="pt-1.5">
                            <Dot color={colors[assignment.member] ?? MUTED} />
                          </span>
                          <span className="min-w-0 leading-snug">
                            <span className="font-medium">
                              {shortName(name(assignment.member))}
                            </span>
                            {": "}
                            {assignment.step}
                            {assignment.module && (
                              <span className="text-xs text-muted">
                                {" "}
                                · {modules.get(assignment.module) ?? assignment.module}
                              </span>
                            )}
                            {likely && (
                              <span
                                title={acceptance.reason}
                                className="ml-1.5 inline-flex translate-y-[-1px] items-center gap-0.5 rounded-full border border-hairline px-1.5 text-[10px] text-ink-2"
                              >
                                <likely.icon size={10} aria-hidden />
                                {likely.label}
                              </span>
                            )}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                  <p className="mt-3 text-xs text-ink-2">
                    <span className="font-semibold">Risk: </span>
                    {proposal.risk}
                  </p>
                  <button
                    type="button"
                    disabled={adopting !== null}
                    onClick={() => adopt(index)}
                    className="mt-4 self-start rounded-lg bg-ink px-3 py-1.5 text-sm font-medium text-surface hover:bg-ink-2 disabled:opacity-60"
                  >
                    {adopting === index ? "Adopting…" : `Adopt as ${shortName(name(me))}`}
                  </button>
                </article>
              ))}
            </div>
          </section>
        )}
      </div>
    </dialog>
  );
}

function TurnCard({
  turn,
  name,
  color,
  onOpenQuote,
}: {
  turn: Turn;
  name: string;
  color: string;
  onOpenQuote: (sourceId: string, line: number, quote?: string) => void;
}) {
  return (
    <li
      className={`appear rounded-lg border border-hairline bg-surface p-3 ${turn.supported ? "" : "opacity-70"}`}
      style={{ borderLeft: `4px solid ${color}` }}
    >
      <p className="text-xs font-semibold text-ink-2">{name}'s delegate</p>
      <p className="mt-1 text-sm leading-snug">{turn.message}</p>
      {turn.supported ? (
        <ul className="mt-2 space-y-1">
          {turn.evidence.map((e) => (
            <li key={`${e.source}-${e.quote}`}>
              <button
                type="button"
                onClick={() => onOpenQuote(e.source, 0, e.quote)}
                className="text-left text-xs text-muted hover:underline"
              >
                “{e.quote}”
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs italic text-muted">No quote checked out: unsupported.</p>
      )}
    </li>
  );
}
