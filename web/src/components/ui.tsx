import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { ApiError } from "../api/client";
import { statusLabel } from "../format";

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return <span className={`badge badge-${status}`}>{label ?? statusLabel(status)}</span>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="muted" role="status">
      {label}
    </p>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="error-box" role="alert">
      <p>{errorMessage(error)}</p>
      {onRetry && (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Something went wrong. Please try again.";
}

/** Accessible modal dialog: traps focus, closes on Escape, returns focus to the opener. */
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>("button, input, select, textarea, [tabindex]");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && ref.current) {
        const items = Array.from(ref.current.querySelectorAll<HTMLElement>("button, input, select, textarea"))
          .filter((el) => !el.hasAttribute("disabled"));
        if (!items.length) return;
        const [firstItem, lastItem] = [items[0], items[items.length - 1]];
        if (e.shiftKey && document.activeElement === firstItem) {
          e.preventDefault();
          lastItem.focus();
        } else if (!e.shiftKey && document.activeElement === lastItem) {
          e.preventDefault();
          firstItem.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, [onClose]);
  return (
    <div className="modal-backdrop">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref}>
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function ConfirmDialog(props: {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
  askReason?: "required" | "optional";
  onConfirm: (reason: string | null) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const reasonId = useId();
  const submit = () => {
    if (props.askReason === "required" && reason.trim().length < 3) {
      setProblem("Please give a reason (at least 3 characters).");
      return;
    }
    props.onConfirm(reason.trim() || null);
  };
  return (
    <Modal title={props.title} onClose={props.onCancel}>
      <p>{props.message}</p>
      {props.askReason && (
        <div className="field">
          <label htmlFor={reasonId}>Reason{props.askReason === "optional" ? " (optional)" : ""}</label>
          <textarea id={reasonId} value={reason} onChange={(e) => setReason(e.target.value)} rows={3}
            aria-invalid={problem ? true : undefined} />
        </div>
      )}
      {problem && <p className="error-text" role="alert">{problem}</p>}
      <div className="actions">
        <button type="button" onClick={props.onCancel}>Cancel</button>
        <button type="button" className={props.danger ? "danger" : "primary"} onClick={submit}>
          {props.confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
