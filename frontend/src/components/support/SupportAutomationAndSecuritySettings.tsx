import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type {
  SupportAutomationRuleInput,
  SupportNotificationSettings,
  SupportSecuritySettings,
} from "../../types/support";
import { MessengerSectionHeader } from "../pages/MessengerPageHeader";

const triggerOptions = [
  ["conversation_created", "Conversation created"],
  ["visitor_message", "Visitor message"],
  ["status_changed", "Status changed"],
  ["assignment_changed", "Assignment changed"],
  ["tag_added", "Tag added"],
  ["sla_due_soon", "SLA due soon"],
  ["sla_breached", "SLA breached"],
  ["follow_up_due", "Follow-up due"],
] as const;

function ToggleRow({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="ms-support-toggle-row">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span><strong>{label}</strong><small>{description}</small></span>
    </label>
  );
}

function NotificationCard() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["support-notification-settings"], queryFn: ({ signal }) => supportApi.getNotificationSettings(signal) });
  const [form, setForm] = useState<SupportNotificationSettings | null>(null);
  useEffect(() => { if (query.data) setForm(query.data); }, [query.data]);
  const mutation = useMutation({
    mutationFn: (payload: SupportNotificationSettings) => supportApi.updateNotificationSettings(payload),
    onSuccess: async (data) => {
      setForm(data);
      await queryClient.invalidateQueries({ queryKey: ["support-notification-settings"] });
    },
  });
  if (!form) return <section className="ms-page-surface ms-page-surface--padded"><div className="ms-support-empty">Loading notification controls…</div></section>;
  const toggle = (key: keyof SupportNotificationSettings, value: boolean | number) => setForm({ ...form, [key]: value });
  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader eyebrow="Notifications" title="Operational alerts" description="Choose which Support Chat events need the owner or assigned agent’s attention." />
      <div className="ms-support-settings-grid">
        <ToggleRow label="New conversations" description="Alert when a visitor starts a new support conversation." checked={form.new_conversation} onChange={(v) => toggle("new_conversation", v)} />
        <ToggleRow label="Assignment changes" description="Alert when ownership or team routing changes." checked={form.assignment_changed} onChange={(v) => toggle("assignment_changed", v)} />
        <ToggleRow label="SLA due soon" description="Warn before a service target is missed." checked={form.sla_due_soon} onChange={(v) => toggle("sla_due_soon", v)} />
        <ToggleRow label="SLA breaches" description="Escalate missed response and resolution targets." checked={form.sla_breached} onChange={(v) => toggle("sla_breached", v)} />
        <ToggleRow label="Internal mentions" description="Notify an agent when mentioned in a private note." checked={form.internal_mention} onChange={(v) => toggle("internal_mention", v)} />
        <ToggleRow label="Follow-ups" description="Notify when a scheduled follow-up becomes due." checked={form.follow_up_due} onChange={(v) => toggle("follow_up_due", v)} />
        <ToggleRow label="Daily summary" description="Send a compact support operations recap once per day." checked={form.daily_summary} onChange={(v) => toggle("daily_summary", v)} />
        <label className="ms-support-setting-line"><span><strong>Summary hour</strong><small>Uses the Support account timezone.</small></span><input type="number" min={0} max={23} value={form.daily_summary_hour} onChange={(e) => toggle("daily_summary_hour", Number(e.target.value))} /></label>
      </div>
      {mutation.isError ? <div className="ms-support-error">{parseApiError(mutation.error, "Notification settings could not be saved.").message}</div> : null}
      <div className="ms-support-settings-actions"><button className="ms-button ms-button--primary" type="button" onClick={() => mutation.mutate(form)} disabled={mutation.isPending}>{mutation.isPending ? "Saving…" : "Save notifications"}</button></div>
    </section>
  );
}

function SecurityCard() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["support-security-settings"], queryFn: ({ signal }) => supportApi.getSecuritySettings(signal) });
  const [form, setForm] = useState<SupportSecuritySettings | null>(null);
  useEffect(() => { if (query.data) setForm(query.data); }, [query.data]);
  const mutation = useMutation({
    mutationFn: (payload: SupportSecuritySettings) => supportApi.updateSecuritySettings(payload),
    onSuccess: async (data) => {
      setForm(data);
      await queryClient.invalidateQueries({ queryKey: ["support-security-settings"] });
    },
  });
  if (!form) return <section className="ms-page-surface ms-page-surface--padded"><div className="ms-support-empty">Loading security controls…</div></section>;
  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader eyebrow="Security" title="Support data protection" description="Bound attachments, sensitive actions, audit history, webhook failures, and agent sessions." />
      <div className="ms-support-settings-grid">
        <ToggleRow label="Verified identity for sensitive actions" description="Require verified visitor identity before protected account actions." checked={form.require_verified_identity_for_sensitive_actions} onChange={(v) => setForm({ ...form, require_verified_identity_for_sensitive_actions: v })} />
        <ToggleRow label="Block unverified attachments" description="Unverified visitors cannot upload files." checked={form.block_unverified_attachments} onChange={(v) => setForm({ ...form, block_unverified_attachments: v })} />
        <label className="ms-support-setting-line"><span><strong>Attachment limit</strong><small>Maximum size for each Support attachment.</small></span><input type="number" min={1} max={100} value={form.max_attachment_mb} onChange={(e) => setForm({ ...form, max_attachment_mb: Number(e.target.value) })} /></label>
        <label className="ms-support-setting-line"><span><strong>Allowed extensions</strong><small>Comma-separated; leave empty to use the platform allowlist.</small></span><input value={form.allowed_attachment_extensions.join(", ")} onChange={(e) => setForm({ ...form, allowed_attachment_extensions: e.target.value.split(",").map((v) => v.trim().replace(/^\./, "")).filter(Boolean) })} /></label>
        <label className="ms-support-setting-line"><span><strong>Audit retention</strong><small>Support audit history retention in days.</small></span><input type="number" min={30} max={3650} value={form.retain_audit_days} onChange={(e) => setForm({ ...form, retain_audit_days: Number(e.target.value) })} /></label>
        <label className="ms-support-setting-line"><span><strong>Webhook failure threshold</strong><small>Disable a failing endpoint after this many failures.</small></span><input type="number" min={3} max={100} value={form.webhook_failure_disable_threshold} onChange={(e) => setForm({ ...form, webhook_failure_disable_threshold: Number(e.target.value) })} /></label>
        <label className="ms-support-setting-line"><span><strong>Agent session timeout</strong><small>Minutes before a Support agent session must be renewed.</small></span><input type="number" min={15} max={10080} value={form.agent_session_timeout_minutes} onChange={(e) => setForm({ ...form, agent_session_timeout_minutes: Number(e.target.value) })} /></label>
      </div>
      {mutation.isError ? <div className="ms-support-error">{parseApiError(mutation.error, "Security settings could not be saved.").message}</div> : null}
      <div className="ms-support-settings-actions"><button className="ms-button ms-button--primary" type="button" onClick={() => mutation.mutate(form)} disabled={mutation.isPending}>{mutation.isPending ? "Saving…" : "Save security"}</button></div>
    </section>
  );
}

