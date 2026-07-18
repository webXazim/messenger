import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import { supportApi } from "../api/support";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { MessengerPageHeader, MessengerSectionHeader } from "../components/pages/MessengerPageHeader";
import { UserAvatar } from "../components/UserAvatar";
import { SupportWebsiteManager } from "../components/support/SupportWebsiteManager";
import { SupportInbox } from "../components/support/SupportInbox";
import { SupportWorkflowSettings } from "../components/support/SupportWorkflowSettings";
import { SupportServiceOperationsSettings } from "../components/support/SupportServiceOperationsSettings";
import { SupportFeedbackSettings } from "../components/support/SupportFeedbackSettings";
import { SupportAnalytics } from "../components/support/SupportAnalytics";
import { SupportKnowledgeBase } from "../components/support/SupportKnowledgeBase";
import { SupportDataGovernance } from "../components/support/SupportDataGovernance";
import { SupportCallSettings } from "../components/support/SupportCallSettings";
import { parseApiError } from "../lib/apiErrors";
import { SUPPORT_PLANS_URL } from "../lib/config";
import type {
  SupportAgent,
  SupportAgentInvitation,
  SupportAvailability,
  SupportBootstrap,
  SupportWebsite,
} from "../types/support";

function sectionFromPath(pathname: string) {
  if (pathname.startsWith("/support/inbox")) return "inbox";
  if (pathname.startsWith("/support/websites")) return "websites";
  if (pathname.startsWith("/support/agents")) return "agents";
  if (pathname.startsWith("/support/analytics")) return "analytics";
  if (pathname.startsWith("/support/knowledge")) return "knowledge";
  if (pathname.startsWith("/support/settings")) return "settings";
  return "inbox";
}

function formatDate(value?: string | null) {
  if (!value) return "Not set";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not set";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function SupportAccessState({ bootstrap }: { bootstrap: SupportBootstrap }) {
  if (!bootstrap.feature_enabled || bootstrap.access === "disabled") {
    return (
      <div className="ms-support-access-state">
        <span className="ms-support-access-state__mark" aria-hidden="true">S</span>
        <div>
          <h1>Support Chat is not enabled</h1>
          <p>This deployment is still protecting Messenger while the Support Chat product is prepared.</p>
        </div>
      </div>
    );
  }

  if (bootstrap.access === "upgrade_required") {
    return (
      <div className="ms-support-access-state">
        <span className="ms-support-access-state__mark" aria-hidden="true">S</span>
        <div>
          <div className="ms-support-access-state__eyebrow">Premium product</div>
          <h1>Support Chat</h1>
          <p>Add website chat, assign company agents, and receive visitor messages without changing your personal Messenger.</p>
          <a className="ms-button ms-button--primary" href={SUPPORT_PLANS_URL}>View Support Chat plans</a>
        </div>
      </div>
    );
  }

  return (
    <div className="ms-support-access-state">
      <span className="ms-support-access-state__mark" aria-hidden="true">!</span>
      <div>
        <div className="ms-support-access-state__eyebrow">Support access restricted</div>
        <h1>Renew Support Chat</h1>
        <p>Your personal Messenger remains available. Renew the Support Chat plan to manage websites and visitor conversations.</p>
        <a className="ms-button ms-button--primary" href={SUPPORT_PLANS_URL}>Open billing</a>
      </div>
    </div>
  );
}

function UsageLine({ label, used, limit, detail }: { label: string; used: number; limit: number; detail?: string }) {
  const percentage = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  return (
    <div className="ms-support-usage">
      <div className="ms-support-usage__copy">
        <strong>{label}</strong>
        <span>{used} of {limit}</span>
      </div>
      <div className="ms-support-usage__track" aria-label={`${label}: ${used} of ${limit}`}>
        <span style={{ width: `${percentage}%` }} />
      </div>
      {detail ? <small className="ms-support-usage__detail">{detail}</small> : null}
    </div>
  );
}

function WebsiteList({ websites }: { websites: SupportWebsite[] }) {
  if (!websites.length) return <div className="ms-support-empty">No websites have been added to Support Chat.</div>;
  return (
    <div className="ms-page-list">
      {websites.map((website) => (
        <div className="ms-page-row" key={website.id}>
          <div className="ms-page-row__copy">
            <strong>{website.name}</strong>
            <span>{website.domain}</span>
          </div>
          <span className={`ms-page-badge${website.widget_enabled ? " ms-page-badge--strong" : ""}`}>
            {website.widget_enabled ? "Widget active" : "Widget off"}
          </span>
        </div>
      ))}
    </div>
  );
}

