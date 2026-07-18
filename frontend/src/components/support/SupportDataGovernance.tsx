import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ConfirmDialog } from "../ConfirmDialog";
import { MessengerSectionHeader } from "../pages/MessengerPageHeader";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type { SupportPrivacySettings, SupportWebhookEndpoint } from "../../types/support";

function formatDateTime(value?: string | null) {
  if (!value) return "Not yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not yet";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatBytes(value: number) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let amount = value;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function PrivacySettingsCard() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["support-privacy-settings"], queryFn: ({ signal }) => supportApi.getPrivacySettings(signal) });
  const [form, setForm] = useState<SupportPrivacySettings | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => { if (query.data) setForm(query.data); }, [query.data]);
  const mutation = useMutation({
    mutationFn: (payload: Partial<SupportPrivacySettings>) => supportApi.updatePrivacySettings(payload),
    onMutate: () => setMessage(null),
    onSuccess: async (data) => {
      setForm(data);
      setMessage("Privacy settings saved.");
      await queryClient.invalidateQueries({ queryKey: ["support-privacy-settings"] });
    },
    onError: (error) => setMessage(parseApiError(error, "Privacy settings could not be saved.").message),
  });
  if (!form) return <section className="ms-page-surface ms-page-surface--padded"><div className="ms-support-empty">Loading privacy settings…</div></section>;
  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader eyebrow="Privacy" title="Retention and visitor rights" description="These controls apply only to website visitor Support data. Personal Messenger is never included." />
      <form className="ms-support-governance-form" onSubmit={(event) => { event.preventDefault(); mutation.mutate(form); }}>
        <label className="ms-support-toggle-row">
          <input type="checkbox" checked={form.retention_enabled} onChange={(event) => setForm({ ...form, retention_enabled: event.target.checked })} />
          <span><strong>Automatic conversation retention</strong><small>Remove old resolved or closed Support conversations after the selected period.</small></span>
        </label>
        <div className="ms-support-governance-grid">
          <label><span>Resolved conversations</span><input type="number" min={30} max={3650} value={form.resolved_conversation_retention_days} onChange={(event) => setForm({ ...form, resolved_conversation_retention_days: Number(event.target.value) })} /><small>Days before eligible Support conversations are removed.</small></label>
          <label><span>Closed widget sessions</span><input type="number" min={7} max={730} value={form.widget_session_retention_days} onChange={(event) => setForm({ ...form, widget_session_retention_days: Number(event.target.value) })} /><small>Days to keep expired, revoked, or closed browser sessions.</small></label>
          <label><span>Export availability</span><input type="number" min={1} max={30} value={form.export_retention_days} onChange={(event) => setForm({ ...form, export_retention_days: Number(event.target.value) })} /><small>Days before generated export files expire.</small></label>
        </div>
        <label className="ms-support-toggle-row">
          <input type="checkbox" checked={form.allow_visitor_deletion_requests} onChange={(event) => setForm({ ...form, allow_visitor_deletion_requests: event.target.checked })} />
          <span><strong>Allow visitor deletion requests</strong><small>Signed widget visitors may request removal of their own Support history.</small></span>
        </label>
        <label className="ms-support-toggle-row">
          <input type="checkbox" checked={form.include_attachments_in_exports} onChange={(event) => setForm({ ...form, include_attachments_in_exports: event.target.checked })} />
          <span><strong>Include attachment files by default</strong><small>Exports always include attachment metadata. File content is optional and size-limited.</small></span>
        </label>
        <div className="ms-support-form-actions"><button className="ms-button ms-button--primary" type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Saving…" : "Save privacy settings"}</button>{message ? <span role="status">{message}</span> : null}</div>
      </form>
    </section>
  );
}

