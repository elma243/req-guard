"use client";

import { useState } from "react";
import Modal from "@/components/Modal";
import { Label, dangerButton, inputClass, secondaryButton } from "@/components/ui";

// The reason is optional. Enter confirms, Shift+Enter starts a new line.
export default function RejectDialog({
  title,
  onConfirm,
  onClose,
}: {
  title: string;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");

  return (
    <Modal title={title} onClose={onClose}>
      <form
        className="mt-4"
        onSubmit={(e) => {
          e.preventDefault();
          onConfirm(reason.trim());
        }}
      >
        <Label htmlFor="reject-reason">Reason (optional)</Label>
        <textarea
          id="reject-reason"
          autoFocus
          rows={3}
          maxLength={1000}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          onKeyDown={(e) => {
            // isComposing: Enter is picking a word in a Bangla input method, not submitting
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
          className={inputClass}
        />
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryButton}>
            Cancel
          </button>
          <button type="submit" className={dangerButton}>
            Reject
          </button>
        </div>
      </form>
    </Modal>
  );
}
