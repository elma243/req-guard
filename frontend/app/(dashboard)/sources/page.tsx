"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import ConnectorCards from "@/components/ConnectorCards";
import NoProjectSelected from "@/components/NoProjectSelected";
import SourceBadge, { sourceLabel } from "@/components/SourceBadge";
import { ErrorBanner, errorText } from "@/components/ui";
import {
  deleteSource,
  getProject,
  listSources,
  updateProject,
  uploadFiles,
  type Source,
  type UploadResult,
} from "@/lib/api";
import { useSelectedProject } from "@/lib/selectedProject";

const ACCEPT = ".txt,.md,.eml,.json";
const MAX_FILES = 10;
// Reload the source list this often, so auto-sync results and times stay current
const REFRESH_MS = 30_000;

export default function SourcesPage() {
  const projectId = useSelectedProject();

  return (
    <div className="max-w-5xl">
      <h1 className="text-2xl font-semibold tracking-tight">Sources</h1>
      <p className="mt-1.5 text-sm text-zinc-400">
        Connect Jira, Slack, Gmail, Telegram or Discord, or upload chat exports, emails and meeting notes.
      </p>
      {/* useSearchParams needs a Suspense boundary */}
      <Suspense fallback={null}>
        <GmailResultBanner />
      </Suspense>
      {projectId ? <ProjectSources key={projectId} projectId={projectId} /> : <NoProjectSelected />}
    </div>
  );
}

// Shown after Google sends the browser back from the Gmail consent page (?gmail=connected|denied|error)
const GMAIL_RESULTS: Record<string, { text: string; style: string }> = {
  connected: {
    text: "Google account connected. You can sync the Gmail source now.",
    style: "border-emerald-500/20 bg-emerald-500/10 text-emerald-300",
  },
  denied: {
    text: "Google access was not granted, so the Gmail source cannot sync yet.",
    style: "border-amber-500/20 bg-amber-500/10 text-amber-300",
  },
  error: {
    text: "Could not connect the Google account. See the Gmail card for details and try again.",
    style: "border-rose-500/20 bg-rose-500/10 text-rose-300",
  },
};

function GmailResultBanner() {
  const result = GMAIL_RESULTS[useSearchParams().get("gmail") ?? ""];
  if (!result) return null;
  return <p className={`mt-4 rounded-lg border px-3.5 py-2.5 text-sm ${result.style}`}>{result.text}</p>;
}

