"use client";

// Clarifications page (Step 8A): every question for the client grouped by requirement, choosing questions for a
// share link, and the links with their state. The app never sends anything to the client: the lead copies the
// link and shares it themselves. Questions are edited in the requirement panel.

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import Modal from "@/components/Modal";
import { ToastStack, useToasts } from "@/components/Toast";
import { ErrorBanner, errorText, primaryButton, secondaryButton } from "@/components/ui";
import { CATEGORY_LABELS } from "@/components/requirements/ValidationFindings";
import { formatTime } from "@/components/requirements/labels";
import {
  MAX_LINK_QUESTIONS,
  createLink,
  generateAllQuestions,
  listLinks,
  listQuestions,
  revokeLink,
  type ClarificationLink,
  type ClarificationQuestion,
  type CreatedLink,
  type LinkState,
} from "@/lib/api";

const LINK_STYLE: Record<LinkState, string> = {
  active: "text-sky-300 ring-sky-400/30",
  answered: "text-emerald-300 ring-emerald-400/30",
  expired: "text-zinc-400 ring-white/10",
  revoked: "text-rose-300 ring-rose-400/30",
};

const STATE_STYLE: Record<ClarificationQuestion["state"], string> = {
  draft: "text-zinc-400 ring-white/10",
  shared: "text-sky-300 ring-sky-400/30",
  answered: "text-emerald-300 ring-emerald-400/30",
};

type Data = { questions: ClarificationQuestion[]; links: ClarificationLink[] };

