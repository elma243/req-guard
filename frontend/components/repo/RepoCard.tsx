"use client";

// GitHub repository of the project (Step 10): connect it by "owner/repo", analyse it, and see what the last analysis
// found. The GitHub token stays in the backend; the card only shows whether it is set.

import { useEffect, useState } from "react";
import { ErrorBanner, NoticeBanner, dangerButton, errorText, inputClass, primaryButton, secondaryButton } from "@/components/ui";
import { analyzeRepo, connectRepo, disconnectRepo, getRepo, type AnalysisResult, type RepoState } from "@/lib/api";

export function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return new Date(iso).toLocaleDateString();
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function ResultSummary({ result }: { result: AnalysisResult }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-zinc-300">
        {plural(result.new_commits, "new commit")}, {plural(result.pulls_updated, "pull request")} updated ·{" "}
        {plural(result.python_files, "Python file")}, {plural(result.test_functions, "test")} ·{" "}
        {plural(result.explicit_added, "ID link")} and {plural(result.suggested_added, "AI suggestion")} added
        {result.unchecked > 0 && ` (${result.unchecked} not checked by AI)`}
        {result.rate_limit_remaining !== null && (
          <span className="text-zinc-500"> · {result.rate_limit_remaining} GitHub requests left this hour</span>
        )}
      </p>
      {result.notices.map((n) => (
        <NoticeBanner key={n} message={n} />
      ))}
    </div>
  );
}

export default function RepoCard({ projectId, onAnalyzed }: { projectId: string; onAnalyzed?: () => void }) {
  const [state, setState] = useState<RepoState | null>(null);
  const [name, setName] = useState("");
  const [working, setWorking] = useState<"connect" | "analyze" | "disconnect" | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AnalysisResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRepo(projectId)
      .then((s) => {
        if (cancelled) return;
        setState(s);
        setName(s.repo?.full_name ?? "");
      })
      .catch((err) => !cancelled && setError(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  async function run(kind: "connect" | "analyze" | "disconnect", action: () => Promise<void>) {
    if (working) return;
    setWorking(kind);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setWorking(null);
    }
  }

  const connect = (e: React.FormEvent) => {
    e.preventDefault();
    void run("connect", async () => {
      const s = await connectRepo(projectId, name.trim());
      setState(s);
      setName(s.repo?.full_name ?? "");
      setResult(null);
    });
  };

  const analyze = () =>
    run("analyze", async () => {
      const r = await analyzeRepo(projectId);
      setResult(r);
      setState(await getRepo(projectId));
      onAnalyzed?.();
    });

  const disconnect = () => {
    if (!window.confirm("Disconnect the repository? Its analysis and all trace links are removed.")) return;
    void run("disconnect", async () => {
      await disconnectRepo(projectId);
      setState((s) => (s ? { ...s, repo: null } : s));
      setName("");
      setResult(null);
      onAnalyzed?.();
    });
  };

  if (!state) {
    return error ? <ErrorBanner message={error} /> : <p className="text-sm text-zinc-500">Loading repository…</p>;
  }
  const repo = state.repo;
  const shown = result ?? repo?.last_result ?? null;
  const busy = working !== null;

  return (
    <section className="space-y-3 rounded-xl border border-white/10 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium text-zinc-200">GitHub repository</h2>
        <span
          className={`rounded-full px-2 py-0.5 text-xs ring-1 ${
            state.token_configured
              ? "bg-emerald-400/10 text-emerald-300 ring-emerald-400/30"
              : "bg-white/5 text-zinc-400 ring-white/10"
          }`}
          title="GITHUB_TOKEN in backend/.env. The token never leaves the backend."
        >
          {state.token_configured ? "Token set" : "No token: public repos only, 60 requests/hour"}
        </span>
      </div>
      <form onSubmit={connect} className="flex flex-wrap gap-2">
        <input
          aria-label="Repository (owner/repo)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="owner/repo or https://github.com/owner/repo"
          maxLength={300}
          className={`${inputClass} min-w-64 flex-1`}
        />
        <button type="submit" disabled={busy || !name.trim()} className={secondaryButton}>
          {working === "connect" ? "Checking…" : repo ? "Change" : "Connect"}
        </button>
      </form>
      {repo && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => void analyze()} disabled={busy} className={primaryButton}>
            {working === "analyze" ? "Analysing…" : "Analyze"}
          </button>
          <p className="text-sm text-zinc-400">
            <a href={repo.url} target="_blank" rel="noreferrer" className="text-indigo-300 hover:underline">
              {repo.full_name}
            </a>{" "}
            · branch {repo.default_branch}
            {repo.private && " · private"} · last analysed {timeAgo(repo.last_analyzed_at)}
            {repo.head_sha && <span className="font-mono"> · {repo.head_sha.slice(0, 7)}</span>}
          </p>
          <button type="button" onClick={disconnect} disabled={busy} className={`${dangerButton} ml-auto py-1 text-xs`}>
            {working === "disconnect" ? "Removing…" : "Disconnect"}
          </button>
        </div>
      )}
      {working === "analyze" && (
        <p className="text-xs text-zinc-500">
          Reading new commits, pull requests and the code, then finding trace links. This can take a minute.
        </p>
      )}
      <ErrorBanner message={error} />
      {shown && <ResultSummary result={shown} />}
    </section>
  );
}
