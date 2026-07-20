import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type {
  SupportAgent,
  SupportBootstrap,
  SupportRoutingPolicy,
  SupportRoutingPolicyInput,
  SupportTeam,
  SupportTeamInput,
} from "../../types/support";
import { SupportBadge, SupportButton, SupportModal, SupportState, SupportToggle } from "../../support/components";

const permissionFields = [
  ["can_view_all_conversations", "View all conversations"],
  ["can_assign_conversations", "Assign conversations"],
  ["can_view_analytics", "View analytics"],
  ["can_manage_websites", "Manage websites"],
  ["can_manage_knowledge", "Manage knowledge"],
  ["can_manage_teams", "Manage teams"],
  ["can_manage_automations", "Manage automations"],
  ["can_export_data", "Export support data"],
] as const;

type PermissionKey = (typeof permissionFields)[number][0];

export function SupportAgentsPage({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const isOwner = bootstrap.role === "owner";
  const [search, setSearch] = useState("");
  const [teamFilter, setTeamFilter] = useState("all");
  const [availability, setAvailability] = useState("all");
  const [selectedAgentId, setSelectedAgentId] = useState(bootstrap.agents[0]?.id ?? "");
  const [panel, setPanel] = useState<"agent" | "teams" | "routing">("agent");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [teamOpen, setTeamOpen] = useState(false);
  const selected = bootstrap.agents.find((agent) => agent.id === selectedAgentId) ?? bootstrap.agents[0];

  const filtered = useMemo(
    () =>
      bootstrap.agents.filter((agent) => {
        const haystack = `${agent.user.display_name} ${agent.user.email ?? ""} ${agent.user.username}`.toLowerCase();
        const matchesSearch = haystack.includes(search.toLowerCase());
        const matchesAvailability = availability === "all" || agent.availability === availability;
        const matchesTeam = teamFilter === "all" || agent.team_ids.includes(teamFilter);
        return matchesSearch && matchesAvailability && matchesTeam;
      }),
    [bootstrap.agents, search, availability, teamFilter],
  );

  if (!isOwner) {
    return (
      <SupportState
        kind="empty"
        title="Owner-managed team settings"
        description="Agents can update their support availability from the existing account controls. Team membership and permissions are managed by the owner."
      />
    );
  }

  return (
    <div className="sc-agents-page">
      <header className="sc-agents-toolbar">
        <div>
          <span className="sc-page-eyebrow">Support Chat</span>
          <h1>Agents</h1>
          <p>Manage people, teams, website access, workload, and permissions.</p>
        </div>
        <div className="sc-agents-toolbar__actions">
          <label className="sc-search-field">
            <span aria-hidden="true">⌕</span>
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search agents" />
          </label>
          <select className="sc-control" value={teamFilter} onChange={(event) => setTeamFilter(event.target.value)}>
            <option value="all">All teams</option>
            {bootstrap.teams.map((team) => (
              <option value={team.id} key={team.id}>
                {team.name}
              </option>
            ))}
          </select>
          <select className="sc-control" value={availability} onChange={(event) => setAvailability(event.target.value)}>
            <option value="all">All availability</option>
            <option value="available">Available</option>
            <option value="busy">Busy</option>
            <option value="away">Away</option>
            <option value="offline">Offline</option>
          </select>
          <SupportButton onClick={() => setInviteOpen(true)}>＋ Invite agent</SupportButton>
        </div>
      </header>

      <section className="sc-agents-workspace">
        <div className="sc-agent-table-wrap">
          <table className="sc-agent-table">
            <thead>
              <tr>
                <th>Agent</th>
                <th>Teams</th>
                <th>Websites</th>
                <th>Availability</th>
                <th>Active</th>
                <th>Capacity</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((agent) => (
                <AgentRow
                  agent={agent}
                  teams={bootstrap.teams}
                  selected={agent.id === selected?.id}
                  onSelect={() => {
                    setSelectedAgentId(agent.id);
                    setPanel("agent");
                  }}
                  key={agent.id}
                />
              ))}
            </tbody>
          </table>
          {!filtered.length ? <div className="sc-table-empty">No agents match the current filters.</div> : null}
        </div>
        <aside className="sc-agent-side">
          <nav className="sc-agent-side__tabs">
            <button className={panel === "agent" ? "is-active" : ""} onClick={() => setPanel("agent")}>
              Agent details
            </button>
            <button className={panel === "teams" ? "is-active" : ""} onClick={() => setPanel("teams")}>
              Teams
            </button>
            <button className={panel === "routing" ? "is-active" : ""} onClick={() => setPanel("routing")}>
              Routing
            </button>
          </nav>
          {panel === "agent" && selected ? <AgentDetails agent={selected} bootstrap={bootstrap} /> : null}
          {panel === "teams" ? <TeamsPanel bootstrap={bootstrap} onAdd={() => setTeamOpen(true)} /> : null}
          {panel === "routing" ? <RoutingPanel bootstrap={bootstrap} /> : null}
        </aside>
      </section>

      <InviteModal open={inviteOpen} bootstrap={bootstrap} onClose={() => setInviteOpen(false)} />
      <TeamModal open={teamOpen} bootstrap={bootstrap} onClose={() => setTeamOpen(false)} />
    </div>
  );
}