function AutomationsCard() {
  const queryClient = useQueryClient();
  const rules = useQuery({ queryKey: ["support-automation-rules"], queryFn: ({ signal }) => supportApi.listAutomationRules(signal) });
  const executions = useQuery({ queryKey: ["support-automation-executions"], queryFn: ({ signal }) => supportApi.listAutomationExecutions(signal) });
  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState("conversation_created");
  const [actionType, setActionType] = useState("notify_owner");
  const [actionValue, setActionValue] = useState("");
  const create = useMutation({
    mutationFn: (payload: SupportAutomationRuleInput) => supportApi.createAutomationRule(payload),
    onSuccess: async () => {
      setName(""); setActionValue("");
      await queryClient.invalidateQueries({ queryKey: ["support-automation-rules"] });
    },
  });
  const update = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) => supportApi.updateAutomationRule(id, { is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["support-automation-rules"] }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => supportApi.deleteAutomationRule(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["support-automation-rules"] }),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    create.mutate({
      name: name.trim(),
      description: "",
      trigger,
      conditions: [],
      actions: [{ type: actionType, value: actionValue || name.trim() }],
      is_active: true,
      priority: 100,
      stop_processing: false,
      execution_limit: 10,
    });
  };
  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader eyebrow="Automation" title="Safe workflow rules" description="Rules are bounded, idempotent, auditable, and cannot execute arbitrary code." />
      <form className="ms-support-automation-form" onSubmit={submit}>
        <label><span>Rule name</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Escalate SLA breach" /></label>
        <label><span>Trigger</span><select value={trigger} onChange={(e) => setTrigger(e.target.value)}>{triggerOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label><span>Action</span><select value={actionType} onChange={(e) => setActionType(e.target.value)}><option value="notify_owner">Notify owner</option><option value="notify_agent">Notify assigned agent</option><option value="set_priority">Set priority</option><option value="set_follow_up">Set follow-up minutes</option><option value="trigger_webhook">Trigger webhook</option></select></label>
        <label><span>Action value</span><input value={actionValue} onChange={(e) => setActionValue(e.target.value)} placeholder="Optional value" /></label>
        <button className="ms-button ms-button--primary" type="submit" disabled={create.isPending || !name.trim()}>{create.isPending ? "Creating…" : "Add rule"}</button>
      </form>
      {create.isError ? <div className="ms-support-error">{parseApiError(create.error, "Automation rule could not be created.").message}</div> : null}
      <div className="ms-support-automation-list">
        {(rules.data ?? []).map((rule) => (
          <article key={rule.id}>
            <div><strong>{rule.name}</strong><span>{rule.trigger.replaceAll("_", " ")}</span><small>{rule.actions.length} action{rule.actions.length === 1 ? "" : "s"} · priority {rule.priority}</small></div>
            <ToggleRow label={rule.is_active ? "Active" : "Paused"} description={rule.stop_processing ? "Stops later rules" : "Continues to later rules"} checked={rule.is_active} onChange={(value) => update.mutate({ id: rule.id, is_active: value })} />
            <button className="ms-button ms-button--secondary" type="button" onClick={() => remove.mutate(rule.id)} disabled={remove.isPending}>Remove</button>
          </article>
        ))}
        {!rules.isLoading && !(rules.data ?? []).length ? <div className="ms-support-empty">No automation rules have been created.</div> : null}
      </div>
      <div className="ms-support-automation-log">
        <h3>Recent executions</h3>
        {(executions.data ?? []).slice(0, 8).map((item) => <div key={item.id}><span>{item.rule.name}</span><strong className={`is-${item.status}`}>{item.status}</strong><small>{item.actions_executed} actions · {item.duration_ms}ms</small></div>)}
        {!executions.isLoading && !(executions.data ?? []).length ? <div className="ms-support-empty">No automation executions yet.</div> : null}
      </div>
    </section>
  );
}

export function SupportAutomationAndSecuritySettings() {
  return <><NotificationCard /><AutomationsCard /><SecurityCard /></>;
}