function WebsiteChoices({
  websites,
  selected,
  onChange,
  disabled = false,
}: {
  websites: SupportWebsite[];
  selected: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
}) {
  const toggle = (websiteId: string) => {
    onChange(selected.includes(websiteId) ? selected.filter((id) => id !== websiteId) : [...selected, websiteId]);
  };
  return (
    <div className="ms-support-website-choices">
      {websites.map((website) => (
        <label className="ms-support-choice" key={website.id}>
          <input type="checkbox" checked={selected.includes(website.id)} onChange={() => toggle(website.id)} disabled={disabled} />
          <span><strong>{website.name}</strong><small>{website.domain}</small></span>
        </label>
      ))}
    </div>
  );
}

function PermissionChoices({
  viewAll,
  assign,
  analytics,
  onViewAll,
  onAssign,
  onAnalytics,
  disabled = false,
}: {
  viewAll: boolean;
  assign: boolean;
  analytics: boolean;
  onViewAll: (value: boolean) => void;
  onAssign: (value: boolean) => void;
  onAnalytics: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="ms-support-permission-grid">
      <label className="ms-support-toggle-row">
        <input type="checkbox" checked={viewAll} onChange={(event) => onViewAll(event.target.checked)} disabled={disabled} />
        <span><strong>View all conversations</strong><small>Within the websites assigned to this agent.</small></span>
      </label>
      <label className="ms-support-toggle-row">
        <input type="checkbox" checked={assign} onChange={(event) => onAssign(event.target.checked)} disabled={disabled} />
        <span><strong>Assign conversations</strong><small>Transfer and assign support chats to agents.</small></span>
      </label>
      <label className="ms-support-toggle-row">
        <input type="checkbox" checked={analytics} onChange={(event) => onAnalytics(event.target.checked)} disabled={disabled} />
        <span><strong>View analytics</strong><small>Access support performance and response reports.</small></span>
      </label>
    </div>
  );
}

