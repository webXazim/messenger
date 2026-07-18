import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type { SupportBootstrap } from "../../types/support";
import { MessengerSectionHeader } from "../pages/MessengerPageHeader";
import { UserAvatar } from "../UserAvatar";

function formatDuration(seconds?: number | null) {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) {
    const hours = seconds / 3600;
    return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)}h`;
  }
  const days = seconds / 86400;
  return `${days < 10 ? days.toFixed(1) : Math.round(days)}d`;
}

function formatRating(value?: number | null) {
  return value === null || value === undefined ? "—" : `${value.toFixed(2)} / 5`;
}

function Metric({ label, value, detail }: { label: string; value: string | number; detail?: string }) {
  return (
    <article className="ms-support-analytics-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </article>
  );
}

export function SupportAnalytics({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const canView = bootstrap.role === "owner" || Boolean(bootstrap.agents[0]?.can_view_analytics);
  const [days, setDays] = useState(30);
  const [website, setWebsite] = useState("");
  const query = useQuery({
    queryKey: ["support-analytics", days, website],
    queryFn: ({ signal }) => supportApi.getAnalytics({ days, website: website || undefined }, signal),
    enabled: canView,
    staleTime: 30_000,
  });

  const peak = useMemo(() => {
    const points = query.data?.daily || [];
    return Math.max(1, ...points.map((point) => Math.max(point.created, point.resolved)));
  }, [query.data?.daily]);

  if (!canView) {
    return (
      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader
          eyebrow="Analytics"
          title="Reporting access is restricted"
          description="The Support Chat owner can grant analytics access from the Agents page. Your personal Messenger access is unaffected."
        />
      </section>
    );
  }

  return (
    <div className="ms-support-analytics-stack">
      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader
          eyebrow="Support performance"
          title="Analytics"
          description="Review website support demand, response performance, team workload, and customer satisfaction without mixing any personal Messenger data."
          actions={(
            <div className="ms-support-analytics-filters">
              <label>
                <span className="ms-visually-hidden">Report period</span>
                <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
                  <option value={7}>Last 7 days</option>
                  <option value={30}>Last 30 days</option>
                  <option value={90}>Last 90 days</option>
                  <option value={365}>Last 12 months</option>
                </select>
              </label>
              <label>
                <span className="ms-visually-hidden">Website</span>
                <select value={website} onChange={(event) => setWebsite(event.target.value)}>
                  <option value="">All websites</option>
                  {bootstrap.websites.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
                </select>
              </label>
            </div>
          )}
        />
        {query.isLoading ? <div className="ms-support-empty">Loading Support analytics…</div> : null}
        {query.isError ? <div className="ms-support-error">{parseApiError(query.error, "Support analytics could not be loaded.").message}</div> : null}
        {query.data ? (
          <div className="ms-support-analytics-metrics">
            <Metric label="New conversations" value={query.data.summary.conversations_created} detail={`${query.data.summary.current_open} currently open`} />
            <Metric label="Resolution rate" value={`${query.data.summary.resolution_rate}%`} detail={`${query.data.summary.resolved} resolved in period`} />
            <Metric label="Median first response" value={formatDuration(query.data.summary.median_first_response_seconds)} detail={`${query.data.summary.sla_breach_rate}% service breach rate`} />
            <Metric label="Customer satisfaction" value={formatRating(query.data.summary.csat_average)} detail={`${query.data.summary.csat_response_rate}% response rate`} />
          </div>
        ) : null}
      </section>

      {query.data ? (
        <>
          <section className="ms-page-surface ms-page-surface--padded">
            <MessengerSectionHeader eyebrow="Trend" title="Conversation volume" description="Created and resolved Support conversations across the selected period." />
            <div className="ms-support-analytics-chart" role="img" aria-label="Daily created and resolved Support conversations">
              {query.data.daily.map((point) => (
                <div className="ms-support-analytics-day" key={point.date} title={`${point.date}: ${point.created} created, ${point.resolved} resolved`}>
                  <div className="ms-support-analytics-bars">
                    <span className="is-created" style={{ height: `${Math.max(3, (point.created / peak) * 100)}%` }} />
                    <span className="is-resolved" style={{ height: `${Math.max(3, (point.resolved / peak) * 100)}%` }} />
                  </div>
                  <time>{new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(`${point.date}T00:00:00`))}</time>
                </div>
              ))}
            </div>
            <div className="ms-support-analytics-legend"><span><i className="is-created" />Created</span><span><i className="is-resolved" />Resolved</span></div>
          </section>

          <section className="ms-page-surface ms-page-surface--padded">
            <MessengerSectionHeader eyebrow="Websites" title="Website performance" description="Each website remains isolated while the report provides an authorized account-wide comparison." />
            {query.data.websites.length ? (
              <div className="ms-support-report-table" role="table" aria-label="Website support performance">
                <div className="ms-support-report-row ms-support-report-row--header" role="row">
                  <span>Website</span><span>Conversations</span><span>First response</span><span>Resolution</span><span>CSAT</span>
                </div>
                {query.data.websites.map((row) => (
                  <div className="ms-support-report-row" role="row" key={row.website.id}>
                    <span data-label="Website"><strong>{row.website.name}</strong><small>{row.website.domain}</small></span>
                    <span data-label="Conversations">{row.conversations}<small>{row.resolution_rate}% resolved</small></span>
                    <span data-label="First response">{formatDuration(row.median_first_response_seconds)}<small>{row.sla_breach_rate}% breach</small></span>
                    <span data-label="Resolution">{formatDuration(row.median_resolution_seconds)}</span>
                    <span data-label="CSAT">{formatRating(row.csat_average)}<small>{row.csat_response_rate}% response</small></span>
                  </div>
                ))}
              </div>
            ) : <div className="ms-support-empty">No website activity was recorded in this period.</div>}
          </section>

          <section className="ms-page-surface ms-page-surface--padded">
            <MessengerSectionHeader eyebrow="Team" title="Agent workload" description={bootstrap.role === "owner" ? "Current workload and period activity for Support agents." : "Your own Support workload and performance across assigned websites."} />
            {query.data.agents.length ? (
              <div className="ms-support-agent-performance">
                {query.data.agents.map((row) => (
                  <article key={row.agent.id}>
                    <div className="ms-support-agent-performance__person">
                      <UserAvatar person={{ display_name: row.agent.display_name, avatar: row.agent.avatar }} size="sm" decorative />
                      <span><strong>{row.agent.display_name}</strong><small>{row.availability}</small></span>
                    </div>
                    <dl>
                      <div><dt>Active</dt><dd>{row.active_assigned}</dd></div>
                      <div><dt>Resolved</dt><dd>{row.resolved_in_period}</dd></div>
                      <div><dt>Replies</dt><dd>{row.team_messages}</dd></div>
                      <div><dt>First response</dt><dd>{formatDuration(row.median_first_response_seconds)}</dd></div>
                    </dl>
                  </article>
                ))}
              </div>
            ) : <div className="ms-support-empty">No agent activity was recorded in this period.</div>}
          </section>
        </>
      ) : null}
    </div>
  );
}
