import type { ConversationNotificationSettings } from "../../../api/chat";
import { DetailsSection } from "./DetailsSection";

export function formatMuteValue(value?: string | null) {
  if (!value) return "Not muted";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Muted" : `Muted until ${date.toLocaleString()}`;
}

export function ConversationNotificationsSection({
  notifications,
  onToggleNotification,
  onSetMuteHours,
}: {
  notifications?: ConversationNotificationSettings;
  onToggleNotification: (patch: Partial<ConversationNotificationSettings>) => void;
  onSetMuteHours: (hours: number | null) => void;
}) {
  const muteValue = notifications?.mute_until ?? notifications?.muted_until;

  return (
    <DetailsSection title="Notifications" eyebrow="Preferences" note={formatMuteValue(muteValue)} collapsible defaultOpen={false}>
      <div className="ms-details-toggle-list">
        <label>
          <span><strong>Messages</strong><small>New message alerts</small></span>
          <input
            type="checkbox"
            checked={notifications?.message_notifications_enabled !== false}
            onChange={(event) => onToggleNotification({ message_notifications_enabled: event.target.checked })}
          />
        </label>
        <label>
          <span><strong>Calls</strong><small>Incoming call alerts</small></span>
          <input
            type="checkbox"
            checked={notifications?.call_notifications_enabled !== false}
            onChange={(event) => onToggleNotification({ call_notifications_enabled: event.target.checked })}
          />
        </label>
        <label>
          <span><strong>Mentions only</strong><small>Reduce group message alerts</small></span>
          <input
            type="checkbox"
            checked={Boolean(notifications?.mentions_only)}
            onChange={(event) => onToggleNotification({ mentions_only: event.target.checked })}
          />
        </label>
      </div>

      <div className="ms-details-mute-options" role="group" aria-label="Mute duration">
        <button type="button" onClick={() => onSetMuteHours(1)}>1 hour</button>
        <button type="button" onClick={() => onSetMuteHours(8)}>8 hours</button>
        <button type="button" onClick={() => onSetMuteHours(24)}>1 day</button>
        <button type="button" onClick={() => onSetMuteHours(null)}>Clear</button>
      </div>
    </DetailsSection>
  );
}
