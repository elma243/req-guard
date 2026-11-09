// Small coloured label for a message source (whatsapp, email, ...).

export const SOURCE_LABELS: Record<string, string> = {
  whatsapp: "WhatsApp",
  messenger: "Messenger",
  email: "Email",
  meeting_notes: "Meeting notes",
  voice_upload: "Voice notes",
  jira: "Jira",
  slack: "Slack",
  gmail: "Gmail",
  telegram: "Telegram",
  discord: "Discord",
};

const SOURCE_STYLE: Record<string, string> = {
  whatsapp: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/30",
  messenger: "bg-sky-400/10 text-sky-300 ring-sky-400/30",
  email: "bg-amber-400/10 text-amber-300 ring-amber-400/30",
  meeting_notes: "bg-fuchsia-400/10 text-fuchsia-300 ring-fuchsia-400/30",
  voice_upload: "bg-orange-400/10 text-orange-300 ring-orange-400/30",
  jira: "bg-blue-400/10 text-blue-300 ring-blue-400/30",
  slack: "bg-purple-400/10 text-purple-300 ring-purple-400/30",
  gmail: "bg-red-400/10 text-red-300 ring-red-400/30",
  telegram: "bg-cyan-400/10 text-cyan-300 ring-cyan-400/30",
  discord: "bg-indigo-400/10 text-indigo-300 ring-indigo-400/30",
};

export function sourceLabel(source: string) {
  return SOURCE_LABELS[source] ?? source;
}

export default function SourceBadge({ source }: { source: string }) {
  const style = SOURCE_STYLE[source] ?? "bg-white/5 text-zinc-300 ring-white/10";
  return (
    <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${style}`}>
      {sourceLabel(source)}
    </span>
  );
}
