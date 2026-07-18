import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import { MessengerSectionHeader } from "../pages/MessengerPageHeader";
import type { SupportFeedbackSettings as FeedbackSettings } from "../../types/support";

export function SupportFeedbackSettings() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["support-feedback-settings"],
    queryFn: ({ signal }) => supportApi.getFeedbackSettings(signal),
  });
  const [form, setForm] = useState<FeedbackSettings | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (query.data) setForm(query.data);
  }, [query.data]);

  const mutation = useMutation({
    mutationFn: (payload: Partial<FeedbackSettings>) =>
      supportApi.updateFeedbackSettings(payload),
    onSuccess: async (data) => {
      setForm(data);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2200);
      await queryClient.invalidateQueries({ queryKey: ["support-feedback-settings"] });
    },
  });

  if (query.isLoading || !form) {
    return <section className="ms-page-surface ms-page-surface--padded"><div className="ms-support-empty">Loading customer feedback settings…</div></section>;
  }

  if (query.isError) {
    return <section className="ms-page-surface ms-page-surface--padded"><div className="ms-support-error">{parseApiError(query.error, "Customer feedback settings could not be loaded.").message}</div></section>;
  }

  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader
        eyebrow="Customer feedback"
        title="Satisfaction requests"
        description="Ask visitors for a simple 1–5 rating after a Support conversation is resolved. Feedback never affects personal Messenger."
      />
      <div className="ms-support-feedback-settings">
        <label className="ms-support-toggle-row">
          <input
            type="checkbox"
            checked={form.csat_enabled}
            onChange={(event) => setForm({ ...form, csat_enabled: event.target.checked })}
          />
          <span><strong>Enable satisfaction ratings</strong><small>Allow resolved Support conversations to request visitor feedback.</small></span>
        </label>
        <label className="ms-support-toggle-row">
          <input
            type="checkbox"
            checked={form.auto_request_on_resolve}
            disabled={!form.csat_enabled}
            onChange={(event) => setForm({ ...form, auto_request_on_resolve: event.target.checked })}
          />
          <span><strong>Request automatically</strong><small>Show the rating prompt when a conversation is resolved.</small></span>
        </label>
        <label className="ms-support-toggle-row">
          <input
            type="checkbox"
            checked={form.allow_comment}
            disabled={!form.csat_enabled}
            onChange={(event) => setForm({ ...form, allow_comment: event.target.checked })}
          />
          <span><strong>Allow a written comment</strong><small>Visitors can optionally explain their rating.</small></span>
        </label>
        <label className="ms-support-control-field ms-support-feedback-expiry">
          <span>Request expires after</span>
          <div className="ms-support-inline-number">
            <input
              type="number"
              min={1}
              max={365}
              value={form.survey_expiry_days}
              disabled={!form.csat_enabled}
              onChange={(event) => setForm({ ...form, survey_expiry_days: Math.min(365, Math.max(1, Number(event.target.value) || 1)) })}
            />
            <span>days</span>
          </div>
        </label>
      </div>
      {mutation.isError ? <div className="ms-support-error">{parseApiError(mutation.error, "Customer feedback settings could not be saved.").message}</div> : null}
      <div className="ms-support-settings-actions">
        {saved ? <span className="ms-support-save-status">Saved</span> : null}
        <button
          type="button"
          className="ms-button ms-button--primary"
          disabled={mutation.isPending}
          onClick={() => mutation.mutate({
            csat_enabled: form.csat_enabled,
            auto_request_on_resolve: form.auto_request_on_resolve,
            allow_comment: form.allow_comment,
            survey_expiry_days: form.survey_expiry_days,
          })}
        >
          {mutation.isPending ? "Saving…" : "Save feedback settings"}
        </button>
      </div>
    </section>
  );
}