function ProjectSources({ projectId }: { projectId: string }) {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [result, setResult] = useState<UploadResult | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function loadSources() {
    return listSources(projectId)
      .then(setSources)
      .catch((err) => setLoadError(errorText(err)));
  }

  useEffect(() => {
    loadSources();
    const timer = window.setInterval(loadSources, REFRESH_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per project (component is keyed by it)
  }, []);

  async function handleDelete(source: Source) {
    const count = source.message_count === 1 ? "1 message" : `${source.message_count} messages`;
    const ok = confirm(
      `Delete "${source.display_name}"?\n\nThis will permanently delete ${count}. This cannot be undone.`,
    );
    if (!ok) return;
    setDeletingId(source.id);
    setLoadError("");
    try {
      await deleteSource(projectId, source.id);
      setSources((list) => list?.filter((s) => s.id !== source.id) ?? null);
    } catch (err) {
      setLoadError(errorText(err));
    } finally {
      setDeletingId(null);
    }
  }

  async function handleFiles(fileList: FileList | null) {
    const files = Array.from(fileList ?? []);
    if (files.length === 0 || uploading) return;
    if (files.length > MAX_FILES) {
      setUploadError(`Upload at most ${MAX_FILES} files at once.`);
      return;
    }
    setUploading(true);
    setUploadError("");
    setResult(null);
    try {
      setResult(await uploadFiles(projectId, files));
      await loadSources();
    } catch (err) {
      setUploadError(errorText(err));
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <>
      <h2 className="mt-8 text-lg font-medium">Connect an API</h2>
      <AutoExtractToggle projectId={projectId} />
      <div className="mt-3">
        <ConnectorCards projectId={projectId} sources={sources} onChange={loadSources} />
      </div>

      <h2 className="mt-10 text-lg font-medium">Upload files</h2>
      <label
        htmlFor="upload-input"
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
        className={`mt-3 flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-12 text-center transition ${
          dragging
            ? "border-indigo-400 bg-indigo-500/10"
            : "border-white/10 bg-zinc-900/40 hover:border-white/20 hover:bg-zinc-900/60"
        } ${uploading ? "pointer-events-none opacity-60" : ""}`}
      >
        <UploadIcon />
        <p className="mt-3 text-sm font-medium text-zinc-200">
          {uploading ? "Importing..." : "Drop files here, or click to choose"}
        </p>
        <p className="mt-1.5 max-w-lg text-xs text-zinc-500">
          WhatsApp &ldquo;Export chat&rdquo; .txt · Messenger message_1.json · .eml emails · .txt/.md meeting notes.
          Up to {MAX_FILES} files, 10 MB each. Messages that were already imported are skipped.
        </p>
        <input
          ref={inputRef}
          id="upload-input"
          type="file"
          multiple
          accept={ACCEPT}
          className="sr-only"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </label>

      <div className="mt-4">
        <ErrorBanner message={uploadError} />
      </div>
      {result && <UploadResultCard result={result} />}

      <h2 className="mt-10 text-lg font-medium">All sources</h2>
      <ErrorBanner message={loadError} />
      <div className="mt-3 overflow-x-auto rounded-2xl border border-white/10">
        <table className="w-full text-left text-sm">
          <thead className="bg-zinc-900/80 text-xs uppercase tracking-wider text-zinc-500">
            <tr>
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium">Type</th>
              <th className="px-4 py-3 text-right font-medium">Messages</th>
              <th className="px-4 py-3 font-medium">Last import / sync</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {sources === null && !loadError && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-zinc-500">
                  Loading...
                </td>
              </tr>
            )}
            {sources?.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-zinc-500">
                  No sources yet.
                </td>
              </tr>
            )}
            {sources?.map((s) => (
              <tr key={s.id}>
                <td className="px-4 py-3 font-medium text-zinc-100">{s.display_name}</td>
                <td className="px-4 py-3">
                  <SourceBadge source={s.type} />
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-zinc-300">{s.message_count}</td>
                <td className="px-4 py-3 text-zinc-400">
                  {s.last_synced_at ? new Date(s.last_synced_at).toLocaleString() : "—"}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right">
                  <Link
                    href={`/messages?source=${encodeURIComponent(s.type)}`}
                    className="text-xs text-indigo-300 hover:text-indigo-200"
                  >
                    View messages
                  </Link>
                  <button
                    onClick={() => handleDelete(s)}
                    disabled={deletingId !== null}
                    className="ml-3 text-xs text-rose-300 hover:text-rose-200 disabled:opacity-50"
                  >
                    {deletingId === s.id ? "Deleting..." : "Delete"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// Project setting: extract (and validate) requirements from messages that auto-sync brings in
function AutoExtractToggle({ projectId }: { projectId: string }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getProject(projectId)
      .then((p) => setEnabled(p.auto_extract))
      .catch((err) => setError(errorText(err)));
  }, [projectId]);

  async function handleChange(value: boolean) {
    setSaving(true);
    setError("");
    try {
      setEnabled((await updateProject(projectId, { auto_extract: value })).auto_extract);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-2">
      <label className="inline-flex items-center gap-2 text-sm text-zinc-300">
        <input
          type="checkbox"
          checked={enabled ?? false}
          disabled={enabled === null || saving}
          onChange={(e) => handleChange(e.target.checked)}
          className="accent-indigo-500"
        />
        Auto-extract: find requirements in new auto-synced messages right away
      </label>
      <p className="mt-0.5 text-xs text-zinc-500">
        The AI suggestions still wait for your review on the Requirements page.
      </p>
      {error && <p className="mt-1 text-xs text-rose-300">{error}</p>}
    </div>
  );
}

function UploadResultCard({ result }: { result: UploadResult }) {
  return (
    <div className="mt-4 rounded-2xl border border-white/10 bg-zinc-900/60 p-5">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <Stat label="imported" value={result.imported} className="text-emerald-300" />
        <Stat label="duplicates" value={result.duplicates} className="text-amber-300" />
        <Stat label="skipped" value={result.skipped} className="text-zinc-300" />
        {result.imported > 0 && (
          <Link href="/messages" className="ml-auto text-sm text-indigo-300 hover:text-indigo-200">
            View messages →
          </Link>
        )}
      </div>
      <ul className="mt-4 divide-y divide-white/5 text-sm">
        {result.files.map((f, i) => (
          <li key={`${f.filename}-${i}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
            <span className="font-medium text-zinc-200">{f.filename}</span>
            {f.type && <span className="text-xs text-zinc-500">{sourceLabel(f.type)}</span>}
            {f.error ? (
              <span className="text-rose-300">{f.error}</span>
            ) : (
              <span className="ml-auto text-xs tabular-nums text-zinc-400">
                {f.imported} imported · {f.duplicates} duplicates · {f.skipped} skipped
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-zinc-500">
        Skipped = system lines, media placeholders, headings and other lines that are not client messages.
      </p>
    </div>
  );
}

function Stat({ label, value, className }: { label: string; value: number; className: string }) {
  return (
    <p className="text-sm text-zinc-400">
      <span className={`text-2xl font-semibold tabular-nums ${className}`}>{value}</span> {label}
    </p>
  );
}

function UploadIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} className="h-9 w-9 text-zinc-500">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 7.5 12 3m0 0L7.5 7.5M12 3v13.5"
      />
    </svg>
  );
}
