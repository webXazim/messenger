import { useId, useRef } from "react";
import { useModalAccessibility } from "../hooks/useModalAccessibility";
import type { Call } from "../types/chat";
import { UserAvatar } from "./UserAvatar";

function callerName(call: Call) {
  return call.initiated_by?.display_name || call.initiated_by?.username || "Someone";
}

export function IncomingCallOverlay({
  call,
  action,
  error,
  onMinimize,
  onAccept,
  onDecline,
}: {
  call: Call;
  action?: "accepting" | "declining" | null;
  error?: string | null;
  onMinimize: () => void;
  onAccept: () => void;
  onDecline: () => void;
}) {
  const busy = Boolean(action);
  const participants = call.participants?.filter((participant) => participant.user.id !== call.initiated_by?.id) ?? [];
  const name = callerName(call);
  const titleId = useId();
  const detailId = useId();
  const declineRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useModalAccessibility<HTMLElement>({
    onClose: onMinimize,
    initialFocusRef: declineRef,
    closeOnEscape: !busy,
  });

  return (
    <div className="ms-overlay" role="presentation">
      <section
        ref={dialogRef}
        className="ms-incoming-call"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={detailId}
        aria-busy={busy}
        tabIndex={-1}
      >
        <button type="button" className="ms-incoming-call__minimize" onClick={onMinimize} disabled={busy} aria-label="Minimize incoming call">
          —
        </button>
        <div className="ms-incoming-call__pulse" aria-hidden="true"><UserAvatar person={call.initiated_by ?? { display_name: name }} size="xl" decorative /></div>
        <div className="ms-incoming-call__eyebrow">Incoming {call.call_type === "video" ? "video" : "voice"} call</div>
        <h2 id={titleId}>{name}</h2>
        <div id={detailId} className="ms-muted ms-text-center">
          {participants.length > 1 ? `Group call with ${participants.length + 1} people` : "Answer or decline the call"}
        </div>
        {error ? <div className="ms-incoming-call__error" role="alert">{error}</div> : null}
        <div className="ms-button-row ms-button-row--center">
          <button ref={declineRef} type="button" className="ms-button ms-incoming-call__button" onClick={onDecline} disabled={busy}>
            {action === "declining" ? "Declining…" : "Decline"}
          </button>
          <button type="button" className="ms-button ms-button--primary ms-incoming-call__button" onClick={onAccept} disabled={busy}>
            {action === "accepting" ? "Answering…" : "Answer"}
          </button>
        </div>
      </section>
    </div>
  );
}