function AgentRow({ agent, teams, selected, onSelect }: { agent: SupportAgent; teams: SupportTeam[]; selected: boolean; onSelect: () => void }) {
  const teamNames = teams.filter((team) => agent.team_ids.includes(team.id)).map((team) => team.name);
  return (
    <tr
      className={selected ? "is-selected" : ""}
      onClick={onSelect}
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onSelect();
      }}
    >
      <td>
        <div className="sc-agent-identity">
          <span>{agent.user.display_name.split(" ").map((part) => part[0]).join("").slice(0, 2)}</span>
          <div>
            <strong>{agent.user.display_name}</strong>
            <small>{agent.user.email || agent.user.username}</small>
          </div>
        </div>
      </td>
      <td>{teamNames.join(", ") || "Unassigned"}</td>
      <td>{agent.assigned_website_ids.length}</td>
      <td>
        <SupportBadge tone={agent.availability === "available" ? "success" : agent.availability === "away" || agent.availability === "busy" ? "warning" : "neutral"}>
          {agent.availability}
        </SupportBadge>
      </td>
      <td>{agent.active_conversation_count}</td>
      <td>{agent.max_active_conversations}</td>
      <td>
        <button className="sc-icon-action" aria-label={`Manage ${agent.user.display_name}`}>
          ⋯
        </button>
      </td>
    </tr>
  );
}

