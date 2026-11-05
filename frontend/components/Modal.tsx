"use client";

import { useEffect, useRef } from "react";

// A dialog on the native <dialog> element: the browser traps focus, handles Esc and honours autoFocus.
// Clicking outside or pressing Esc calls onClose, which may keep the dialog open (e.g. unsaved changes).
export default function Modal({
  title,
  onClose,
  wide = false,
  children,
}: {
  title: string;
  onClose: () => void;
  wide?: boolean;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  // No close() on cleanup: Strict Mode runs effects twice, and the queued close event would call onClose.
  // Unmounting removes the dialog from the top layer anyway.
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      aria-label={title}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClose={onClose}
      onMouseDown={(e) => {
        if (e.target === ref.current) onClose(); // the backdrop counts as the dialog itself
      }}
      className={`m-auto w-[calc(100vw-2rem)] rounded-2xl border border-white/10 bg-zinc-900 p-0 text-zinc-100 shadow-2xl backdrop:bg-black/60 ${
        wide ? "max-w-2xl" : "max-w-lg"
      }`}
    >
      <div className="p-6">
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {children}
      </div>
    </dialog>
  );
}
