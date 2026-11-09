"use client";

// A voice note: the 🎤 badge, its transcript or why there is none, Retry for failed ones, and a player when the
// audio was kept (KEEP_AUDIO=true on the server, or not transcribed yet).

import { useState } from "react";
import { errorText } from "@/components/ui";
import {
  messageAudioUrl,
  retranscribe,
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
    <audio controls preload="metadata" src={messageAudioUrl(projectId, messageId)} className="mt-2 h-8 w-full max-w-xs" />
  );
}

const WAITING: Partial<Record<TranscriptStatus, string>> = {
  pending: "Waiting to be transcribed...",
  transcribing: "Transcribing...",
};

const RETRANSCRIBE_LANGUAGES = [
  { value: "bn", label: "Bangla" },
  { value: "en", label: "English" },
] as const;

const smallButton =
  "rounded-md bg-white/5 px-2.5 py-1 text-xs font-medium text-zinc-200 ring-1 ring-white/10 transition hover:bg-white/10 disabled:opacity-50";

type Props = {
  projectId: string;
  message: Pick<
    Message,
    "id" | "text" | "transcript_status" | "transcript_error" | "has_audio" | "can_retranscribe" | "voice"
  >;
  // Shown instead of the plain transcript (e.g. the expandable text of the Messages table)
  transcript?: React.ReactNode;
  // After Retry or Re-transcribe changed the message
  onRetried?: (message: Message) => void;
};

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// "bangla", plus how it got there: "auto-corrected from hindi" / "chosen"
function LanguageLabel({ voice }: { voice: VoiceInfo | null }) {
  if (!voice?.language) return null;
  const note = voice.retried
    ? `auto-corrected from ${voice.detected_language ?? "another language"}`
    : voice.requested_language && voice.requested_language !== "auto"
      ? "chosen"
      : "";
  return (
    <span
      className="text-xs text-zinc-500"
      title={voice.retry_reason ? `Transcribed again as Bangla: ${voice.retry_reason}` : undefined}
    >
      {voice.language}
      {note && ` · ${note}`}
      {voice.previous_transcripts > 0 && ` · ${plural(voice.previous_transcripts, "earlier transcript")} kept`}
    </span>
  );
}

export default function VoiceNote({ projectId, message, transcript, onRetried }: Props) {
  const [busy, setBusy] = useState<"" | "retry" | "bn" | "en">("");
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const status = message.transcript_status;

  async function retry() {
    setBusy("retry");
    setError("");
    try {
      onRetried?.(await retryTranscription(projectId, message.id));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy("");
    }
  }

  async function transcribeAgain(language: "bn" | "en") {
    setBusy(language);
    setError("");
    setDone("");
    try {
      const result = await retranscribe(projectId, message.id, language);
      const found = result.extraction.requirements_created;
      setDone(`New transcript saved; ${found === 0 ? "no new requirements" : plural(found, "new requirement")}.`);
      onRetried?.(result.message);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy("");
    }
  }

  const canTranscribeAgain = status === "done" || status === "failed";

  return (
    <div>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <VoiceBadge voice={message.voice} />
        {status === "done" && <LanguageLabel voice={message.voice} />}
      </div>

      {status === "done" ? (
        message.text ? (
          (transcript ?? <p className="whitespace-pre-line text-zinc-100">{message.text}</p>)
        ) : (
          <p className="text-zinc-500 italic">No speech was recognised.</p>
        )
      ) : status && WAITING[status] ? (
        <p className="text-zinc-400 italic">
          {busy === "bn" || busy === "en" ? "Transcribing again..." : WAITING[status]}
          {message.transcript_error && (
            <span className="block text-xs not-italic text-zinc-500">{message.transcript_error}</span>
          )}
        </p>
      ) : (
        <div className="text-sm">
          <p className="text-zinc-300">voice (not transcribed)</p>
          {message.transcript_error && <p className="mt-0.5 text-xs text-amber-300/90">{message.transcript_error}</p>}
          {status === "failed" && (
            <button type="button" onClick={retry} disabled={busy !== ""} className={`mt-1.5 ${smallButton}`}>
              {busy === "retry" ? "Retrying..." : "Retry"}
            </button>
          )}
        </div>
      )}

      {canTranscribeAgain && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-zinc-500">
          <span>Re-transcribe as:</span>
          {RETRANSCRIBE_LANGUAGES.map((l) => (
            <button
              key={l.value}
              type="button"
              onClick={() => transcribeAgain(l.value)}
              disabled={busy !== "" || !message.can_retranscribe}
              title={
                message.can_retranscribe
                  ? `Transcribe this voice note again as ${l.label} and extract its requirements again`
                  : "The audio was deleted after transcription (KEEP_AUDIO=false). Upload the file again to re-transcribe."
              }
              className={smallButton}
            >
              {busy === l.value ? "Transcribing..." : l.label}
            </button>
          ))}
        </div>
      )}
      {done && <p className="mt-1 text-xs text-emerald-300">{done}</p>}
      {error && <p className="mt-1 text-xs text-rose-300">{error}</p>}

      {message.has_audio && <VoicePlayer projectId={projectId} messageId={message.id} />}
    </div>
  );
}