function ExportsCard() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["support-data-exports"], queryFn: ({ signal }) => supportApi.listDataExports(signal), refetchInterval: 15_000 });
  const mutation = useMutation({
    mutationFn: () => supportApi.createDataExport(),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-data-exports"] }),
  });
  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader eyebrow="Exports" title="Support account data" description="Generate a private ZIP containing only Support websites, visitors, conversations, messages, workflow, feedback, and audit records." actions={<button className="ms-button ms-button--primary ms-button--compact" type="button" disabled={mutation.isPending || query.data?.some((item) => item.status === "pending" || item.status === "processing")} onClick={() => mutation.mutate()}>{mutation.isPending ? "Requesting…" : "Create export"}</button>} />
      {mutation.isError ? <div className="ms-support-detail-error" role="alert">{parseApiError(mutation.error, "Export could not be requested.").message}</div> : null}
      <div className="ms-support-governance-list">
        {query.data?.map((item) => (
          <article className="ms-support-governance-row" key={item.id}>
            <div><strong>{item.status === "ready" ? "Export ready" : item.status === "failed" ? "Export failed" : "Preparing export"}</strong><span>{formatDateTime(item.created_at)} · {formatBytes(item.file_size)}</span>{item.error ? <small>{item.error}</small> : null}</div>
            <div className="ms-support-governance-row__actions"><span className={`ms-page-badge${item.status === "ready" ? " ms-page-badge--strong" : ""}`}>{item.status}</span>{item.download_url ? <a className="ms-button ms-button--ghost ms-button--compact" href={item.download_url}>Download</a> : null}</div>
          </article>
        ))}
        {!query.isLoading && !query.data?.length ? <div className="ms-support-empty">No Support data exports have been requested.</div> : null}
      </div>
    </section>
  );
}