export default function ClarificationsView({ projectId }: { projectId: string }) {
  const [result, setResult] = useState<{ data?: Data; error?: string } | null>(null);
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [working, setWorking] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedLink | null>(null);
  const { toasts, show, dismiss } = useToasts();

  useEffect(() => {
    let cancelled = false;
    Promise.all([listQuestions(projectId), listLinks(projectId)])
      .then(([questions, links]) => !cancelled && setResult({ data: { questions, links } }))
      .catch((err) => !cancelled && setResult({ error: errorText(err) }));
    return () => {
      cancelled = true;
    };
  }, [projectId, reload]);

  const data = result?.data;
  const groups = useMemo(() => {
    const byRequirement = new Map<number, { code: string; questions: ClarificationQuestion[] }>();
    for (const q of data?.questions ?? []) {
      const group = byRequirement.get(q.requirement_id) ?? { code: q.requirement_code, questions: [] };
      group.questions.push(q);
      byRequirement.set(q.requirement_id, group);
    }
    return [...byRequirement.entries()];
  }, [data]);

  // Answered questions cannot go into a new link
  const chosen = (data?.questions ?? []).filter((q) => selected.has(q.id) && q.state !== "answered");

  function toggle(id: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function act(label: string, action: () => Promise<void>) {
    if (working) return;
    setWorking(label);
    try {
      await action();
    } catch (err) {
      show("error", errorText(err));
    } finally {
      setWorking(null);
    }
  }

  const suggestAll = () =>
    act("generate", async () => {
      const result = await generateAllQuestions(projectId);
      const unclear = result.items.filter((i) => i.not_enough_information).map((i) => i.code);
      if (result.items.length === 0) show("info", "Every open finding that a question could resolve already has one.");
      else show("info", `${result.created} question${result.created === 1 ? "" : "s"} suggested. Review them before sharing.`);
      if (unclear.length) show("info", `Not enough information to write a good question for ${unclear.join(", ")}.`);
      if (result.error) show("error", `Stopped early: ${result.error}`);
      setReload((n) => n + 1);
    });

  const makeLink = () =>
    act("link", async () => {
      if (chosen.length > MAX_LINK_QUESTIONS) throw new Error(`Choose at most ${MAX_LINK_QUESTIONS} questions.`);
      setCreated(await createLink(projectId, chosen.map((q) => q.id)));
      setSelected(new Set());
      setReload((n) => n + 1);
    });

  const revoke = (link: ClarificationLink) =>
    act(`revoke-${link.id}`, async () => {
      await revokeLink(projectId, link.id);
      show("info", "Link revoked. The client can no longer open it.");
      setReload((n) => n + 1);
    });

  if (result?.error) return <div className="mt-6"><ErrorBanner message={`Could not load clarifications: ${result.error}`} /></div>;
  if (!data) return <p className="mt-6 text-sm text-zinc-500">Loading…</p>;

  return (
    <div className="mt-6 space-y-8">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void suggestAll()} disabled={working !== null} className={secondaryButton}>
          {working === "generate" ? "Writing questions…" : "Suggest questions for all"}
        </button>
        <button
          type="button"
          onClick={() => void makeLink()}
          disabled={working !== null || chosen.length === 0}
          className={primaryButton}
        >
          {working === "link" ? "Creating…" : `Create link${chosen.length ? ` (${chosen.length})` : ""}`}
        </button>
        <p className="text-xs text-zinc-500">
          Nothing is sent to the client automatically. Edit questions in the{" "}
          <Link href="/requirements" className="text-indigo-300 hover:underline">
            requirement panel
          </Link>
          .
        </p>
      </div>

      <section>
        <h2 className="text-sm font-medium uppercase tracking-wider text-zinc-500">Questions</h2>
        {groups.length === 0 ? (
          <p className="mt-3 rounded-2xl border border-dashed border-white/10 p-8 text-center text-sm text-zinc-500">
            No questions yet. Validate the requirements, then suggest questions for possibly vague, incomplete or
            conflicting ones.
          </p>
        ) : (
          <ul className="mt-3 space-y-4">
            {groups.map(([requirementId, group]) => (
              <li key={requirementId} className="rounded-xl border border-white/10 p-4">
                <p className="font-mono text-sm text-zinc-300">{group.code}</p>
                <ul className="mt-2 space-y-2">
                  {group.questions.map((q) => (
                    <li key={q.id} className="flex items-start gap-3">
                      <input
                        type="checkbox"
                        aria-label={`Choose: ${q.text}`}
                        checked={selected.has(q.id)}
                        disabled={q.state === "answered"}
                        onChange={() => toggle(q.id)}
                        className="mt-1 size-4 accent-indigo-500"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-zinc-100">{q.text}</p>
                        <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                          <span className={`rounded-full px-2 py-0.5 capitalize ring-1 ${STATE_STYLE[q.state]}`}>
                            {q.state}
                          </span>
                          <span>{q.origin === "user" ? "Yours" : q.status === "edited" ? "AI, edited" : "AI-suggested"}</span>
                          {q.finding_category && <span>· {CATEGORY_LABELS[q.finding_category]}</span>}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="text-sm font-medium uppercase tracking-wider text-zinc-500">Links</h2>
        {data.links.length === 0 ? (
          <p className="mt-3 text-sm text-zinc-500">No links yet. Choose questions above and create a link.</p>
        ) : (
          <div className="mt-3 overflow-x-auto rounded-xl border border-white/10">
            <table className="w-full text-left text-sm">
              <thead className="bg-white/5 text-xs uppercase tracking-wider text-zinc-500">
                <tr>
                  <th className="px-4 py-2 font-medium">Created</th>
                  <th className="px-4 py-2 font-medium">Questions</th>
                  <th className="px-4 py-2 font-medium">State</th>
                  <th className="px-4 py-2 font-medium">Expires</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {data.links.map((link) => (
                  <tr key={link.id}>
                    <td className="px-4 py-2 tabular-nums text-zinc-300">{formatTime(link.created_at)}</td>
                    <td className="px-4 py-2 text-zinc-300">
                      {link.questions.length}{" "}
                      <span className="text-xs text-zinc-500">
                        ({[...new Set(link.questions.map((q) => q.requirement_code))].join(", ")})
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-xs capitalize ring-1 ${LINK_STYLE[link.state]}`}>
                        {link.state}
                      </span>
                      {link.submitted_at && (
                        <span className="ml-2 text-xs text-zinc-500">{formatTime(link.submitted_at)}</span>
                      )}
                    </td>
                    <td className="px-4 py-2 tabular-nums text-zinc-400">{formatTime(link.expires_at)}</td>
                    <td className="px-4 py-2 text-right">
                      {link.state === "active" && (
                        <button
                          type="button"
                          onClick={() => void revoke(link)}
                          disabled={working !== null}
                          className="rounded px-2 py-1 text-xs text-rose-300 hover:bg-rose-500/10"
                        >
                          Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {created && <CreatedLinkDialog link={created} onClose={() => setCreated(null)} />}
      <ToastStack toasts={toasts} dismiss={dismiss} />
    </div>
  );
}

function CreatedLinkDialog({ link, onClose }: { link: CreatedLink; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Modal title="Link for the client" onClose={onClose}>
      <p className="mt-3 text-sm text-zinc-300">
        Copy this link now and share it with the client yourself. It is shown only once: just a fingerprint of it is
        stored. It works once and expires on {formatTime(link.expires_at)}.
      </p>
      <div className="mt-4 flex gap-2">
        <input
          readOnly
          aria-label="Link"
          value={link.url}
          onFocus={(e) => e.currentTarget.select()}
          className="min-w-0 flex-1 rounded-lg border border-white/10 bg-zinc-900 px-3 py-2 font-mono text-xs text-zinc-200"
        />
        <button
          type="button"
          className={primaryButton}
          onClick={() =>
            void navigator.clipboard.writeText(link.url).then(
              () => setCopied(true),
              () => setCopied(false),
            )
          }
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <p className="mt-3 text-xs text-zinc-500">
        {link.questions.length} question{link.questions.length === 1 ? "" : "s"}. Nothing was sent to the client.
      </p>
      <div className="mt-4 flex justify-end">
        <button type="button" onClick={onClose} className={secondaryButton}>
          Done
        </button>
      </div>
    </Modal>
  );
}
