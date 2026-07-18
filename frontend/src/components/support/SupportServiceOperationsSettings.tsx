import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type {
  SupportBusinessDay,
  SupportServiceSettings,
  SupportServiceTargets,
} from "../../types/support";

const DAYS = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
] as const;
const PRIORITIES = ["urgent", "high", "normal", "low"] as const;
const TIMEZONES = [
  "UTC",
  "Asia/Riyadh",
  "Asia/Dubai",
  "Asia/Kuwait",
  "Asia/Qatar",
  "Asia/Bahrain",
  "Europe/London",
  "America/New_York",
  "America/Los_Angeles",
];

function cloneSettings(value: SupportServiceSettings): SupportServiceSettings {
  return JSON.parse(JSON.stringify(value)) as SupportServiceSettings;
}

function TargetGrid({
  label,
  description,
  targets,
  onChange,
}: {
  label: string;
  description: string;
  targets: SupportServiceTargets;
  onChange: (value: SupportServiceTargets) => void;
}) {
  return (
    <fieldset className="ms-support-service-targets">
      <legend>
        <strong>{label}</strong>
        <span>{description}</span>
      </legend>
      <div className="ms-support-service-target-grid">
        {PRIORITIES.map((priority) => (
          <label key={priority}>
            <span>{priority}</span>
            <input
              type="number"
              min={1}
              max={43200}
              value={targets[priority]}
              onChange={(event) =>
                onChange({
                  ...targets,
                  [priority]: Math.max(
                    1,
                    Math.min(43200, Number(event.target.value) || 1),
                  ),
                })
              }
            />
            <small>minutes</small>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function SupportServiceOperationsSettings() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<SupportServiceSettings | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const query = useQuery({
    queryKey: ["support-service-settings"],
    queryFn: ({ signal }) => supportApi.getServiceSettings(signal),
    staleTime: 30_000,
  });

  useEffect(() => {
    if (query.data) setDraft(cloneSettings(query.data));
  }, [query.data]);

  const mutation = useMutation({
    mutationFn: (payload: SupportServiceSettings) =>
      supportApi.updateServiceSettings(payload),
    onMutate: () => {
      setMessage("");
      setError("");
    },
    onSuccess: async (saved) => {
      setDraft(cloneSettings(saved));
      setMessage("Service operations saved.");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["support-service-settings"] }),
        queryClient.invalidateQueries({ queryKey: ["support-conversations"] }),
      ]);
    },
    onError: (reason) =>
      setError(
        parseApiError(reason, "Service operations could not be saved.").message,
      ),
  });

  const updateDay = (day: (typeof DAYS)[number], value: SupportBusinessDay) => {
    if (!draft) return;
    setDraft({
      ...draft,
      business_hours: { ...draft.business_hours, [day]: value },
    });
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (draft && !mutation.isPending) mutation.mutate(draft);
  };

  if (query.isLoading || !draft) {
    return (
      <section className="ms-page-surface ms-page-surface--padded">
        <div className="ms-support-inbox-state">Loading service operations…</div>
      </section>
    );
  }
  if (query.isError) {
    return (
      <section className="ms-page-surface ms-page-surface--padded">
        <div className="ms-page-error" role="alert">
          {parseApiError(query.error, "Service operations could not be loaded.").message}
        </div>
      </section>
    );
  }

  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <div className="ms-support-workflow-heading">
        <div>
          <span>Service operations</span>
          <h2>Response targets and business hours</h2>
          <p>
            Deadlines and alerts apply only to Support Chat. Personal Messenger
            remains independent.
          </p>
        </div>
      </div>
      <form className="ms-support-service-form" onSubmit={submit}>
        <div className="ms-support-service-basics">
          <label>
            <span>Service timezone</span>
            <select
              value={draft.timezone}
              onChange={(event) =>
                setDraft({ ...draft, timezone: event.target.value })
              }
            >
              {!TIMEZONES.includes(draft.timezone) ? (
                <option value={draft.timezone}>{draft.timezone}</option>
              ) : null}
              {TIMEZONES.map((zone) => (
                <option value={zone} key={zone}>
                  {zone}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Due-soon warning</span>
            <div className="ms-support-service-number">
              <input
                type="number"
                min={1}
                max={1440}
                value={draft.due_soon_minutes}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    due_soon_minutes: Math.max(
                      1,
                      Math.min(1440, Number(event.target.value) || 1),
                    ),
                  })
                }
              />
              <small>minutes before</small>
            </div>
          </label>
          <label>
            <span>Default follow-up</span>
            <div className="ms-support-service-number">
              <input
                type="number"
                min={1}
                max={43200}
                value={draft.default_follow_up_minutes}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    default_follow_up_minutes: Math.max(
                      1,
                      Math.min(43200, Number(event.target.value) || 1),
                    ),
                  })
                }
              />
              <small>minutes</small>
            </div>
          </label>
        </div>

        <label className="ms-support-toggle-row ms-support-service-hours-toggle">
          <input
            type="checkbox"
            checked={draft.business_hours_enabled}
            onChange={(event) =>
              setDraft({ ...draft, business_hours_enabled: event.target.checked })
            }
          />
          <span>
            <strong>Count targets during business hours only</strong>
            <small>Outside hours and closed days do not consume SLA time.</small>
          </span>
        </label>

        <div className="ms-support-business-hours" aria-label="Business hours">
          {DAYS.map((day) => {
            const value = draft.business_hours[day];
            return (
              <div className="ms-support-business-day" key={day}>
                <label className="ms-support-business-day__enabled">
                  <input
                    type="checkbox"
                    checked={value.enabled}
                    disabled={!draft.business_hours_enabled}
                    onChange={(event) =>
                      updateDay(day, { ...value, enabled: event.target.checked })
                    }
                  />
                  <strong>{day.slice(0, 3)}</strong>
                </label>
                <label>
                  <span>Open</span>
                  <input
                    type="time"
                    value={value.start}
                    disabled={!draft.business_hours_enabled || !value.enabled}
                    onChange={(event) =>
                      updateDay(day, { ...value, start: event.target.value })
                    }
                  />
                </label>
                <label>
                  <span>Close</span>
                  <input
                    type="time"
                    value={value.end}
                    disabled={!draft.business_hours_enabled || !value.enabled}
                    onChange={(event) =>
                      updateDay(day, { ...value, end: event.target.value })
                    }
                  />
                </label>
              </div>
            );
          })}
        </div>

        <div className="ms-support-service-target-stack">
          <TargetGrid
            label="First response"
            description="From the visitor's first message until the first team reply."
            targets={draft.first_response_targets}
            onChange={(value) => setDraft({ ...draft, first_response_targets: value })}
          />
          <TargetGrid
            label="Next response"
            description="After a visitor replies while the team is handling the conversation."
            targets={draft.next_response_targets}
            onChange={(value) => setDraft({ ...draft, next_response_targets: value })}
          />
          <TargetGrid
            label="Resolution"
            description="From conversation creation until it is resolved or closed."
            targets={draft.resolution_targets}
            onChange={(value) => setDraft({ ...draft, resolution_targets: value })}
          />
        </div>

        <div className="ms-support-service-alert-options">
          <label className="ms-support-toggle-row">
            <input
              type="checkbox"
              checked={draft.alert_owner}
              onChange={(event) => setDraft({ ...draft, alert_owner: event.target.checked })}
            />
            <span><strong>Alert owner</strong><small>Receive due-soon, overdue, and follow-up alerts.</small></span>
          </label>
          <label className="ms-support-toggle-row">
            <input
              type="checkbox"
              checked={draft.alert_assigned_agent}
              onChange={(event) =>
                setDraft({ ...draft, alert_assigned_agent: event.target.checked })
              }
            />
            <span><strong>Alert assigned agent</strong><small>Notify the person responsible for the conversation.</small></span>
          </label>
        </div>

        {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
        {message ? <div className="ms-support-success" role="status">{message}</div> : null}
        <div className="ms-support-form-actions">
          <button
            className="ms-button ms-button--primary"
            type="submit"
            disabled={mutation.isPending}
          >
            {mutation.isPending ? "Saving…" : "Save service operations"}
          </button>
        </div>
      </form>
    </section>
  );
}
