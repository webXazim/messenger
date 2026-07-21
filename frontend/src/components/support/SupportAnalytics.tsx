import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type {
  SupportAnalyticsV2Filters,
  SupportAnalyticsV2MetricSet,
  SupportAnalyticsV2VolumePoint,
  SupportBootstrap,
} from "../../types/support";
import { MessengerSectionHeader } from "../pages/MessengerPageHeader";

function formatDuration(seconds?: number | null) {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const hours = seconds / 3600;
  return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)}h`;
}

function formatRating(value?: number | null) {
  return value === null || value === undefined ? "—" : `${value.toFixed(1)} / 5`;
}

function trend(current: number, previous: number, lowerIsBetter = false) {
  if (!previous) return { label: "No previous baseline", tone: "neutral" };
  const delta = ((current - previous) / previous) * 100;
  const improved = lowerIsBetter ? delta <= 0 : delta >= 0;
  return {
    label: `${delta >= 0 ? "↑" : "↓"} ${Math.abs(delta).toFixed(0)}% vs previous`,
    tone: improved ? "positive" : "negative",
  };
}

function Metric({
  label,
  value,
  detail,
  tone = "neutral",
}: {
  label: string;
  value: string | number;
  detail: string;
  tone?: string;
}) {
  return (
    <article className="ms-support-analytics-v2-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small className={`is-${tone}`}>{detail}</small>
    </article>
  );
}

function LineChart({
  current,
  previous,
}: {
  current: SupportAnalyticsV2VolumePoint[];
  previous: SupportAnalyticsV2VolumePoint[];
}) {
  const width = 920;
  const height = 290;
  const pad = { left: 42, right: 18, top: 18, bottom: 34 };
  const max = Math.max(
    1,
    ...current.map((item) => item.created),
    ...previous.map((item) => item.created),
  );
  const x = (index: number, length: number) =>
    pad.left + (index * (width - pad.left - pad.right)) / Math.max(1, length - 1);
  const y = (value: number) =>
    height - pad.bottom - (value / max) * (height - pad.top - pad.bottom);
  const path = (rows: SupportAnalyticsV2VolumePoint[]) =>
    rows.map((row, index) => `${index ? "L" : "M"} ${x(index, rows.length)} ${y(row.created)}`).join(" ");

  return (
    <div className="ms-support-analytics-v2-line-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Conversation volume with previous-period comparison">
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const yy = pad.top + ratio * (height - pad.top - pad.bottom);
          const value = Math.round(max * (1 - ratio));
          return (
            <g key={ratio}>
              <line x1={pad.left} x2={width - pad.right} y1={yy} y2={yy} className="grid" />
              <text x={pad.left - 9} y={yy + 3} textAnchor="end">{value}</text>
            </g>
          );
        })}
        <path d={path(previous)} className="previous" />
        <path d={path(current)} className="current" />
        {current.map((row, index) => (
          <g key={row.date}>
            <circle cx={x(index, current.length)} cy={y(row.created)} r="4" className="point">
              <title>{`${row.date}: ${row.created} conversations`}</title>
            </circle>
            {(current.length <= 14 || index % Math.ceil(current.length / 8) === 0) ? (
              <text x={x(index, current.length)} y={height - 9} textAnchor="middle">
                {new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(`${row.date}T00:00:00`))}
              </text>
            ) : null}
          </g>
        ))}
      </svg>
      <div className="ms-support-analytics-v2-legend">
        <span><i className="current" />Current period</span>
        <span><i className="previous" />Previous period</span>
      </div>
    </div>
  );
}

export function SupportAnalytics({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const canView = bootstrap.role === "owner" || Boolean(bootstrap.agents[0]?.can_view_analytics);
  const [days, setDays] = useState(30);
  const [website, setWebsite] = useState("");
  const [team, setTeam] = useState("");
  const [exportMessage, setExportMessage] = useState("");

  const filters = useMemo<SupportAnalyticsV2Filters>(
    () => ({
      days,
      website: website || undefined,
      team: team || undefined,
    }),
    [days, website, team],
  );

  const overview = useQuery({
    queryKey: ["support-analytics-v2-overview", filters],
    queryFn: ({ signal }) => supportApi.getAnalyticsV2Overview(filters, signal),
    enabled: canView,
    staleTime: 60_000,
  });
  const volume = useQuery({
    queryKey: ["support-analytics-v2-volume", filters],
    queryFn: ({ signal }) => supportApi.getAnalyticsV2Volume(filters, signal),
    enabled: canView,
    staleTime: 60_000,
  });
  const websites = useQuery({
    queryKey: ["support-analytics-v2-websites", days],
    queryFn: ({ signal }) => supportApi.getAnalyticsV2Websites({ days }, signal),
    enabled: canView,
    staleTime: 60_000,
  });
  const tags = useQuery({
    queryKey: ["support-analytics-v2-tags", filters],
    queryFn: ({ signal }) => supportApi.getAnalyticsV2Tags(filters, signal),
    enabled: canView,
    staleTime: 60_000,
  });
  const hours = useQuery({
    queryKey: ["support-analytics-v2-hours", filters],
    queryFn: ({ signal }) => supportApi.getAnalyticsV2Hours(filters, signal),
    enabled: canView,
    staleTime: 60_000,
  });
  const agents = useQuery({
    queryKey: ["support-analytics-v2-agents", filters],
    queryFn: ({ signal }) => supportApi.getAnalyticsV2Agents(filters, signal),
    enabled: canView,
    staleTime: 60_000,
  });
  const exportMutation = useMutation({
    mutationFn: () => supportApi.createAnalyticsExport(filters),
    onSuccess: () => setExportMessage("Export queued. It will appear in your analytics exports when ready."),
  });

  if (!canView) {
    return (
      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader
          eyebrow="Analytics"
          title="Reporting access is restricted"
          description="The Support Chat owner can grant analytics access from the Agents page. Messenger access is unaffected."
        />
      </section>
    );
  }

  const queries = [overview, volume, websites, tags, hours, agents];
  const loading = queries.some((query) => query.isLoading);
  const firstError = queries.find((query) => query.isError)?.error;
  const current = overview.data?.current;
  const previous = overview.data?.previous;
  const hasData = Boolean(current && current.conversations > 0);
  const maxHour = Math.max(1, ...(hours.data?.results ?? []).map((row) => row.conversations));
  const maxWebsite = Math.max(1, ...(websites.data?.results ?? []).map((row) => row.conversations));

  const metricTrend = (
    selector: (metric: SupportAnalyticsV2MetricSet) => number,
    lowerIsBetter = false,
  ) => current && previous ? trend(selector(current), selector(previous), lowerIsBetter) : { label: "—", tone: "neutral" };

  return (
    <div className="ms-support-analytics-v2">
      <section className="ms-page-surface ms-page-surface--padded ms-support-analytics-v2-overview">
        <div className="ms-support-analytics-v2-commandbar">
          <div>
            <strong>Performance overview</strong>
            <span>Aggregated reporting for demand, service quality, queues, and team performance.</span>
          </div>
          <div className="ms-support-analytics-v2-filters">
              <select value={days} onChange={(event) => setDays(Number(event.target.value))} aria-label="Report period">
                <option value={7}>Last 7 days</option>
                <option value={30}>Last 30 days</option>
                <option value={90}>Last 90 days</option>
                <option value={365}>Last 12 months</option>
              </select>
              <select value={website} onChange={(event) => setWebsite(event.target.value)} aria-label="Website">
                <option value="">All websites</option>
                {bootstrap.websites.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              <select value={team} onChange={(event) => setTeam(event.target.value)} aria-label="Team">
                <option value="">All teams</option>
                {bootstrap.teams.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              <button className="ms-button ms-button--secondary" type="button" onClick={() => exportMutation.mutate()} disabled={exportMutation.isPending}>
                {exportMutation.isPending ? "Queuing…" : "Export CSV"}
              </button>
          </div>
        </div>
        {exportMessage ? <div className="ms-support-success" role="status">{exportMessage}</div> : null}
        {loading ? <div className="ms-support-empty">Loading aggregated Support analytics…</div> : null}
        {firstError ? <div className="ms-support-error">{parseApiError(firstError, "Support analytics could not be loaded.").message}</div> : null}
        {!loading && !firstError && !hasData ? <div className="ms-support-empty">No support activity exists for this selection yet.</div> : null}

        {current && previous ? (
          <div className="ms-support-analytics-v2-metrics">
            {(() => { const item = metricTrend((value) => value.conversations); return <Metric label="Conversations" value={current.conversations} detail={item.label} tone={item.tone} />; })()}
            {(() => { const item = metricTrend((value) => value.first_response_seconds ?? 0, true); return <Metric label="First response" value={formatDuration(current.first_response_seconds)} detail={item.label} tone={item.tone} />; })()}
            {(() => { const item = metricTrend((value) => value.resolution_seconds ?? 0, true); return <Metric label="Resolution time" value={formatDuration(current.resolution_seconds)} detail={item.label} tone={item.tone} />; })()}
            {(() => { const item = metricTrend((value) => value.sla_compliance); return <Metric label="SLA compliance" value={`${current.sla_compliance}%`} detail={item.label} tone={item.tone} />; })()}
            {(() => { const item = metricTrend((value) => value.csat_average ?? 0); return <Metric label="CSAT" value={formatRating(current.csat_average)} detail={item.label} tone={item.tone} />; })()}
            {(() => { const item = metricTrend((value) => value.unassigned_rate, true); return <Metric label="Unassigned rate" value={`${current.unassigned_rate}%`} detail={item.label} tone={item.tone} />; })()}
          </div>
        ) : null}
      </section>

      {hasData && current ? (
        <div className="ms-support-analytics-v2-grid">
          <section className="ms-page-surface ms-page-surface--padded ms-support-analytics-v2-volume">
            <MessengerSectionHeader eyebrow="Volume" title="Conversations over time" description="Current period compared with the immediately preceding period." />
            {volume.data ? <LineChart current={volume.data.current} previous={volume.data.previous} /> : null}
          </section>

          <section className="ms-page-surface ms-page-surface--padded ms-support-analytics-v2-websites">
            <MessengerSectionHeader eyebrow="Distribution" title="Conversations by website" description="Authorized account-wide support demand." />
            <div className="ms-support-analytics-v2-bars">
              {(websites.data?.results ?? []).slice(0, 6).map((row) => (
                <div key={row.website.id}>
                  <span>{row.website.name}</span>
                  <i><b style={{ width: `${(row.conversations / maxWebsite) * 100}%` }} /></i>
                  <strong>{row.conversations}</strong>
                </div>
              ))}
            </div>
          </section>

          <section className="ms-page-surface ms-page-surface--padded">
            <MessengerSectionHeader eyebrow="Live queue" title="Queue health" description="Current operational workload." />
            <div className="ms-support-analytics-v2-queue">
              <div><span>Open</span><strong>{current.queue.open}</strong></div>
              <div><span>Unassigned</span><strong className="warning">{current.queue.unassigned}</strong></div>
              <div><span>Overdue</span><strong className="danger">{current.queue.overdue}</strong></div>
              <div><span>SLA at risk</span><strong className="warning">{current.queue.at_risk}</strong></div>
            </div>
          </section>

          <section className="ms-page-surface ms-page-surface--padded">
            <MessengerSectionHeader eyebrow="Topics" title="Top tags" description="Most common support subjects." />
            <div className="ms-support-analytics-v2-tags">
              {(tags.data?.results ?? []).map((row) => (
                <div key={row.tag.id}>
                  <span>{row.tag.name}</span><strong>{row.conversations}</strong><small>{row.share}%</small>
                </div>
              ))}
            </div>
          </section>

          <section className="ms-page-surface ms-page-surface--padded">
            <MessengerSectionHeader eyebrow="Demand" title="Busiest hours" description="Conversation arrivals by local hour." />
            <div className="ms-support-analytics-v2-hours">
              {(hours.data?.results ?? []).map((row) => (
                <i key={row.hour} style={{ height: `${Math.max(4, (row.conversations / maxHour) * 100)}%` }}>
                  <span>{row.conversations}</span>
                </i>
              ))}
            </div>
            <div className="ms-support-analytics-v2-hour-axis"><span>12 AM</span><span>6 AM</span><span>12 PM</span><span>6 PM</span></div>
          </section>

          <section className="ms-page-surface ms-page-surface--padded ms-support-analytics-v2-agents">
            <MessengerSectionHeader eyebrow="Team" title="Agent performance" description="Handled conversations, speed, resolutions, and satisfaction." />
            <div className="ms-support-report-table" role="table" aria-label="Agent performance">
              <div className="ms-support-report-row ms-support-report-row--header" role="row">
                <span>Agent</span><span>Handled</span><span>First response</span><span>Resolution</span><span>CSAT</span>
              </div>
              {(agents.data?.results ?? []).map((row) => (
                <div className="ms-support-report-row" role="row" key={row.agent.id}>
                  <span data-label="Agent"><strong>{row.agent.display_name}</strong><small>{row.availability}</small></span>
                  <span data-label="Handled">{row.conversations}<small>{row.resolved} resolved · {row.replies} replies</small></span>
                  <span data-label="First response">{formatDuration(row.first_response_seconds)}</span>
                  <span data-label="Resolution">{formatDuration(row.resolution_seconds)}</span>
                  <span data-label="CSAT">{formatRating(row.csat_average)}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
