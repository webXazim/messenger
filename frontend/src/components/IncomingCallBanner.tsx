import type { Call } from "../types/chat";
import { UserAvatar } from "./UserAvatar";

function callerName(call: Call) {
  return call.initiated_by?.display_name || call.initiated_by?.username || "Someone";
}

export function IncomingCallBanner({
  call,
  action,
  error,
  onOpen,
  onAccept,
  onDecline,
}: {
  call: Call;
  action?: "accepting" | "declining" | null;
  error?: string | null;
  onOpen: () => void;
  onAccept: () => void;
  onDecline: () => void;
}) {
  const busy = Boolean(action);
  return (
    <section className="ms-incoming-call-banner" aria-label="Incoming call" aria-live="assertive">
      <button type="button" className="ms-incoming-call-banner__open" onClick={onOpen} disabled={busy}>
        <UserAvatar person={call.initiated_by ?? { display_name: callerName(call) }} size="md" className="ms-incoming-call-banner__avatar" decorative />
        <span className="ms-incoming-call-banner__copy">
          <span className="ms-incoming-call-banner__title">{callerName(call)}</span>
          <span>{call.call_type === "video" ? "Incoming video call" : "Incoming voice call"}</span>
          {error ? <span className="ms-incoming-call-banner__error">{error}</span> : null}
        </span>
      </button>
      <div className="ms-button-row">
        <button type="button" className="ms-button ms-button--inverse-ghost ms-button--compact" onClick={onDecline} disabled={busy}>
          {action === "declining" ? "Declining…" : "Decline"}
        </button>
        <button type="button" className="ms-button ms-button--inverse ms-button--compact" onClick={onAccept} disabled={busy}>
          {action === "accepting" ? "Answering…" : "Answer"}
        </button>
      </div>
    </section>
  );
}
