"use client";

// A voice note: the 🎤 badge, its transcript or why there is none, Retry for failed ones, and a player when the
// audio was kept (KEEP_AUDIO=true on the server, or not transcribed yet).

import { useState } from "react";
import { errorText } from "@/components/ui";
import {
  messageAudioUrl,
  retryTranscription,
  type Message,
  type TranscriptStatus,
  type VoiceInfo,
} from "@/lib/api";

function duration(seconds: number | null | undefined) {
  if (seconds == null) return "";
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function VoiceBadge({ voice }: { voice?: VoiceInfo | null }) {
  const length = duration(voice?.duration);
  return (
    <span
      className="inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-orange-400/10 px-2 py-0.5 text-xs font-medium text-orange-300 ring-1 ring-orange-400/30"
      title={voice?.filename ?? "Voice note"}
    >
      <span aria-hidden>🎤</span> Voice{length && ` · ${length}`}
    </span>
  );
}

export function VoicePlayer({ projectId, messageId }: { projectId: string; messageId: number }) {
  // No captions track: the transcript is shown next to the player
  return (
    <audio controls preload="none" src={messageAudioUrl(projectId, messageId)} className="mt-2 h-8 w-full max-w-xs" />
  );
}

const WAITING: Partial<Record<TranscriptStatus, string>> = {
  pending: "Waiting to be transcribed...",
  transcribing: "Transcribing...",
};

type Props = {
  projectId: string;
  message: Pick<Message, "id" | "text" | "transcript_status" | "transcript_error" | "has_audio" | "voice">;
  // Shown instead of the plain transcript (e.g. the expandable text of the Messages table)
  transcript?: React.ReactNode;
  onRetried?: (message: Message) => void;
};

export default function VoiceNote({ projectId, message, transcript, onRetried }: Props) {
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState("");
  const status = message.transcript_status;

  async function retry() {
    setRetrying(true);
    setError("");
    try {
      onRetried?.(await retryTranscription(projectId, message.id));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setRetrying(false);
    }
  }

  return (
    <div>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <VoiceBadge voice={message.voice} />
        {status === "done" && message.voice?.language && (
          <span className="text-xs text-zinc-500">{message.voice.language}</span>
        )}
      </div>

      {status === "done" ? (
        message.text ? (
          (transcript ?? <p className="whitespace-pre-line text-zinc-100">{message.text}</p>)
        ) : (
          <p className="text-zinc-500 italic">No speech was recognised.</p>
        )
      ) : status && WAITING[status] ? (
        <p className="text-zinc-400 italic">
          {WAITING[status]}
          {message.transcript_error && <span className="block text-xs not-italic text-zinc-500">{message.transcript_error}</span>}
        </p>
      ) : (
        <div className="text-sm">
          <p className="text-zinc-300">voice (not transcribed)</p>
          {message.transcript_error && <p className="mt-0.5 text-xs text-amber-300/90">{message.transcript_error}</p>}
          {status === "failed" && (
            <button
              type="button"
              onClick={retry}
              disabled={retrying}
              className="mt-1.5 rounded-md bg-white/5 px-2.5 py-1 text-xs font-medium text-zinc-200 ring-1 ring-white/10 transition hover:bg-white/10 disabled:opacity-50"
            >
              {retrying ? "Retrying..." : "Retry"}
            </button>
          )}
          {error && <p className="mt-1 text-xs text-rose-300">{error}</p>}
        </div>
      )}

      {message.has_audio && <VoicePlayer projectId={projectId} messageId={message.id} />}
    </div>
  );
}
