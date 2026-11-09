"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import NoProjectSelected from "@/components/NoProjectSelected";
import SourceBadge, { sourceLabel } from "@/components/SourceBadge";
import VoiceNote from "@/components/VoiceNote";
import { ErrorBanner, NoticeBanner, errorText, inputClass, primaryButton, secondaryButton } from "@/components/ui";
import {
  MESSAGE_SOURCES,
  SIMILARITY_UNAVAILABLE,
  deleteMessage,
  extractRequirements,
  listMessages,
  type ExtractResult,
  type Message,
  type MessageFilters,
  type MessagePage,
} from "@/lib/api";
import { markMessagesSeenNow } from "@/lib/newMessages";
import { useSelectedProject } from "@/lib/selectedProject";

const PAGE_SIZE = 50;
const SEARCH_DELAY_MS = 350;
// While voice notes on the page wait for transcription, the page is refreshed this often
const VOICE_REFRESH_MS = 5000;
// Filters kept in the URL, so a filtered view can be reloaded or shared
const FILTER_KEYS = ["source", "author", "q", "date_from", "date_to"] as const;

export default function MessagesPage() {
  return (
    <div className="max-w-6xl">
      <h1 className="text-2xl font-semibold tracking-tight">Messages</h1>
      <p className="mt-1.5 text-sm text-zinc-400">All imported client messages of the selected project.</p>
      {/* useSearchParams needs a Suspense boundary */}
      <Suspense fallback={<p className="mt-8 text-sm text-zinc-500">Loading...</p>}>
        <MessagesForSelectedProject />
      </Suspense>
    </div>
  );
}

function MessagesForSelectedProject() {
  const projectId = useSelectedProject();
  return projectId ? <MessagesView key={projectId} projectId={projectId} /> : <NoProjectSelected />;
}