function OverviewSection({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const agentLimit = bootstrap.limits?.agents;
  return (
    <>
      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader eyebrow="Usage" title="Current plan capacity" description="Website and agent limits apply only to Support Chat. Messenger remains independent." />
        <div className="ms-support-usage-grid">
          <UsageLine label="Websites" used={bootstrap.limits?.websites.used ?? 0} limit={bootstrap.limits?.websites.limit ?? 0} />
          <UsageLine
            label="Agent seats"
            used={agentLimit?.used ?? 0}
            limit={agentLimit?.limit ?? 0}
            detail={`${agentLimit?.active ?? 0} active · ${agentLimit?.pending ?? 0} pending`}
          />
        </div>
      </section>

      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader eyebrow="Websites" title="Connected support websites" description="Each website keeps its visitor conversations separate while Support Chat can notify you across all of them." actions={<Link className="ms-button ms-button--ghost ms-button--compact" to="/support/websites">Manage websites</Link>} />
        <WebsiteList websites={bootstrap.websites.slice(0, 4)} />
      </section>

      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader eyebrow="Team" title="Support agents" description="The owner has full control. Agents only handle the websites and support work assigned to them." actions={<Link className="ms-button ms-button--ghost ms-button--compact" to="/support/agents">Manage agents</Link>} />
        {bootstrap.agents.length ? (
          <div className="ms-support-agent-strip">
            {bootstrap.agents.slice(0, 6).map((agent) => (
              <div className="ms-support-agent-chip" key={agent.id}>
                <UserAvatar person={{ display_name: agent.user.display_name, avatar: agent.user.avatar }} size="sm" decorative />
                <span><strong>{agent.user.display_name}</strong><small>{agent.availability}</small></span>
              </div>
            ))}
          </div>
        ) : <div className="ms-support-empty">No active agents have been added yet.</div>}
      </section>
    </>
  );
}

function WebsitesSection({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const isOwner = bootstrap.role === "owner";
  const atLimit = (bootstrap.limits?.websites.used ?? 0) >= (bootstrap.limits?.websites.limit ?? 0);

  const createMutation = useMutation({
    mutationFn: () => supportApi.createWebsite({ name: name.trim(), domain: domain.trim() }),
    onMutate: () => setError(null),
    onSuccess: async () => {
      setName("");
      setDomain("");
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
    },
    onError: (mutationError) => setError(parseApiError(mutationError, "The website could not be added.").message),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !domain.trim() || createMutation.isPending || atLimit) return;
    createMutation.mutate();
  };

  return (
    <>
      {isOwner ? (
        <section className="ms-page-surface ms-page-surface--padded">
          <MessengerSectionHeader eyebrow="Add website" title="Connect another website" description={`Your plan allows ${bootstrap.limits?.websites.limit ?? 0} active website${(bootstrap.limits?.websites.limit ?? 0) === 1 ? "" : "s"}.`} />
          <form className="ms-support-website-form" onSubmit={submit}>
            <label><span>Website name</span><input className="ms-page-field" value={name} onChange={(event) => setName(event.target.value)} placeholder="Products website" disabled={atLimit} /></label>
            <label><span>Domain</span><input className="ms-page-field" value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="products.example.com" inputMode="url" disabled={atLimit} /></label>
            <button className="ms-button ms-button--primary" type="submit" disabled={atLimit || createMutation.isPending || !name.trim() || !domain.trim()}>{createMutation.isPending ? "Adding…" : atLimit ? "Plan limit reached" : "Add website"}</button>
          </form>
          {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
        </section>
      ) : null}

      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader eyebrow="Website separation" title={isOwner ? "Your support websites" : "Assigned websites"} description={isOwner ? "Configure each website widget independently. Visitor sessions, future chats, media, permissions, and unread state remain scoped to that website." : "Visitor chats, media, permissions, and unread state remain scoped to the websites assigned to you."} />
        {isOwner ? (
          bootstrap.websites.length ? <div className="ms-support-website-manager-list">{bootstrap.websites.map((website) => <SupportWebsiteManager website={website} key={website.id} />)}</div> : <div className="ms-support-empty">No websites have been added to Support Chat.</div>
        ) : <WebsiteList websites={bootstrap.websites} />}
      </section>
    </>
  );
}

function InviteAgentSection({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [websiteIds, setWebsiteIds] = useState<string[]>(() => bootstrap.websites.map((website) => website.id));
  const [maxActive, setMaxActive] = useState(5);
  const [viewAll, setViewAll] = useState(false);
  const [assign, setAssign] = useState(false);
  const [analytics, setAnalytics] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const atLimit = (bootstrap.limits?.agents.used ?? 0) >= (bootstrap.limits?.agents.limit ?? 0);

  useEffect(() => {
    setWebsiteIds((current) => current.filter((id) => bootstrap.websites.some((website) => website.id === id)));
  }, [bootstrap.websites]);

  const mutation = useMutation({
    mutationFn: () => supportApi.inviteAgent({
      email: email.trim(),
      website_ids: websiteIds,
      max_active_conversations: maxActive,
      can_view_all_conversations: viewAll,
      can_assign_conversations: assign,
      can_view_analytics: analytics,
    }),
    onMutate: () => { setError(null); setSuccess(null); },
    onSuccess: async (invitation) => {
      setSuccess(`Invitation sent to ${invitation.email}.`);
      setEmail("");
      setViewAll(false);
      setAssign(false);
      setAnalytics(false);
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
    },
    onError: (reason) => setError(parseApiError(reason, "The invitation could not be sent.").message),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim() || !websiteIds.length || atLimit || mutation.isPending) return;
    mutation.mutate();
  };

  if (!bootstrap.websites.length) {
    return (
      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader eyebrow="Invite agents" title="Add a website first" description="Every support agent must be assigned to at least one website." actions={<Link className="ms-button ms-button--primary ms-button--compact" to="/support/websites">Add website</Link>} />
      </section>
    );
  }

  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader eyebrow="Invite agent" title="Add company personnel" description={`Pending invitations reserve agent seats. Your plan allows ${bootstrap.limits?.agents.limit ?? 0} agent${(bootstrap.limits?.agents.limit ?? 0) === 1 ? "" : "s"}; the owner is included separately.`} />
      <form className="ms-support-agent-form" onSubmit={submit}>
        <div className="ms-support-form-grid">
          <label><span>Email address</span><input className="ms-page-field" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="agent@company.com" disabled={atLimit} required /></label>
          <label><span>Maximum active chats</span><input className="ms-page-field" type="number" min={1} max={100} value={maxActive} onChange={(event) => setMaxActive(Math.max(1, Math.min(100, Number(event.target.value) || 1)))} disabled={atLimit} /></label>
        </div>
        <fieldset className="ms-support-fieldset" disabled={atLimit}>
          <legend>Website access</legend>
          <WebsiteChoices websites={bootstrap.websites} selected={websiteIds} onChange={setWebsiteIds} disabled={atLimit} />
        </fieldset>
        <fieldset className="ms-support-fieldset" disabled={atLimit}>
          <legend>Additional permissions</legend>
          <PermissionChoices viewAll={viewAll} assign={assign} analytics={analytics} onViewAll={setViewAll} onAssign={setAssign} onAnalytics={setAnalytics} disabled={atLimit} />
        </fieldset>
        {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
        {success ? <div className="ms-support-success" role="status">{success}</div> : null}
        <div className="ms-support-form-actions">
          <button className="ms-button ms-button--primary" type="submit" disabled={atLimit || mutation.isPending || !email.trim() || !websiteIds.length}>
            {mutation.isPending ? "Sending invitation…" : atLimit ? "Agent limit reached" : "Invite agent"}
          </button>
        </div>
      </form>
    </section>
  );
}

function PendingInvitationRow({ invitation }: { invitation: SupportAgentInvitation }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const resend = useMutation({
    mutationFn: () => supportApi.resendAgentInvitation(invitation.id),
    onMutate: () => setError(null),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] }); },
    onError: (reason) => setError(parseApiError(reason, "The invitation could not be resent.").message),
  });
  const revoke = useMutation({
    mutationFn: () => supportApi.revokeAgentInvitation(invitation.id),
    onMutate: () => setError(null),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] }); },
    onError: (reason) => setError(parseApiError(reason, "The invitation could not be revoked.").message),
  });
  const busy = resend.isPending || revoke.isPending;
  return (
    <div className="ms-support-invitation-row">
      <div className="ms-page-row__copy">
        <strong>{invitation.email}</strong>
        <span>{invitation.assigned_websites.map((website) => website.name).join(", ") || "No website access"}</span>
        <small>{invitation.status === "expired" ? "Expired" : `Expires ${formatDate(invitation.expires_at)}`} · sent {invitation.send_count} time{invitation.send_count === 1 ? "" : "s"}</small>
        {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
      </div>
      <div className="ms-page-actions ms-page-actions--wrap">
        <span className={`ms-page-badge${invitation.status === "pending" ? " ms-page-badge--strong" : ""}`}>{invitation.status}</span>
        <button className="ms-button ms-button--ghost ms-button--compact" type="button" onClick={() => resend.mutate()} disabled={busy}>{resend.isPending ? "Sending…" : "Resend"}</button>
        <button className="ms-button ms-button--danger ms-button--compact" type="button" onClick={() => revoke.mutate()} disabled={busy}>{revoke.isPending ? "Revoking…" : "Revoke"}</button>
      </div>
    </div>
  );
}