function WebhooksCard() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["support-webhooks"], queryFn: ({ signal }) => supportApi.listWebhooks(signal) });
  const deliveriesQuery = useQuery({ queryKey: ["support-webhook-deliveries"], queryFn: ({ signal }) => supportApi.listWebhookDeliveries(undefined, signal) });
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["conversation.created", "message.created"]);
  const [secret, setSecret] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SupportWebhookEndpoint | null>(null);
  const createMutation = useMutation({
    mutationFn: () => supportApi.createWebhook({ name, url, event_types: events }),
    onSuccess: async (data) => {
      setName(""); setUrl(""); setSecret(data.signing_secret || null);
      await queryClient.invalidateQueries({ queryKey: ["support-webhooks"] });
    },
  });
  const updateMutation = useMutation({
    mutationFn: ({ endpointId, payload }: { endpointId: string; payload: Partial<{ is_active: boolean }> }) => supportApi.updateWebhook(endpointId, payload),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-webhooks"] }),
  });
  const deleteMutation = useMutation({
    mutationFn: (endpointId: string) => supportApi.removeWebhook(endpointId),
    onSuccess: async () => { setDeleteTarget(null); await queryClient.invalidateQueries({ queryKey: ["support-webhooks"] }); },
  });
  const rotateMutation = useMutation({ mutationFn: (endpointId: string) => supportApi.rotateWebhookSecret(endpointId), onSuccess: (data) => setSecret(data.signing_secret) });
  const testMutation = useMutation({ mutationFn: (endpointId: string) => supportApi.testWebhook(endpointId), onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-webhook-deliveries"] }) });
  const recentDeliveries = useMemo(() => deliveriesQuery.data?.slice(0, 8) || [], [deliveriesQuery.data]);
  const submit = (event: FormEvent) => { event.preventDefault(); if (name.trim() && url.trim() && events.length) createMutation.mutate(); };
  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader eyebrow="Integrations" title="Signed webhooks" description="Send selected Support events to an HTTPS endpoint. Destinations are checked again before every delivery." />
      {secret ? <div className="ms-support-secret" role="status"><strong>Copy the signing secret now</strong><code>{secret}</code><button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={() => void navigator.clipboard?.writeText(secret)}>Copy</button></div> : null}
      <form className="ms-support-governance-form" onSubmit={submit}>
        <div className="ms-support-governance-grid"><label><span>Name</span><input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} placeholder="CRM events" /></label><label><span>HTTPS endpoint</span><input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/webhooks/support" /></label></div>
        <div className="ms-support-event-grid">{query.data?.supported_events.filter((item) => item !== "webhook.test").map((eventType) => <label key={eventType}><input type="checkbox" checked={events.includes(eventType)} onChange={() => setEvents(events.includes(eventType) ? events.filter((value) => value !== eventType) : [...events, eventType])} /><span>{eventType}</span></label>)}</div>
        {createMutation.isError ? <div className="ms-support-detail-error" role="alert">{parseApiError(createMutation.error, "Webhook could not be created.").message}</div> : null}
        <div className="ms-support-form-actions"><button className="ms-button ms-button--primary" type="submit" disabled={createMutation.isPending || !name.trim() || !url.trim() || !events.length}>{createMutation.isPending ? "Adding…" : "Add webhook"}</button></div>
      </form>
      <div className="ms-support-governance-list">
        {query.data?.endpoints.map((endpoint) => <article className="ms-support-governance-row" key={endpoint.id}><div><strong>{endpoint.name}</strong><span>{endpoint.url}</span><small>{endpoint.event_types.join(", ")} · {endpoint.failure_count} recent failures</small></div><div className="ms-support-governance-row__actions"><button className="ms-button ms-button--ghost ms-button--compact" type="button" onClick={() => updateMutation.mutate({ endpointId: endpoint.id, payload: { is_active: !endpoint.is_active } })}>{endpoint.is_active ? "Disable" : "Enable"}</button><button className="ms-button ms-button--ghost ms-button--compact" type="button" onClick={() => testMutation.mutate(endpoint.id)}>Test</button><button className="ms-button ms-button--ghost ms-button--compact" type="button" onClick={() => rotateMutation.mutate(endpoint.id)}>Rotate secret</button><button className="ms-button ms-button--danger ms-button--compact" type="button" onClick={() => setDeleteTarget(endpoint)}>Remove</button></div></article>)}
      </div>
      {recentDeliveries.length ? <div className="ms-support-delivery-list"><strong>Recent deliveries</strong>{recentDeliveries.map((delivery) => <div key={delivery.id}><span>{delivery.event_type}</span><span>{delivery.endpoint_name}</span><span className={`ms-page-badge${delivery.status === "succeeded" ? " ms-page-badge--strong" : ""}`}>{delivery.status}</span></div>)}</div> : null}
      <ConfirmDialog open={Boolean(deleteTarget)} title="Remove webhook?" description="Pending delivery history for this endpoint will also be removed." confirmLabel="Remove webhook" tone="danger" pending={deleteMutation.isPending} onClose={() => setDeleteTarget(null)} onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)} />
    </section>
  );
}

function DeletionHistoryCard() {
  const query = useQuery({ queryKey: ["support-deletion-requests"], queryFn: ({ signal }) => supportApi.listVisitorDeletionRequests(signal), refetchInterval: 15_000 });
  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader eyebrow="Visitor rights" title="Deletion activity" description="Deletion requests are auditable, website-scoped, and cannot remove personal Messenger data." />
      <div className="ms-support-governance-list">{query.data?.map((item) => <article className="ms-support-governance-row" key={item.id}><div><strong>{item.website_name}</strong><span>Visitor {item.visitor_external_id}</span><small>{item.source} request · {formatDateTime(item.requested_at)}</small>{item.error ? <small>{item.error}</small> : null}</div><span className={`ms-page-badge${item.status === "completed" ? " ms-page-badge--strong" : ""}`}>{item.status}</span></article>)}{!query.isLoading && !query.data?.length ? <div className="ms-support-empty">No visitor deletion requests have been recorded.</div> : null}</div>
    </section>
  );
}

export function SupportDataGovernance() {
  return <div className="ms-support-settings-stack"><PrivacySettingsCard /><ExportsCard /><WebhooksCard /><DeletionHistoryCard /></div>;
}
