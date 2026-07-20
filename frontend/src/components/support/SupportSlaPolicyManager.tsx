import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type {
  SupportServiceSettings,
  SupportSlaPolicyInput,
} from "../../types/support";

const priorities = ["low", "normal", "high", "urgent"] as const;

function targets(minutes: number) {
  return Object.fromEntries(priorities.map((priority) => [priority, minutes])) as SupportServiceSettings["first_response_targets"];
}

export function SupportSlaPolicyManager({
  defaults,
}: {
  defaults: SupportServiceSettings;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [scopeType, setScopeType] = useState<"website" | "team">("website");
  const [scopeId, setScopeId] = useState("");
  const [firstMinutes, setFirstMinutes] = useState(defaults.first_response_targets.normal);
  const [nextMinutes, setNextMinutes] = useState(defaults.next_response_targets.normal);
  const [resolutionMinutes, setResolutionMinutes] = useState(defaults.resolution_targets.normal);
  const [message, setMessage] = useState("");

  const policies = useQuery({
    queryKey: ["support-sla-policies"],
    queryFn: ({ signal }) => supportApi.listSlaPolicies(signal),
  });
  const websites = useQuery({
    queryKey: ["support-websites"],
    queryFn: ({ signal }) => supportApi.listWebsites(signal),
  });
  const teams = useQuery({
    queryKey: ["support-teams"],
    queryFn: ({ signal }) => supportApi.listTeams(signal),
  });

  const scopes = useMemo(
    () => (scopeType === "website" ? websites.data ?? [] : teams.data ?? []),
    [scopeType, websites.data, teams.data],
  );

  const createMutation = useMutation({
    mutationFn: (payload: SupportSlaPolicyInput) => supportApi.createSlaPolicy(payload),
    onSuccess: async () => {
      setName("");
      setScopeId("");
      setMessage("SLA override created.");
      await queryClient.invalidateQueries({ queryKey: ["support-sla-policies"] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      supportApi.updateSlaPolicy(id, { is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["support-sla-policies"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => supportApi.deleteSlaPolicy(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["support-sla-policies"] }),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !scopeId || createMutation.isPending) return;
    createMutation.mutate({
      name: name.trim(),
      website: scopeType === "website" ? scopeId : null,
      team: scopeType === "team" ? scopeId : null,
      is_active: true,
      first_response_targets: targets(firstMinutes),
      next_response_targets: targets(nextMinutes),
      resolution_targets: targets(resolutionMinutes),
      due_soon_minutes: defaults.due_soon_minutes,
      pause_while_waiting_customer: defaults.pause_while_waiting_customer,
      pause_resolution_while_snoozed: defaults.pause_resolution_while_snoozed,
      alert_owner: defaults.alert_owner,
      alert_assigned_agent: defaults.alert_assigned_agent,
      escalate_on_breach: defaults.escalate_on_breach,
      escalation_team: defaults.escalation_team ?? null,
    });
  };

  return (
    <section className="ms-support-sla-policies">
      <div className="ms-support-workflow-heading">
        <div>
          <span>Policy overrides</span>
          <h2>Website and team SLA rules</h2>
          <p>Website rules take priority over team rules. Account settings remain the fallback.</p>
        </div>
      </div>

      <form className="ms-support-sla-policy-form" onSubmit={submit}>
        <label>
          <span>Policy name</span>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Premium website SLA" />
        </label>
        <label>
          <span>Scope</span>
          <select value={scopeType} onChange={(event) => { setScopeType(event.target.value as "website" | "team"); setScopeId(""); }}>
            <option value="website">Website</option>
            <option value="team">Team</option>
          </select>
        </label>
        <label>
          <span>{scopeType === "website" ? "Website" : "Team"}</span>
          <select value={scopeId} onChange={(event) => setScopeId(event.target.value)}>
            <option value="">Choose…</option>
            {scopes.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label><span>First response</span><input type="number" min={1} max={43200} value={firstMinutes} onChange={(event) => setFirstMinutes(Number(event.target.value) || 1)} /><small>minutes</small></label>
        <label><span>Next response</span><input type="number" min={1} max={43200} value={nextMinutes} onChange={(event) => setNextMinutes(Number(event.target.value) || 1)} /><small>minutes</small></label>
        <label><span>Resolution</span><input type="number" min={1} max={43200} value={resolutionMinutes} onChange={(event) => setResolutionMinutes(Number(event.target.value) || 1)} /><small>minutes</small></label>
        <button className="ms-button ms-button--primary" type="submit" disabled={!name.trim() || !scopeId || createMutation.isPending}>
          {createMutation.isPending ? "Creating…" : "Add SLA override"}
        </button>
      </form>

      {createMutation.isError ? <div className="ms-page-error" role="alert">{parseApiError(createMutation.error, "SLA override could not be created.").message}</div> : null}
      {message ? <div className="ms-support-success" role="status">{message}</div> : null}

      <div className="ms-support-sla-policy-list">
        {(policies.data ?? []).map((policy) => (
          <article key={policy.id}>
            <div>
              <strong>{policy.name}</strong>
              <span>{policy.website_name ?? policy.team_name}</span>
              <small>
                {policy.first_response_targets.normal}m first · {policy.next_response_targets.normal}m next · {policy.resolution_targets.normal}m resolution
              </small>
            </div>
            <label className="ms-support-toggle-row">
              <input
                type="checkbox"
                checked={policy.is_active}
                disabled={updateMutation.isPending}
                onChange={(event) => updateMutation.mutate({ id: policy.id, is_active: event.target.checked })}
              />
              <span><strong>{policy.is_active ? "Active" : "Paused"}</strong></span>
            </label>
            <button className="ms-button ms-button--secondary" type="button" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate(policy.id)}>
              Remove
            </button>
          </article>
        ))}
        {policies.isLoading ? <div className="ms-support-inbox-state">Loading SLA policies…</div> : null}
        {!policies.isLoading && !(policies.data ?? []).length ? <div className="ms-support-inbox-state">No SLA overrides yet.</div> : null}
      </div>
    </section>
  );
}
