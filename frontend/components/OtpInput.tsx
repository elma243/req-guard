"use client";

import { useEffect, useRef } from "react";
import { OTP_LENGTH } from "@/lib/authRules";

type Props = {
  value: string[]; // OTP_LENGTH entries, "" for an empty box
  onChange: (digits: string[]) => void;
  hasError?: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
};

export const emptyOtp = () => Array<string>(OTP_LENGTH).fill("");

const digitsIn = (text: string) => text.replace(/[^0-9]/g, "").split("");

// Row of one-digit boxes. Typing moves forward, Backspace and the arrow keys move back,
// and pasting a code (or a phone offering it from a message) fills the row from the first box.
export default function OtpInput({ value, onChange, hasError = false, disabled = false, autoFocus = false }: Props) {
  const boxes = useRef<(HTMLInputElement | null)[]>([]);
  const focus = (index: number) => boxes.current[Math.max(0, Math.min(index, OTP_LENGTH - 1))]?.focus();

  // Ready to type on arrival, and again after a wrong code clears the row
  const empty = value.every((digit) => !digit);
  useEffect(() => {
    if (autoFocus && empty && !disabled) boxes.current[0]?.focus();
  }, [autoFocus, empty, disabled]);

  function fillFromStart(digits: string[]) {
    const next = emptyOtp().map((_, i) => digits[i] ?? "");
    onChange(next);
    focus(digits.length);
  }

  function handleChange(index: number, text: string) {
    const digits = digitsIn(text);
    if (digits.length > 1) return fillFromStart(digits);
    if (digits.length === 0) return;
    onChange(value.map((d, i) => (i === index ? digits[0] : d)));
    focus(index + 1);
  }

  function handleKeyDown(index: number, ev: React.KeyboardEvent<HTMLInputElement>) {
    if (ev.key === "ArrowLeft") {
      ev.preventDefault();
      focus(index - 1);
    } else if (ev.key === "ArrowRight") {
      ev.preventDefault();
      focus(index + 1);
    } else if (ev.key === "Backspace") {
      ev.preventDefault();
      const target = value[index] ? index : Math.max(index - 1, 0);
      onChange(value.map((d, i) => (i === target ? "" : d)));
      focus(target);
    }
  }

  function handlePaste(ev: React.ClipboardEvent<HTMLInputElement>) {
    ev.preventDefault();
    const digits = digitsIn(ev.clipboardData.getData("text"));
    if (digits.length > 0) fillFromStart(digits);
  }

  return (
    <div role="group" aria-label={`${OTP_LENGTH}-digit code`} className="flex justify-between gap-2">
      {value.map((digit, index) => (
        <input
          key={index}
          ref={(el) => {
            boxes.current[index] = el;
          }}
          type="text"
          inputMode="numeric"
          autoComplete={index === 0 ? "one-time-code" : "off"}
          aria-label={`Digit ${index + 1}`}
          aria-invalid={hasError}
          disabled={disabled}
          value={digit}
          onChange={(e) => handleChange(index, e.target.value)}
          onKeyDown={(e) => handleKeyDown(index, e)}
          onPaste={handlePaste}
          onFocus={(e) => e.target.select()}
          className={`h-14 w-full min-w-0 rounded-xl border bg-zinc-950/60 text-center text-xl font-semibold text-zinc-100 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-500/20 disabled:opacity-60 ${
            hasError ? "border-rose-500/70" : "border-white/10"
          }`}
        />
      ))}
    </div>
  );
}