function AgentManagementCard({ agent, websites }: { agent: SupportAgent; websites: SupportWebsite[] }) {
  const queryClient = useQueryClient();
  const [websiteIds, setWebsiteIds] = useState(agent.assigned_website_ids);
  const [maxActive, setMaxActive] = useState(agent.max_active_conversations);
  const [viewAll, setViewAll] = useState(agent.can_view_all_conversations);
  const [assign, setAssign] = useState(agent.can_assign_conversations);
  const [analytics, setAnalytics] = useState(agent.can_view_analytics);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  useEffect(() => {
    setWebsiteIds(agent.assigned_website_ids);
    setMaxActive(agent.max_active_conversations);
    setViewAll(agent.can_view_all_conversations);
    setAssign(agent.can_assign_conversations);
    setAnalytics(agent.can_view_analytics);
  }, [agent]);

  const save = useMutation({
    mutationFn: () => supportApi.updateAgent(agent.id, {
      website_ids: websiteIds,
      max_active_conversations: maxActive,
      can_view_all_conversations: viewAll,
      can_assign_conversations: assign,
      can_view_analytics: analytics,
    }),
    onMutate: () => { setError(null); setSuccess(null); },
    onSuccess: async () => { setSuccess("Agent access updated."); await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] }); },
    onError: (reason) => setError(parseApiError(reason, "The agent could not be updated.").message),
  });
  const remove = useMutation({
    mutationFn: () => supportApi.removeAgent(agent.id),
    onMutate: () => { setError(null); setSuccess(null); },
    onSuccess: async () => { setConfirmRemove(false); await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] }); },
    onError: (reason) => setError(parseApiError(reason, "The agent could not be removed.").message),
  });

  return (
    <details className="ms-support-agent-card">
      <summary>
        <span className="ms-support-agent-row__identity">
          <UserAvatar person={{ display_name: agent.user.display_name, avatar: agent.user.avatar }} size="sm" decorative />
          <span className="ms-page-row__copy"><strong>{agent.user.display_name}</strong><span>{agent.user.email || agent.user.username}</span></span>
        </span>
        <span className="ms-page-actions ms-page-actions--wrap">
          <span className="ms-page-badge">{agent.assigned_website_ids.length} website{agent.assigned_website_ids.length === 1 ? "" : "s"}</span>
          <span className={`ms-page-badge${agent.availability === "available" ? " ms-page-badge--strong" : ""}`}>{agent.availability}</span>
          <span className="ms-support-disclosure" aria-hidden="true">⌄</span>
        </span>
      </summary>
      <div className="ms-support-agent-card__body">
        <div className="ms-support-agent-config-grid">
          <fieldset className="ms-support-fieldset">
            <legend>Assigned websites</legend>
            <WebsiteChoices websites={websites} selected={websiteIds} onChange={setWebsiteIds} disabled={save.isPending || remove.isPending} />
          </fieldset>
          <fieldset className="ms-support-fieldset">
            <legend>Agent permissions</legend>
            <PermissionChoices viewAll={viewAll} assign={assign} analytics={analytics} onViewAll={setViewAll} onAssign={setAssign} onAnalytics={setAnalytics} disabled={save.isPending || remove.isPending} />
          </fieldset>
        </div>
        <label className="ms-support-capacity-field"><span>Maximum active chats</span><input className="ms-page-field" type="number" min={1} max={100} value={maxActive} onChange={(event) => setMaxActive(Math.max(1, Math.min(100, Number(event.target.value) || 1)))} /></label>
        {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
        {success ? <div className="ms-support-success" role="status">{success}</div> : null}
        <div className="ms-support-form-actions ms-support-form-actions--split">
          <button className="ms-button ms-button--danger" type="button" onClick={() => setConfirmRemove(true)} disabled={remove.isPending}>Remove agent</button>
          <button className="ms-button ms-button--primary" type="button" onClick={() => save.mutate()} disabled={save.isPending || remove.isPending}>{save.isPending ? "Saving…" : "Save access"}</button>
        </div>
      </div>
      <ConfirmDialog
        open={confirmRemove}
        title={`Remove ${agent.user.display_name}?`}
        description="This immediately removes Support Chat access and frees the plan seat. Personal Messenger remains unchanged."
        confirmLabel="Remove agent"
        tone="danger"
        pending={remove.isPending}
        error={error}
        onConfirm={() => remove.mutate()}
        onClose={() => { if (!remove.isPending) setConfirmRemove(false); }}
      />
    </details>
  );
}

