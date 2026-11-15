"use client";

import { useState } from "react";
import Modal from "@/components/Modal";
import { Label, inputClass, primaryButton, secondaryButton } from "@/components/ui";
import type { Requirement } from "@/lib/api";

// Shown only when the requirement has open High findings: approving over them needs a reason, which is recorded.
export default function ApproveDialog({
  requirement: r,
  onConfirm,
  onClose,
}: {
  requirement: Requirement;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const count = r.open_high_findings;

  return (
    <Modal title={`Approve ${r.code} anyway?`} onClose={onClose}>
      <form
        className="mt-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (reason.trim()) onConfirm(reason.trim());
        }}
      >
        <p className="text-sm text-zinc-300">
          {r.code} has {count} open High finding{count === 1 ? "" : "s"}, such as a possible conflict or a possibly
          missing value. Resolve or reject {count === 1 ? "it" : "them"} first, or give a reason to approve anyway. The
          reason is kept with the approval.
        </p>
        <div className="mt-4">
          <Label htmlFor="override-reason">Reason</Label>
          <textarea
            id="override-reason"
            autoFocus
            required
            rows={3}
            maxLength={1000}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className={inputClass}
          />
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryButton}>
            Cancel
          </button>
          <button type="submit" disabled={!reason.trim()} className={primaryButton}>
            Approve with reason
          </button>
        </div>
      </form>
    </Modal>
  );
}
