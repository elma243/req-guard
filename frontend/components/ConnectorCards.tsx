"use client";

// "Connect" cards for the API connectors (Jira, Slack, Gmail, Telegram, Discord) on the Sources page.
// A connector whose token is missing from backend/.env shows "Not configured" with the variable names.
// Tokens never reach the browser: the backend only says which variables are missing.
// Each connected source has a Sync button and auto-sync settings (on/off, interval, last and next run).

import { useEffect, useState } from "react";
import TagInput from "@/components/TagInput";
import { ErrorBanner, Label, errorText, inputClass, primaryButton, secondaryButton } from "@/components/ui";
import {
  SYNC_INTERVALS,
  createSource,
  gmailAuthorizeUrl,
  listConnectors,
  setAutoSync,
  syncSource,
  type Connector,
  type Source,
  type SyncInterval,
  type SyncResult,
} from "@/lib/api";
import { timeAgo, timeUntil } from "@/lib/time";

// How often the relative times ("2 min ago") are refreshed
const CLOCK_MS = 30_000;

const smallPrimary =
  "rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-60";
const smallSecondary =
  "rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 transition hover:bg-white/5 hover:text-zinc-100";

type Field = {
  key: string;
  label: string;
  kind: "text" | "tags";
  placeholder: string;
  required?: boolean;
  help?: string;
};

const DESCRIPTIONS: Record<string, string> = {
  jira: "Issues and comments of one Jira project.",
  slack: "Messages and thread replies from selected channels.",
  gmail: "Email threads that match a Gmail search.",
  telegram:
    "Messages from groups the bot was added to. Telegram keeps them about 24 hours, so auto-sync is on by default.",
  discord: "Messages from the text channels of a server.",
};

const FIELDS: Record<string, Field[]> = {
  jira: [{ key: "project_key", label: "Project key", kind: "text", placeholder: "ACME", required: true }],
  slack: [
    {
      key: "channels",
      label: "Channels",
      kind: "tags",
      placeholder: "#client-acme",
      required: true,
      help: "Public channels. Invite the bot to each one.",
    },
  ],
  gmail: [
    {
      key: "query",
      label: "Gmail search",
      kind: "text",
      placeholder: "from:client@acme.com",
      required: true,
      help: "Same syntax as the Gmail search box.",
    },
  ],
  telegram: [
    {
      key: "chat_ids",
      label: "Chat ids (optional)",
      kind: "tags",
      placeholder: "-1001234567890",
      help: "Leave empty to import every group the bot is in.",
    },
  ],
  discord: [
    { key: "guild_id", label: "Server id", kind: "text", placeholder: "1280000000000000000", required: true },
    {
      key: "channels",
      label: "Channels (optional)",
      kind: "tags",
      placeholder: "#client-acme",
      help: "Leave empty for every text channel the bot can read.",
    },
  ],
};

type Props = {
  projectId: string;
  sources: Source[] | null;
  onChange: () => Promise<unknown>;
};

export default function ConnectorCards({ projectId, sources, onChange }: Props) {
  const [connectors, setConnectors] = useState<Connector[] | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    listConnectors()
      .then(setConnectors)
      .catch((err) => setError(errorText(err)));
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), CLOCK_MS);
    return () => window.clearInterval(timer);
  }, []);

  if (error) return <ErrorBanner message={error} />;
  if (!connectors) return <p className="text-sm text-zinc-500">Loading connectors...</p>;

  return (
    <div className="grid gap-4 md:grid-cols-2">
      {connectors.map((c) => (
        <ConnectorCard
          key={c.type}
          connector={c}
          projectId={projectId}
          sources={(sources ?? []).filter((s) => s.type === c.type)}
          now={now}
          onChange={onChange}
        />
      ))}
    </div>
  );
}