function MessagesView({ projectId }: { projectId: string }) {
  // Opening the page clears the "new messages" badge in the menu
  useEffect(() => {
    markMessagesSeenNow(projectId).catch(() => undefined);
  }, [projectId]);

  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const filters: MessageFilters = {
    ...Object.fromEntries(FILTER_KEYS.map((k) => [k, searchParams.get(k) ?? ""])),
    page: Math.max(1, Number(searchParams.get("page")) || 1),
    page_size: PAGE_SIZE,
  };
  // Bumping `reload` fetches the current page again (after a delete)
  const [reload, setReload] = useState(0);
  const queryKey = `${projectId}?${searchParams.toString()}#${reload}`;

  // Text boxes update the URL after a short pause in typing
  const [authorText, setAuthorText] = useState(filters.author ?? "");
  const [searchText, setSearchText] = useState(filters.q ?? "");

  const [result, setResult] = useState<{ key: string; data?: MessagePage; error?: string } | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState("");

  function updateUrl(changes: Record<string, string | number>) {
    const params = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(changes)) {
      if (value === "" || value === 1) params.delete(key);
      else params.set(key, String(value));
    }
    if (!("page" in changes)) params.delete("page"); // a new filter starts at page 1
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }

  useEffect(() => {
    const timer = setTimeout(() => {
      const author = authorText.trim();
      const q = searchText.trim();
      if (author !== (searchParams.get("author") ?? "") || q !== (searchParams.get("q") ?? "")) {
        updateUrl({ author, q });
      }
    }, SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to typing
  }, [authorText, searchText]);

  useEffect(() => {
    let cancelled = false;
    listMessages(projectId, filters)
      .then((data) => !cancelled && setResult({ key: queryKey, data }))
      .catch((err) => !cancelled && setResult({ key: queryKey, error: errorText(err) }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- queryKey covers projectId and all filters
  }, [queryKey]);

  const loading = result?.key !== queryKey;
  const data = result?.data;
  const waitingForVoice = !!data?.items.some(
    (m) => m.transcript_status === "pending" || m.transcript_status === "transcribing",
  );

  // Quietly fetch the page again while voice notes are being transcribed, so transcripts appear on their own
  useEffect(() => {
    if (!waitingForVoice || loading) return;
    const timer = setTimeout(() => {
      listMessages(projectId, filters)
        .then((fresh) => setResult((prev) => (prev?.key === queryKey ? { key: queryKey, data: fresh } : prev)))
        .catch(() => undefined);
    }, VOICE_REFRESH_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- queryKey covers projectId and all filters
  }, [waitingForVoice, loading, queryKey, data]);
  const hasFilters = FILTER_KEYS.some((k) => searchParams.get(k));

  function clearFilters() {
    setAuthorText("");
    setSearchText("");
    router.replace(pathname, { scroll: false });
  }

  async function handleDelete(m: Message) {
    const text = m.kind === "voice" && !m.text ? `Voice note ${m.voice?.filename ?? ""}`.trim() : m.text;
    const preview = text.length > 80 ? `${text.slice(0, 80)}...` : text;
    if (!confirm(`Delete this message permanently? This cannot be undone.\n\n"${preview}"`)) return;
    setDeletingId(m.id);
    setDeleteError("");
    try {
      await deleteMessage(projectId, m.id);
      // Last message on a later page: go back one page, otherwise reload this one
      if (data && data.items.length === 1 && data.page > 1) updateUrl({ page: data.page - 1 });
      else setReload((n) => n + 1);
    } catch (err) {
      setDeleteError(errorText(err));
    } finally {
      setDeletingId(null);
    }
  }

  function toggle(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <>
      <ExtractRequirements projectId={projectId} />

      <div className="mt-6 grid gap-3 rounded-2xl border border-white/10 bg-zinc-900/60 p-4 sm:grid-cols-2 lg:grid-cols-[10rem_1fr_1fr_9.5rem_9.5rem_auto]">
        <FilterField label="Source" htmlFor="f-source">
          <select
            id="f-source"
            value={filters.source}
            onChange={(e) => updateUrl({ source: e.target.value })}
            className={inputClass}
          >
            <option value="">All sources</option>
            {MESSAGE_SOURCES.map((s) => (
              <option key={s} value={s}>
                {sourceLabel(s)}
              </option>
            ))}
          </select>
        </FilterField>
        <FilterField label="Author" htmlFor="f-author">
          <input
            id="f-author"
            value={authorText}
            onChange={(e) => setAuthorText(e.target.value)}
            placeholder="Any author"
            maxLength={200}
            className={inputClass}
          />
        </FilterField>
        <FilterField label="Search text" htmlFor="f-q">
          <input
            id="f-q"
            type="search"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            placeholder="e.g. delivery"
            maxLength={200}
            className={inputClass}
          />
        </FilterField>
        <FilterField label="From" htmlFor="f-from">
          <input
            id="f-from"
            type="date"
            value={filters.date_from}
            onChange={(e) => updateUrl({ date_from: e.target.value })}
            className={`${inputClass} [color-scheme:dark]`}
          />
        </FilterField>
        <FilterField label="To" htmlFor="f-to">
          <input
            id="f-to"
            type="date"
            value={filters.date_to}
            onChange={(e) => updateUrl({ date_to: e.target.value })}
            className={`${inputClass} [color-scheme:dark]`}
          />
        </FilterField>
        <div className="flex items-end">
          <button onClick={clearFilters} disabled={!hasFilters} className={`${secondaryButton} w-full disabled:opacity-40`}>
            Clear
          </button>
        </div>
      </div>

      {(result?.error || deleteError) && (
        <div className="mt-4">
          <ErrorBanner message={deleteError || result?.error || ""} />
        </div>
      )}

      <div
        className={`mt-6 overflow-x-auto rounded-2xl border border-white/10 transition-opacity ${loading && data ? "opacity-60" : ""}`}
      >
        <table className="w-full text-left text-sm">
          <thead className="bg-zinc-900/80 text-xs uppercase tracking-wider text-zinc-500">
            <tr>
              <th className="w-44 px-4 py-3 font-medium">Time</th>
              <th className="w-40 px-4 py-3 font-medium">Source</th>
              <th className="w-40 px-4 py-3 font-medium">Author</th>
              <th className="px-4 py-3 font-medium">Text</th>
              <th className="w-12 px-2 py-3">
                <span className="sr-only">Delete</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {!data && loading && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-zinc-500">
                  Loading messages...
                </td>
              </tr>
            )}
            {data?.items.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-zinc-500">
                  {hasFilters ? (
                    "No messages match these filters."
                  ) : (
                    <>
                      No messages yet.{" "}
                      <Link href="/sources" className="text-indigo-300 hover:text-indigo-200">
                        Upload chat exports or files
                      </Link>{" "}
                      on the Sources page.
                    </>
                  )}
                </td>
              </tr>
            )}
            {data?.items.map((m) => {
              const open = expanded.has(m.id);
              return (
                <tr key={m.id} className="align-top">
                  <td className="whitespace-nowrap px-4 py-3 text-zinc-400 tabular-nums">
                    {new Date(m.sent_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
                  </td>
                  <td className="px-4 py-3">
                    <SourceBadge source={m.source} />
                    {m.channel && (
                      <p className="mt-1 max-w-[9rem] truncate text-xs text-zinc-500" title={m.channel}>
                        {m.channel}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3 text-zinc-200">{m.author || <span className="text-zinc-600">—</span>}</td>
                  <td className="px-4 py-3">
                    {m.kind === "voice" ? (
                      <VoiceNote
                        projectId={projectId}
                        message={m}
                        transcript={<ExpandableText text={m.text} open={open} onToggle={() => toggle(m.id)} />}
                        onRetried={() => setReload((n) => n + 1)}
                      />
                    ) : (
                      <ExpandableText text={m.text} open={open} onToggle={() => toggle(m.id)} />
                    )}
                  </td>
                  <td className="px-2 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => handleDelete(m)}
                      disabled={deletingId !== null}
                      title="Delete message"
                      aria-label="Delete message"
                      className="rounded-md p-1.5 text-zinc-500 transition hover:bg-rose-500/10 hover:text-rose-300 disabled:opacity-40"
                    >
                      <TrashIcon />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {data && data.total > 0 && (
        <Pagination
          page={data.page}
          pageSize={data.page_size}
          total={data.total}
          onPage={(page) => updateUrl({ page })}
        />
      )}
    </>
  );
}

function ExpandableText({ text, open, onToggle }: { text: string; open: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      title={open ? "Click to collapse" : "Click to show all"}
      className={`w-full cursor-pointer whitespace-pre-line text-left text-zinc-100 ${open ? "" : "line-clamp-3"}`}
    >
      {text}
    </button>
  );
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// Runs AI requirement extraction on messages not read yet (or all again, when asked)
function ExtractRequirements({ projectId }: { projectId: string }) {
  const [running, setRunning] = useState(false);
  const [includeProcessed, setIncludeProcessed] = useState(false);
  const [result, setResult] = useState<ExtractResult | null>(null);
  const [error, setError] = useState("");

  async function run() {
    if (
      includeProcessed &&
      !confirm(
        "Read all messages again?\n\nAI suggestions that nobody has reviewed yet are replaced. " +
          "Accepted, edited, rejected and merged requirements are kept.",
      )
    )
      return;
    setRunning(true);
    setError("");
    setResult(null);
    try {
      setResult(await extractRequirements(projectId, { force: includeProcessed }));
      setIncludeProcessed(false);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="mt-6 rounded-2xl border border-white/10 bg-zinc-900/60 p-4">
      <div className="flex flex-wrap items-center gap-4">
        <button onClick={run} disabled={running} className={primaryButton}>
          {running ? "Extracting..." : "Extract requirements"}
        </button>
        <label className="flex items-center gap-2 text-sm text-zinc-400">
          <input
            type="checkbox"
            checked={includeProcessed}
            onChange={(e) => setIncludeProcessed(e.target.checked)}
            disabled={running}
            className="accent-indigo-500"
          />
          Include already processed messages
        </label>
        <p className="text-xs text-zinc-500 lg:ml-auto">
          {running
            ? "The AI is reading the messages. This can take a minute..."
            : "The AI reads new messages and suggests requirements for you to review."}
        </p>
      </div>

      {error && (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      )}
      {result && (
        <div className="mt-3 space-y-2">
          {result.notice && <NoticeBanner message={result.notice} detail={result.ai_error} />}
          {result.similarity_unavailable_reason && (
            <NoticeBanner
              message={SIMILARITY_UNAVAILABLE}
              detail={`${result.similarity_unavailable_reason} Duplicates, conflicts and change requests were not looked for.`}
            />
          )}
          <p className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3.5 py-2.5 text-sm text-emerald-200">
            {result.messages_processed === 0 ? (
              "No new messages to read. Tick “Include already processed messages” to read them again."
            ) : (
              <>
                Read {plural(result.messages_processed, "message")}
                {result.messages_small_talk > 0 && ` (${result.messages_small_talk} only small talk)`},{" "}
                found {plural(result.requirements_created, "new requirement")}.
                {result.messages_remaining > 0 &&
                  ` ${plural(result.messages_remaining, "message")} still waiting: click Extract again.`}{" "}
                <Link href="/requirements" className="font-medium text-emerald-100 underline-offset-2 hover:underline">
                  View requirements →
                </Link>
              </>
            )}
          </p>
        </div>
      )}
    </div>
  );
}

function FilterField({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-xs font-medium text-zinc-400">
        {label}
      </label>
      {children}
    </div>
  );
}

function Pagination(props: { page: number; pageSize: number; total: number; onPage: (page: number) => void }) {
  const { page, pageSize, total, onPage } = props;
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);
  const lastPage = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="mt-4 flex items-center justify-between text-sm text-zinc-400">
      <p className="tabular-nums">
        {first <= total ? `${first}–${last}` : "0"} of {total}
      </p>
      <div className="flex items-center gap-2">
        <button onClick={() => onPage(page - 1)} disabled={page <= 1} className={`${secondaryButton} disabled:opacity-40`}>
          ← Previous
        </button>
        <span className="px-2 tabular-nums">
          Page {page} of {lastPage}
        </span>
        <button
          onClick={() => onPage(page + 1)}
          disabled={page >= lastPage}
          className={`${secondaryButton} disabled:opacity-40`}
        >
          Next →
        </button>
      </div>
    </div>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} className="h-4 w-4" aria-hidden>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
      />
    </svg>
  );
}