function AgentDetails({ agent, bootstrap }: { agent: SupportAgent; bootstrap: SupportBootstrap }) {
  const queryClient = useQueryClient();
  const [websiteIds, setWebsiteIds] = useState(agent.assigned_website_ids);
  const [teamIds, setTeamIds] = useState(agent.team_ids);
  const [capacity, setCapacity] = useState(agent.max_active_conversations);
  const [permissions, setPermissions] = useState<Record<PermissionKey, boolean>>(
    () => Object.fromEntries(permissionFields.map(([key]) => [key, agent[key]])) as Record<PermissionKey, boolean>,
  );
  const [error, setError] = useState<string | null>(null);
  const update = useMutation({
    mutationFn: () =>
      supportApi.updateAgent(agent.id, {
        website_ids: websiteIds,
        team_ids: teamIds,
        max_active_conversations: capacity,
        ...permissions,
      }),
    onSuccess: async () => {
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
    },
    onError: (reason) => setError(parseApiError(reason, "Agent access could not be updated.").message),
  });
  const remove = useMutation({
    mutationFn: () => supportApi.removeAgent(agent.id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
    },
    onError: (reason) => setError(parseApiError(reason, "Agent could not be deactivated.").message),
  });
  const utilization = Math.min(100, Math.round((agent.active_conversation_count / Math.max(1, capacity)) * 100));
  return (
    <div className="sc-agent-details">
      <div className="sc-agent-profile">
        <span>{agent.user.display_name.split(" ").map((part) => part[0]).join("").slice(0, 2)}</span>
        <div>
          <h2>{agent.user.display_name}</h2>
          <p>{agent.user.email || agent.user.username}</p>
        </div>
      </div>
      <div className="sc-agent-workload">
        <div>
          <strong>{agent.active_conversation_count}</strong>
          <span>Active conversations</span>
        </div>
        <div>
          <strong>{capacity}</strong>
          <span>Maximum capacity</span>
        </div>
        <div>
          <strong>{utilization}%</strong>
          <span>Utilization</span>
        </div>
      </div>
      <fieldset className="sc-agent-fieldset">
        <legend>Teams</legend>
        {bootstrap.teams.map((team) => (
          <CheckRow
            key={team.id}
            label={team.name}
            checked={teamIds.includes(team.id)}
            onChange={(checked) => setTeamIds((current) => (checked ? [...current, team.id] : current.filter((id) => id !== team.id)))}
          />
        ))}
      </fieldset>
      <fieldset className="sc-agent-fieldset">
        <legend>Website access</legend>
        {bootstrap.websites.map((website) => (
          <CheckRow
            key={website.id}
            label={website.name}
            checked={websiteIds.includes(website.id)}
            onChange={(checked) =>
              setWebsiteIds((current) => (checked ? [...current, website.id] : current.filter((id) => id !== website.id)))
            }
          />
        ))}
      </fieldset>
      <fieldset className="sc-agent-fieldset">
        <legend>Permissions</legend>
        {permissionFields.map(([key, label]) => (
          <div className="sc-agent-permission" key={key}>
            <span>{label}</span>
            <SupportToggle
              label={label}
              checked={permissions[key]}
              onChange={(checked) => setPermissions((current) => ({ ...current, [key]: checked }))}
            />
          </div>
        ))}
      </fieldset>
      <label className="sc-agent-capacity">
        <span>Maximum active conversations</span>
        <input
          type="number"
          min={1}
          max={100}
          value={capacity}
          onChange={(event) => setCapacity(Math.max(1, Math.min(100, Number(event.target.value) || 1)))}
        />
      </label>
      {error ? (
        <div className="sc-inline-error" role="alert">
          {error}
        </div>
      ) : null}
      <div className="sc-agent-actions">
        <SupportButton
          variant="danger"
          isLoading={remove.isPending}
          onClick={() => {
            if (window.confirm(`Deactivate ${agent.user.display_name}? Active assignments will remain in history.`)) remove.mutate();
          }}
        >
          Deactivate agent
        </SupportButton>
        <SupportButton isLoading={update.isPending} onClick={() => update.mutate()}>
          Save changes
        </SupportButton>
      </div>
    </div>
  );
}

function TeamsPanel({ bootstrap, onAdd }: { bootstrap: SupportBootstrap; onAdd: () => void }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const deactivate = useMutation({
    mutationFn: (teamId: string) => supportApi.deactivateTeam(teamId),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] }),
    onError: (reason) => setError(parseApiError(reason, "Team could not be deactivated.").message),
  });
  return (
    <div className="sc-teams-panel">
      <header>
        <div>
          <h2>Teams</h2>
          <p>Operational groups for access, assignment, and reporting.</p>
        </div>
        <SupportButton variant="secondary" size="sm" onClick={onAdd}>
          ＋ Add team
        </SupportButton>
      </header>
      {error ? <div className="sc-inline-error">{error}</div> : null}
      <div className="sc-team-list">
        {bootstrap.teams.map((team) => (
          <article key={team.id}>
            <div>
              <strong>{team.name}</strong>
              <small>
                {team.agent_count} agents · {team.website_ids.length} websites
              </small>
              <p>{team.description || "No description"}</p>
            </div>
            <button
              className="sc-danger-link"
              onClick={() => {
                if (window.confirm(`Deactivate ${team.name}?`)) deactivate.mutate(team.id);
              }}
            >
              Deactivate
            </button>
          </article>
        ))}
        {!bootstrap.teams.length ? (
          <SupportState
            kind="empty"
            title="No teams yet"
            description="Create a team to group agents and website access."
            actionLabel="Add team"
            onAction={onAdd}
          />
        ) : null}
      </div>
    </div>
  );
}

function CheckRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="sc-agent-check">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function InviteModal({ open, bootstrap, onClose }: { open: boolean; bootstrap: SupportBootstrap; onClose: () => void }) {
  const queryClient = useQueryClient();
  const defaultWebsiteIds = bootstrap.websites.map((item) => item.id);
  const [email, setEmail] = useState("");
  const [websites, setWebsites] = useState<string[]>(defaultWebsiteIds);
  const [teams, setTeams] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const defaults = Object.fromEntries(permissionFields.map(([key]) => [key, false])) as Record<PermissionKey, boolean>;

  const ownerEmail = bootstrap.account?.owner.email?.trim().toLowerCase() ?? "";
  const normalizedEmail = email.trim().toLowerCase();
  const isOwnerEmail = Boolean(ownerEmail && normalizedEmail && normalizedEmail === ownerEmail);
  const selectedWebsites = bootstrap.websites.filter((website) => websites.includes(website.id));
  const selectedTeams = bootstrap.teams.filter((team) => teams.includes(team.id));
  const seatSummary = bootstrap.limits?.agents;

  const resetForm = () => {
    setEmail("");
    setWebsites(defaultWebsiteIds);
    setTeams([]);
    setError(null);
  };

  useEffect(() => {
    if (!open) {
      resetForm();
      return;
    }
    setWebsites((current) => (current.length ? current : defaultWebsiteIds));
  }, [open]);

  useEffect(() => {
    if (error) setError(null);
  }, [email, websites, teams]);

  const mutation = useMutation({
    mutationFn: () =>
      supportApi.inviteAgent({
        email: email.trim(),
        website_ids: websites,
        team_ids: teams,
        max_active_conversations: 5,
        ...defaults,
      }),
    onSuccess: async () => {
      resetForm();
      onClose();
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
    },
    onError: (reason) => setError(parseApiError(reason, "Invitation could not be sent.").message),
  });

  return (
    <SupportModal
      open={open}
      size="lg"
      title="Invite support agent"
      description="Send an access invitation for Support Chat only. Personal Messenger stays completely separate."
      onClose={onClose}
      secondaryAction={{ label: "Cancel" }}
      primaryAction={{
        label: "Send invitation",
        onClick: () => mutation.mutate(),
        disabled: !email.trim() || !websites.length || isOwnerEmail,
        isLoading: mutation.isPending,
      }}
    >
      <div className="sc-agent-invite-layout">
        <div className="sc-modal-form sc-agent-invite-form">
          <label>
            <span>Email</span>
            <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="agent@company.com" />
            <small className="sc-form-hint">Invite a separate agent email. The workspace owner already has access and never uses an agent seat.</small>
          </label>

          <fieldset>
            <legend>Teams</legend>
            {bootstrap.teams.length ? (
              bootstrap.teams.map((team) => (
                <CheckRow
                  key={team.id}
                  label={team.name}
                  checked={teams.includes(team.id)}
                  onChange={(checked) => setTeams((current) => (checked ? [...current, team.id] : current.filter((id) => id !== team.id)))}
                />
              ))
            ) : (
              <div className="sc-field-empty">No teams yet. You can invite the agent now and group them later.</div>
            )}
          </fieldset>

          <fieldset>
            <legend>Websites</legend>
            {bootstrap.websites.map((website) => (
              <CheckRow
                key={website.id}
                label={website.name}
                checked={websites.includes(website.id)}
                onChange={(checked) =>
                  setWebsites((current) => (checked ? [...current, website.id] : current.filter((id) => id !== website.id)))
                }
              />
            ))}
          </fieldset>

          {isOwnerEmail ? (
            <div className="sc-inline-error" role="alert">
              Use a different email. The Support Chat owner already has access and does not consume an agent seat.
            </div>
          ) : null}
          {error ? <div className="sc-inline-error">{error}</div> : null}
        </div>

        <aside className="sc-agent-invite-summary">
          <div className="sc-agent-invite-summary__card">
            <span className="sc-page-eyebrow">Invitation summary</span>
            <h3>{email.trim() || "New agent invitation"}</h3>
            <p>Review seat usage and access scope before you send the invitation.</p>
            <div className="sc-agent-invite-summary__stats">
              <article>
                <strong>{selectedWebsites.length}</strong>
                <span>Assigned websites</span>
              </article>
              <article>
                <strong>{selectedTeams.length}</strong>
                <span>Assigned teams</span>
              </article>
              <article>
                <strong>{seatSummary ? `${seatSummary.used}/${seatSummary.limit}` : "—"}</strong>
                <span>Seats in use</span>
              </article>
            </div>
          </div>

          <div className="sc-agent-invite-summary__list">
            <div>
              <strong>Website access</strong>
              <small>{selectedWebsites.length ? selectedWebsites.map((website) => website.name).join(", ") : "Select at least one website."}</small>
            </div>
            <div>
              <strong>Team membership</strong>
              <small>{selectedTeams.length ? selectedTeams.map((team) => team.name).join(", ") : "No team selected yet."}</small>
            </div>
            <div>
              <strong>Default permissions</strong>
              <small>Starts with standard Support Chat access only. Advanced permissions can be adjusted after acceptance.</small>
            </div>
          </div>
        </aside>
      </div>
    </SupportModal>
  );
}