function ConnectorCard({
  connector,
  projectId,
  sources,
  now,
  onChange,
}: {
  connector: Connector;
  projectId: string;
  sources: Source[];
  now: number;
  onChange: () => Promise<unknown>;
}) {
  const [adding, setAdding] = useState(false);

  return (
    <section
      className={`rounded-2xl border p-5 ${
        connector.configured ? "border-white/10 bg-zinc-900/60" : "border-white/5 bg-zinc-900/30"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-medium text-zinc-100">{connector.label}</h3>
          <p className="mt-0.5 text-xs text-zinc-500">{DESCRIPTIONS[connector.type]}</p>
        </div>
        {connector.configured ? (
          !adding && (
            <button onClick={() => setAdding(true)} className={`${smallSecondary} shrink-0`}>
              Connect
            </button>
          )
        ) : (
          <span className="shrink-0 rounded-full bg-white/5 px-2 py-0.5 text-xs font-medium text-zinc-400 ring-1 ring-white/10">
            Not configured
          </span>
        )}
      </div>

      {!connector.configured && (
        <p className="mt-3 text-xs leading-relaxed text-zinc-500">
          Add{" "}
          {connector.missing_env.map((name, i) => (
            <span key={name}>
              {i > 0 && ", "}
              <code className="rounded bg-white/5 px-1 py-0.5 text-zinc-300">{name}</code>
            </span>
          ))}{" "}
          to <code className="rounded bg-white/5 px-1 py-0.5 text-zinc-300">backend/.env</code> and restart the
          backend.
        </p>
      )}

      {connector.configured && adding && (
        <AddSourceForm
          connector={connector}
          projectId={projectId}
          onCancel={() => setAdding(false)}
          onCreated={async () => {
            setAdding(false);
            await onChange();
          }}
        />
      )}

      {sources.length > 0 && (
        <ul className="mt-4 divide-y divide-white/5 border-t border-white/5">
          {sources.map((s) => (
            <SourceRow
              key={s.id}
              source={s}
              projectId={projectId}
              configured={connector.configured}
              now={now}
              onChange={onChange}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function AddSourceForm({
  connector,
  projectId,
  onCancel,
  onCreated,
}: {
  connector: Connector;
  projectId: string;
  onCancel: () => void;
  onCreated: () => Promise<void>;
}) {
  const fields = FIELDS[connector.type] ?? [];
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string | string[]>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      await createSource(projectId, { type: connector.type, display_name: name, settings: values });
      await onCreated();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  const missingRequired = fields.some((f) => f.required && !(values[f.key]?.length ?? 0));

  return (
    <form onSubmit={handleSubmit} className="mt-4 space-y-3">
      <div>
        <Label htmlFor={`${connector.type}-name`}>Name</Label>
        <input
          id={`${connector.type}-name`}
          required
          maxLength={200}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={`Acme ${connector.label}`}
          className={inputClass}
        />
      </div>
      {fields.map((f) => {
        const id = `${connector.type}-${f.key}`;
        return (
          <div key={f.key}>
            <Label htmlFor={id}>{f.label}</Label>
            {f.kind === "tags" ? (
              <TagInput
                id={id}
                value={(values[f.key] as string[] | undefined) ?? []}
                onChange={(tags) => setValues((v) => ({ ...v, [f.key]: tags }))}
                placeholder={f.placeholder}
              />
            ) : (
              <input
                id={id}
                required={f.required}
                value={(values[f.key] as string | undefined) ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                placeholder={f.placeholder}
                className={inputClass}
              />
            )}
            {f.help && <p className="mt-1 text-xs text-zinc-500">{f.help}</p>}
          </div>
        );
      })}
      <ErrorBanner message={error} />
      <div className="flex gap-2">
        <button type="submit" disabled={saving || missingRequired} className={primaryButton}>
          {saving ? "Saving..." : "Save"}
        </button>
        <button type="button" onClick={onCancel} className={secondaryButton}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function SourceRow({
  source,
  projectId,
  configured,
  now,
  onChange,
}: {
  source: Source;
  projectId: string;
  configured: boolean;
  now: number;
  onChange: () => Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<SyncResult | null>(null);
  const [error, setError] = useState("");

  async function handleSync() {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(await syncSource(projectId, source.id));
    } catch (err) {
      setError(errorText(err));
    } finally {
      // The source row (status, last sync, message count) comes from the reloaded list
      await onChange();
      setBusy(false);
    }
  }

  async function handleConnectGoogle() {
    setBusy(true);
    setError("");
    try {
      const { url } = await gmailAuthorizeUrl(projectId, source.id);
      window.location.assign(url);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  async function handleAutoSync(data: { enabled?: boolean; interval_minutes?: SyncInterval }) {
    setSaving(true);
    setError("");
    try {
      await setAutoSync(projectId, source.id, data);
    } catch (err) {
      setError(errorText(err));
    } finally {
      await onChange();
      setSaving(false);
    }
  }

  // A Gmail source that synced (or failed) before and lost its Google access: expired or revoked token
  const reconnect = source.needs_auth && Boolean(source.last_synced_at || source.sync_error);
  const autoRunning =
    source.auto_sync && source.auto_sync_available && !source.auto_sync_paused && !source.needs_auth;
  const syncing = busy || source.syncing;
  // The latest error comes from this click, else from the last sync stored on the source
  const shownError = error || (result ? "" : source.sync_error ?? "");
  // Set by the connector, e.g. Slack without files:read: "Add files:read to read voice notes"
  const state = (source.config?.state ?? {}) as { voice_notice?: unknown };
  const voiceNotice = typeof state.voice_notice === "string" ? state.voice_notice : "";

  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-sm font-medium text-zinc-200">{source.display_name}</span>
        <span className="text-xs text-zinc-500">
          {source.message_count} messages · last sync{" "}
          {source.last_synced_at ? (
            <time dateTime={source.last_synced_at} title={new Date(source.last_synced_at).toLocaleString()}>
              {timeAgo(source.last_synced_at, now)}
            </time>
          ) : (
            "never"
          )}
          {autoRunning && source.next_sync_at && <> · next run {timeUntil(source.next_sync_at, now)}</>}
        </span>
        <span className="ml-auto">
          {source.needs_auth ? (
            <button onClick={handleConnectGoogle} disabled={busy || !configured} className={smallPrimary}>
              {busy ? "Opening Google..." : reconnect ? "Reconnect Google account" : "Connect Google account"}
            </button>
          ) : (
            <button onClick={handleSync} disabled={syncing || !configured} className={smallPrimary}>
              {syncing ? "Syncing..." : "Sync"}
            </button>
          )}
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-400">
        <label className="inline-flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={source.auto_sync}
            disabled={saving}
            onChange={(e) => handleAutoSync({ enabled: e.target.checked })}
            className="accent-indigo-500"
          />
          Auto-sync
        </label>
        <label className="inline-flex items-center gap-1.5">
          every
          <select
            value={source.sync_interval_minutes}
            disabled={saving || !source.auto_sync}
            onChange={(e) => handleAutoSync({ interval_minutes: Number(e.target.value) as SyncInterval })}
            className="rounded-md border border-white/10 bg-zinc-950/60 px-1.5 py-0.5 text-xs text-zinc-200 outline-none focus:border-indigo-400 disabled:opacity-50"
          >
            {SYNC_INTERVALS.map((m) => (
              <option key={m} value={m}>
                {m === 60 ? "1 hour" : `${m} min`}
              </option>
            ))}
          </select>
        </label>
        {source.auto_sync && !source.auto_sync_available && (
          <span className="text-amber-300">Auto-sync is turned off on this server.</span>
        )}
      </div>

      {source.auto_sync && source.auto_sync_paused && (
        <p className="mt-1.5 text-xs text-amber-300">
          Paused after errors –{" "}
          <button
            onClick={handleSync}
            disabled={syncing || !configured}
            className="font-medium underline underline-offset-2 hover:text-amber-200 disabled:opacity-60"
          >
            {syncing ? "Retrying..." : "Retry"}
          </button>
        </p>
      )}
      {source.auto_sync && reconnect && (
        <p className="mt-1.5 text-xs text-amber-300">
          Auto-sync stopped: Google access expired. Reconnect the Google account to continue.
        </p>
      )}
      {result && (
        <p className="mt-1.5 text-xs text-emerald-300">
          {result.imported} new · {result.duplicates} already imported
          {result.voice_queued > 0 && ` · 🎤 ${result.voice_queued} voice notes to transcribe`}
        </p>
      )}
      {voiceNotice && (
        <p className="mt-1.5 text-xs text-amber-300">
          <span aria-hidden>🎤</span> {voiceNotice}
          {source.type === "slack" && " (Slack app → OAuth & Permissions → Bot Token Scopes, then reinstall the app)."}
        </p>
      )}
      {shownError && <p className="mt-1.5 text-xs text-rose-300">{shownError}</p>}
    </li>
  );
}
