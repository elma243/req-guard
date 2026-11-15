"use client";

// The client answers the team's questions here, on a phone or a computer, without an account.
// Answers are sent exactly as typed (English, Bangla or Banglish). The link works once.

import { useEffect, useState } from "react";
import { ShieldIcon } from "@/components/AuthShell";
import {
  ApiError,
  MAX_ANSWER_CHARS,
  getPublicClarification,
  submitClarificationAnswers,
  type PublicClarification,
} from "@/lib/api";

type State =
  | { kind: "loading" }
  | { kind: "closed"; title: string; message: string }
  | { kind: "form"; data: PublicClarification }
  | { kind: "thanks"; answered: number };

function closedState(err: unknown): State {
  if (err instanceof ApiError && (err.status === 404 || err.status === 410 || err.status === 429)) {
    const title = err.status === 404 ? "Link not found" : err.status === 429 ? "Please wait a moment" : "Link closed";
    return { kind: "closed", title, message: err.message };
  }
  return {
    kind: "closed",
    title: "Something went wrong",
    message: "The page could not be loaded. Please check your connection and try again.",
  };
}

export default function ClarifyForm({ token }: { token: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getPublicClarification(token)
      .then((data) => !cancelled && setState({ kind: "form", data }))
      .catch((err) => !cancelled && setState(closedState(err)));
    return () => {
      cancelled = true;
    };
  }, [token]);

  const filled = Object.values(answers).filter((text) => text.trim()).length;

  async function submit(data: PublicClarification) {
    if (sending || filled === 0) return;
    setSending(true);
    setError(null);
    try {
      const payload = data.questions
        .filter((q) => answers[q.id]?.trim())
        .map((q) => ({ question_id: q.id, text: answers[q.id] }));
      const { answered } = await submitClarificationAnswers(token, payload);
      setState({ kind: "thanks", answered });
    } catch (err) {
      if (err instanceof ApiError && (err.status === 410 || err.status === 404)) setState(closedState(err));
      else setError(err instanceof ApiError ? err.message : "Your answers could not be sent. Please try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="min-h-screen bg-zinc-950 px-4 py-6 text-zinc-100 sm:py-12">
      <div className="mx-auto w-full max-w-xl">
        <header className="flex items-center gap-2 text-sm text-zinc-400">
          <ShieldIcon size={18} />
          <span>Questions from your project team</span>
        </header>

        {state.kind === "loading" && (
          <div className="mt-8 animate-pulse space-y-3" aria-label="Loading">
            <div className="h-6 w-2/3 rounded bg-white/10" />
            <div className="h-24 rounded-xl bg-white/5" />
            <div className="h-24 rounded-xl bg-white/5" />
          </div>
        )}

        {state.kind === "closed" && (
          <section className="mt-8 rounded-2xl border border-white/10 bg-white/5 p-6">
            <h1 className="text-xl font-semibold">{state.title}</h1>
            <p className="mt-2 text-zinc-300">{state.message}</p>
          </section>
        )}

        {state.kind === "thanks" && (
          <section className="mt-8 rounded-2xl border border-emerald-400/30 bg-emerald-500/10 p-6" role="status">
            <h1 className="text-xl font-semibold text-emerald-100">Thank you!</h1>
            <p className="mt-2 text-emerald-50/90">
              Your {state.answered === 1 ? "answer was" : `${state.answered} answers were`} sent to the team. You can
              close this page.
            </p>
          </section>
        )}

        {state.kind === "form" && (
          <form
            className="mt-6"
            onSubmit={(e) => {
              e.preventDefault();
              void submit(state.data);
            }}
          >
            <h1 className="text-2xl font-semibold leading-snug">{state.data.project_name}</h1>
            <p className="mt-2 text-sm text-zinc-400">
              The team has a few questions so they build exactly what you need. You can answer in English, বাংলা or
              Banglish, and skip any question you are not sure about. You can send your answers once.
            </p>

            <ol className="mt-6 space-y-5">
              {state.data.questions.map((q, index) => (
                <li key={q.id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                  <label htmlFor={`answer-${q.id}`} className="block text-base leading-relaxed text-zinc-100">
                    <span className="mr-1 text-zinc-500">{index + 1}.</span>
                    {q.text}
                  </label>
                  <textarea
                    id={`answer-${q.id}`}
                    rows={3}
                    maxLength={MAX_ANSWER_CHARS}
                    value={answers[q.id] ?? ""}
                    onChange={(e) => setAnswers((current) => ({ ...current, [q.id]: e.target.value }))}
                    placeholder="Your answer"
                    className="mt-3 block w-full rounded-xl border border-white/10 bg-zinc-900 px-3 py-3 text-base text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-400/30"
                  />
                </li>
              ))}
            </ol>

            {error && (
              <p role="alert" className="mt-4 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={sending || filled === 0}
              className="mt-6 w-full rounded-xl bg-indigo-500 px-4 py-3 text-base font-medium text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {sending ? "Sending…" : filled === 0 ? "Send answers" : `Send ${filled} answer${filled === 1 ? "" : "s"}`}
            </button>
            <p className="mt-3 text-center text-xs text-zinc-500">
              This link works until {new Date(state.data.expires_at).toLocaleDateString(undefined, { dateStyle: "medium" })}.
            </p>
          </form>
        )}
      </div>
    </main>
  );
}