function TeamModal({ open, bootstrap, onClose }: { open: boolean; bootstrap: SupportBootstrap; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [agentIds, setAgentIds] = useState<string[]>([]);
  const [websiteIds, setWebsiteIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const payload: SupportTeamInput = {
    name,
    description,
    default_max_active_conversations: 5,
    agent_ids: agentIds,
    website_ids: websiteIds,
    is_active: true,
  };
  const mutation = useMutation({
    mutationFn: () => supportApi.createTeam(payload),
    onSuccess: async () => {
      onClose();
      setName("");
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
    },
    onError: (reason) => setError(parseApiError(reason, "Team could not be created.").message),
  });
  return (
    <SupportModal
      open={open}
      title="Add support team"
      description="Teams group agents and website access without adding a new account role."
      onClose={onClose}
      secondaryAction={{ label: "Cancel" }}
      primaryAction={{
        label: "Create team",
        onClick: () => mutation.mutate(),
        disabled: !name.trim(),
        isLoading: mutation.isPending,
      }}
    >
      <div className="sc-modal-form">
        <label>
          Team name
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label>
          Description
          <textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} />
        </label>
        <fieldset>
          <legend>Agents</legend>
          {bootstrap.agents.map((agent) => (
            <CheckRow
              key={agent.id}
              label={agent.user.display_name}
              checked={agentIds.includes(agent.id)}
              onChange={(checked) => setAgentIds((current) => (checked ? [...current, agent.id] : current.filter((id) => id !== agent.id)))}
            />
          ))}
        </fieldset>
        <fieldset>
          <legend>Websites</legend>
          {bootstrap.websites.map((website) => (
            <CheckRow
              key={website.id}
              label={website.name}
              checked={websiteIds.includes(website.id)}
              onChange={(checked) =>
                setWebsiteIds((current) => (checked ? [...current, website.id] : current.filter((id) => id !== website.id)))
              }
            />
          ))}
        </fieldset>
        {error ? <div className="sc-inline-error">{error}</div> : null}
      </div>
    </SupportModal>
  );
}

function RoutingPanel({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const queryClient = useQueryClient();
  const policies = useQuery({ queryKey: ["support-routing-policies"], queryFn: ({ signal }) => supportApi.listRoutingPolicies(signal) });
  if (policies.isLoading) {
    return <SupportState kind="loading" title="Loading routing settings" description="Checking assignment policies for your websites." />;
  }
  if (policies.isError) {
    return (
      <SupportState
        kind="error"
        title="Routing settings unavailable"
        description={parseApiError(policies.error, "Routing settings could not be loaded.").message}
        actionLabel="Try again"
        onAction={() => policies.refetch()}
      />
    );
  }
  return (
    <div className="sc-routing-panel">
      <header>
        <h2>Routing</h2>
        <p>Assign new conversations safely using website access, team membership, availability, and capacity.</p>
      </header>
      {(policies.data ?? []).map((policy) => (
        <RoutingPolicyCard
          key={policy.website_id}
          policy={policy}
          onSaved={() => queryClient.invalidateQueries({ queryKey: ["support-routing-policies"] })}
        />
      ))}
      {!policies.data?.length ? (
        <SupportState kind="empty" title="No routing policies" description="Create a website before configuring automatic assignment." />
      ) : null}
    </div>
  );
}

function RoutingPolicyCard({ policy, onSaved }: { policy: SupportRoutingPolicy; onSaved: () => void }) {
  const [value, setValue] = useState<SupportRoutingPolicyInput>({
    mode: policy.mode,
    least_busy_tiebreaker: policy.least_busy_tiebreaker,
    overflow_behavior: policy.overflow_behavior,
    offline_reassignment_minutes: policy.offline_reassignment_minutes,
    prefer_previous_agent: policy.prefer_previous_agent,
    enabled: policy.enabled,
  });
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => supportApi.updateRoutingPolicy(policy.website_id, value),
    onSuccess: () => {
      setError(null);
      onSaved();
    },
    onError: (reason) => setError(parseApiError(reason, "Routing settings could not be saved.").message),
  });
  return (
    <article className="sc-routing-card">
      <div className="sc-routing-card__head">
        <div>
          <strong>{policy.website_name}</strong>
          <small>Website routing policy</small>
        </div>
        <SupportToggle
          label={`Enable routing for ${policy.website_name}`}
          checked={value.enabled}
          onChange={(enabled) => setValue((current) => ({ ...current, enabled }))}
        />
      </div>
      <label>
        <span>Assignment mode</span>
        <select
          value={value.mode}
          onChange={(event) => setValue((current) => ({ ...current, mode: event.target.value as SupportRoutingPolicyInput["mode"] }))}
        >
          <option value="manual">Manual</option>
          <option value="round_robin">Round robin</option>
          <option value="least_busy">Least busy</option>
        </select>
      </label>
      <label>
        <span>Overflow handling</span>
        <select
          value={value.overflow_behavior}
          onChange={(event) =>
            setValue((current) => ({ ...current, overflow_behavior: event.target.value as SupportRoutingPolicyInput["overflow_behavior"] }))
          }
        >
          <option value="leave_unassigned">Leave unassigned</option>
          <option value="least_busy">Assign to least-busy agent</option>
          <option value="notify_owner">Notify owner</option>
        </select>
      </label>
      <label>
        <span>Offline reassignment delay</span>
        <input
          type="number"
          min={0}
          max={1440}
          value={value.offline_reassignment_minutes}
          onChange={(event) =>
            setValue((current) => ({ ...current, offline_reassignment_minutes: Number(event.target.value) || 0 }))
          }
        />
      </label>
      <div className="sc-routing-toggle">
        <span>
          <strong>Least-busy tie-breaker</strong>
          <small>Prefer the lowest workload when candidates are otherwise equal.</small>
        </span>
        <SupportToggle
          label="Least-busy tie-breaker"
          checked={value.least_busy_tiebreaker}
          onChange={(least_busy_tiebreaker) => setValue((current) => ({ ...current, least_busy_tiebreaker }))}
        />
      </div>
      <div className="sc-routing-toggle">
        <span>
          <strong>Previous-agent continuity</strong>
          <small>Keep the setting available for the later lifecycle upgrade.</small>
        </span>
        <SupportToggle
          label="Previous-agent continuity"
          checked={value.prefer_previous_agent}
          onChange={(prefer_previous_agent) => setValue((current) => ({ ...current, prefer_previous_agent }))}
        />
      </div>
      {error ? (
        <div className="sc-inline-error" role="alert">
          {error}
        </div>
      ) : null}
      <SupportButton isLoading={save.isPending} onClick={() => save.mutate()}>
        Save routing policy
      </SupportButton>
    </article>
  );
}
