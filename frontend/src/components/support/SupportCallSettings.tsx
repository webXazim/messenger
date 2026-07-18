import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { MessengerSectionHeader } from "../pages/MessengerPageHeader";
import { parseApiError } from "../../lib/apiErrors";

export function SupportCallSettings() {
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const query = useQuery({ queryKey: ["support-call-settings"], queryFn: ({ signal }) => supportApi.getCallSettings(signal) });
  const mutation = useMutation({
    mutationFn: supportApi.updateCallSettings,
    onMutate: () => setError(""),
    onSuccess: (data) => queryClient.setQueryData(["support-call-settings"], data),
    onError: (value) => setError(parseApiError(value, "Call settings could not be saved.").message),
  });
  const settings = query.data;
  if (!settings) return null;
  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader eyebrow="Visitor calls" title="Audio and video calls" description="Calls use the existing TURN and WebRTC deployment, but remain isolated from personal Messenger calls." />
      {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
      <div className="ms-support-call-settings-grid">
        <label className="ms-support-toggle-row">
          <input type="checkbox" checked={settings.enabled} disabled={mutation.isPending} onChange={(event) => mutation.mutate({ enabled: event.target.checked })} />
          <span><strong>Enable Support calls</strong><small>Allow agents to call visitors from open Support conversations.</small></span>
        </label>
        <label className="ms-support-toggle-row">
          <input type="checkbox" checked={settings.allow_video} disabled={!settings.enabled || mutation.isPending} onChange={(event) => mutation.mutate({ allow_video: event.target.checked })} />
          <span><strong>Allow video calls</strong><small>Audio calls remain available when video is disabled.</small></span>
        </label>
        <label className="ms-support-field-row">
          <span><strong>Maximum call duration</strong><small>Calls are monitored against this operational limit.</small></span>
          <select value={settings.max_duration_minutes} disabled={mutation.isPending} onChange={(event) => mutation.mutate({ max_duration_minutes: Number(event.target.value) })}>
            {[15, 30, 60, 90, 120].map((value) => <option value={value} key={value}>{value} minutes</option>)}
          </select>
        </label>
      </div>
    </section>
  );
}
