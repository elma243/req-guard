"use client";

// Repository page (Step 10): the repo card plus what the last analysis stored. Read only; links open on GitHub.

import { useEffect, useState } from "react";
import { ApiError, getRepoSnapshot, type RepoSnapshot } from "@/lib/api";
import { ErrorBanner, errorText } from "@/components/ui";
import RepoCard, { timeAgo } from "./RepoCard";

const LINK = "text-indigo-300 hover:underline";

export default function RepositoryView({ projectId }: { projectId: string }) {
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState<{ data?: RepoSnapshot | null; error?: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRepoSnapshot(projectId)
      .then((data) => !cancelled && setResult({ data }))
      .catch((err) => {
        if (cancelled) return;
        // 409: no repository connected yet; the card above says what to do
        setResult(err instanceof ApiError && err.status === 409 ? { data: null } : { error: errorText(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, reload]);

  const snapshot = result?.data;
  return (
    <div className="mt-6 space-y-6">
      <RepoCard projectId={projectId} onAnalyzed={() => setReload((n) => n + 1)} />
      {result?.error && <ErrorBanner message={`Could not load the analysis: ${result.error}`} />}
      {snapshot && !snapshot.commit_sha && (
        <p className="text-sm text-zinc-500">Not analysed yet. Press Analyze above.</p>
      )}
      {snapshot?.commit_sha && (
        <>
          <p className="text-sm text-zinc-400">
            At commit <span className="font-mono">{snapshot.commit_sha.slice(0, 7)}</span>: {snapshot.file_count} files,{" "}
            {snapshot.python_files} Python files, {snapshot.test_functions} tests.
            {snapshot.truncated && " Some files were skipped (installed packages, build folders or very large files)."}
          </p>
          <div className="grid gap-6 lg:grid-cols-2">
            <section>
              <h2 className="mb-2 text-sm font-medium text-zinc-200">Python files</h2>
              <ul className="max-h-96 divide-y divide-white/5 overflow-y-auto rounded-xl border border-white/10 text-sm">
                {snapshot.python.map((f) => (
                  <li key={f.path} className="flex items-center gap-2 px-3 py-1.5">
                    <a href={f.url} target="_blank" rel="noreferrer" className={`${LINK} break-all font-mono text-xs`}>
                      {f.path}
                    </a>
                    {f.kind === "test" && (
                      <span className="rounded-full bg-sky-400/10 px-2 text-xs text-sky-300">
                        {f.tests} test{f.tests === 1 ? "" : "s"}
                      </span>
                    )}
                    {f.parse_error && (
                      <span className="text-xs text-amber-300" title={f.parse_error}>
                        could not parse
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
            <section>
              <h2 className="mb-2 text-sm font-medium text-zinc-200">Pull requests</h2>
              {snapshot.pulls.length === 0 ? (
                <p className="text-sm text-zinc-500">None.</p>
              ) : (
                <ul className="space-y-1.5 text-sm">
                  {snapshot.pulls.map((p) => (
                    <li key={p.number} className="flex flex-wrap items-center gap-2">
                      <a href={p.url} target="_blank" rel="noreferrer" className={LINK}>
                        #{p.number}
                      </a>
                      <span className="text-zinc-300">{p.title}</span>
                      <span
                        className={`rounded-full px-2 text-xs ${
                          p.merged ? "bg-violet-400/10 text-violet-300" : "bg-white/5 text-zinc-400"
                        }`}
                      >
                        {p.merged ? "merged" : p.state}
                      </span>
                      <span className="font-mono text-xs text-zinc-500">{p.head_ref}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
          <section>
            <h2 className="mb-2 text-sm font-medium text-zinc-200">Recent commits</h2>
            <ul className="divide-y divide-white/5 rounded-xl border border-white/10 text-sm">
              {snapshot.commits.map((c) => (
                <li key={c.sha} className="px-3 py-2">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <a href={c.url} target="_blank" rel="noreferrer" className={`${LINK} font-mono text-xs`}>
                      {c.sha.slice(0, 7)}
                    </a>
                    <span className="text-zinc-200">{c.title}</span>
                    <span className="text-xs text-zinc-500">
                      {c.author} · {timeAgo(c.committed_at)}
                    </span>
                  </div>
                  <p className="mt-0.5 break-all text-xs text-zinc-500">
                    {c.files_fetched ? c.files.join(", ") || "No files" : "Changed files not read yet"}
                  </p>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