function AgentAvailabilitySection({ agent }: { agent: SupportAgent }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (availability: SupportAvailability) => supportApi.updateMyAvailability(availability),
    onMutate: () => setError(null),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] }); },
    onError: (reason) => setError(parseApiError(reason, "Availability could not be updated.").message),
  });
  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader eyebrow="Agent status" title="Support availability" description="This is separate from Messenger online status and controls future support assignment." />
      <div className="ms-support-availability-options" role="group" aria-label="Support availability">
        {(["available", "busy", "away", "offline"] as SupportAvailability[]).map((availability) => (
          <button className={`ms-support-availability${agent.availability === availability ? " is-active" : ""}`} type="button" key={availability} onClick={() => mutation.mutate(availability)} disabled={mutation.isPending}>
            {availability}
          </button>
        ))}
      </div>
      {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
    </section>
  );
}

function AgentsSection({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const isOwner = bootstrap.role === "owner";
  if (!isOwner) {
    const agent = bootstrap.agents[0];
    return (
      <>
        {agent ? <AgentAvailabilitySection agent={agent} /> : null}
        <section className="ms-page-surface ms-page-surface--padded">
          <MessengerSectionHeader eyebrow="Website access" title="Assigned support websites" description="Only these websites and their visitor conversations are available to your agent account." />
          <WebsiteList websites={bootstrap.websites} />
        </section>
      </>
    );
  }

  return (
    <>
      <InviteAgentSection bootstrap={bootstrap} />
      {bootstrap.invitations.length ? (
        <section className="ms-page-surface ms-page-surface--padded">
          <MessengerSectionHeader eyebrow="Invitations" title="Pending agent access" description="Pending and expired invitations remain visible so the owner can resend or revoke them." />
          <div className="ms-support-invitation-list">{bootstrap.invitations.map((invitation) => <PendingInvitationRow invitation={invitation} key={invitation.id} />)}</div>
        </section>
      ) : null}
      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader eyebrow="Active agents" title="Support team" description="Agents are separate from Messenger friends and only access the websites assigned here." />
        {bootstrap.agents.length ? (
          <div className="ms-support-agent-list">{bootstrap.agents.map((agent) => <AgentManagementCard agent={agent} websites={bootstrap.websites} key={agent.id} />)}</div>
        ) : <div className="ms-support-empty">No active support agents have been added.</div>}
      </section>
    </>
  );
}

function SettingsSection({ bootstrap }: { bootstrap: SupportBootstrap }) {
  return (
    <div className="ms-support-settings-stack">
      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader eyebrow="Support product" title="Plan and access" description="These settings affect Support Chat only and cannot block or change personal Messenger." />
        <div className="ms-page-list">
          <div className="ms-page-row"><div className="ms-page-row__copy"><strong>Plan</strong><span>{bootstrap.account?.plan_code || "Managed plan"}</span></div><span className="ms-page-badge ms-page-badge--strong">{bootstrap.account?.status}</span></div>
          <div className="ms-page-row"><div className="ms-page-row__copy"><strong>Current period ends</strong><span>{formatDate(bootstrap.account?.current_period_end)}</span></div></div>
          <div className="ms-page-row"><div className="ms-page-row__copy"><strong>Owner</strong><span>{bootstrap.account?.owner.display_name}</span></div></div>
        </div>
      </section>
      {bootstrap.role === "owner" ? (
        <>
          <SupportCallSettings />
          <SupportServiceOperationsSettings />
          <SupportFeedbackSettings />
          <SupportWorkflowSettings bootstrap={bootstrap} />
          <SupportDataGovernance />
        </>
      ) : (
        <section className="ms-page-surface ms-page-surface--padded">
          <MessengerSectionHeader eyebrow="Support workflow" title="Shared team tools" description="The owner manages tags and canned replies. Agents can use them in the Support inbox." />
        </section>
      )}
    </div>
  );
}

export function SupportChatPage() {
  const location = useLocation();
  const section = sectionFromPath(location.pathname);
  const bootstrapQuery = useQuery({ queryKey: ["support-bootstrap"], queryFn: ({ signal }) => supportApi.bootstrap(signal), staleTime: 30_000 });

  const title = useMemo(() => {
    if (section === "inbox") return "Inbox";
    if (section === "websites") return "Websites";
    if (section === "agents") return "Agents";
    if (section === "analytics") return "Analytics";
    if (section === "knowledge") return "Knowledge";
    if (section === "settings") return "Support settings";
    return "Support Chat";
  }, [section]);

  if (bootstrapQuery.isLoading) return <div className="ms-support-loading" role="status"><span aria-hidden="true" />Loading Support Chat…</div>;
  if (bootstrapQuery.isError || !bootstrapQuery.data) {
    return (
      <div className="ms-support-access-state" role="alert">
        <span className="ms-support-access-state__mark" aria-hidden="true">!</span>
        <div><h1>Support Chat could not be loaded</h1><p>{parseApiError(bootstrapQuery.error, "Check your connection and try again.").message}</p><button className="ms-button ms-button--primary" type="button" onClick={() => void bootstrapQuery.refetch()}>Retry</button></div>
      </div>
    );
  }

  const bootstrap = bootstrapQuery.data;
  if (bootstrap.access !== "active") return <SupportAccessState bootstrap={bootstrap} />;

  return (
    <div className="ms-workspace-page ms-support-page">
      <MessengerPageHeader
        eyebrow="Support Chat"
        title={title}
        description="Website visitor support is isolated from personal Messenger while using the same reliable responsive application foundation."
        stats={bootstrap.limits ? [
          { label: "Websites", value: `${bootstrap.limits.websites.used}/${bootstrap.limits.websites.limit}` },
          { label: "Agent seats", value: `${bootstrap.limits.agents.used}/${bootstrap.limits.agents.limit}` },
        ] : []}
      />
      {section === "inbox" ? <SupportInbox bootstrap={bootstrap} /> : null}
      {section === "websites" ? <WebsitesSection bootstrap={bootstrap} /> : null}
      {section === "agents" ? <AgentsSection bootstrap={bootstrap} /> : null}
      {section === "analytics" ? <SupportAnalytics bootstrap={bootstrap} /> : null}
      {section === "knowledge" ? <SupportKnowledgeBase bootstrap={bootstrap} /> : null}
      {section === "settings" ? <SettingsSection bootstrap={bootstrap} /> : null}
    </div>
  );
}
