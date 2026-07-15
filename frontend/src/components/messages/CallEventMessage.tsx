import type { ReactNode } from "react";
import type { CallEventPresentation } from "./messagePresentation";

function PhoneIcon({ video }: { video: boolean }) {
  if (video) {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="6" width="12.5" height="12" rx="3" /><path d="m16 10 4.5-2.5v9L16 14" /></svg>;
  }
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4h3l1 4-2 1.6a14.5 14.5 0 0 0 5.4 5.4l1.6-2 4 1v3c0 1.1-.9 2-2 2C10.8 19 5 13.2 5 6c0-1.1.9-2 2-2Z" /></svg>;
}

export function CallEventMessage({ event, footer }: { event: CallEventPresentation; footer?: ReactNode }) {
  return (
    <div className={`ms-call-message ms-call-message--${event.tone}`}>
      <span className="ms-call-message__icon"><PhoneIcon video={event.callType === "video"} /></span>
      <span className="ms-call-message__copy">
        <strong>{event.title}</strong>
        <small>{event.detail}</small>
      </span>
      <span className={`ms-call-message__direction ms-call-message__direction--${event.direction}`} aria-hidden="true">
        {event.direction === "incoming" ? "↙" : event.direction === "outgoing" ? "↗" : "—"}
      </span>
      {footer ? <div className="ms-call-message__meta">{footer}</div> : null}
    </div>
  );
}
