import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type { SupportBootstrap, SupportWebsite, SupportWidgetSettings } from "../../types/support";
import { SupportBadge, SupportButton, SupportModal, SupportState, SupportTabs, SupportToggle } from "../../support/components";

const tabs = [
  { id: "setup", label: "Setup" },
  { id: "appearance", label: "Appearance" },
  { id: "behavior", label: "Behavior" },
  { id: "access", label: "Access" },
  { id: "usage", label: "Usage" },
];

function originsFromText(value: string) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function duration(value?: number | null) {
  if (!value) return "—";
  const minutes = Math.max(1, Math.round(value / 60));
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function WidgetPreview({ website, settings, enabled }: { website: SupportWebsite; settings: SupportWidgetSettings; enabled: boolean }) {
  const theme = settings.theme === "dark" ? "dark" : "light";
  const brandName = settings.brand_name || website.name;
  const launcherLabel = settings.launcher_text || "Chat";
  const welcomeText = settings.welcome_text || "Hi, how can we help?";
  const offlineText = settings.offline_text || "Leave a message and our team will reply soon.";
  const statusText = enabled ? "Online · Support" : "Offline · Leave a message";
  const featureLabels = [
    settings.allow_attachments ? "Attachments" : null,
    settings.allow_audio_calls ? "Audio" : null,
    settings.allow_video_calls ? "Video" : null,
  ].filter(Boolean) as string[];

  return (
    <div className={`sc-widget-demo is-${theme} is-${settings.position}`}>
      <div className="sc-widget-demo__canvas">
        <div className="sc-widget-demo__page">
          <div className="sc-widget-demo__page-bar" />
          <div className="sc-widget-demo__page-copy">
            <strong>{website.name}</strong>
            <span>Embedded support widget preview</span>
          </div>
          <div className="sc-widget-demo__dock">
            <div className="sc-widget-demo__panel">
              <div className="sc-widget-demo__panel-header">
                <span className="sc-widget-demo__mark" style={{ backgroundColor: settings.primary_color }}>
                  {brandName.slice(0, 1).toUpperCase()}
                </span>
                <div className="sc-widget-demo__title">
                  <strong>{brandName}</strong>
                  <small>{statusText}</small>
                </div>
                <button type="button" aria-label="Close preview" disabled>
                  ×
                </button>
              </div>

              <div className="sc-widget-demo__panel-body">
                <p className="sc-widget-demo__welcome">{enabled ? welcomeText : offlineText}</p>
                <div className="sc-widget-demo__form">
                  {settings.require_name ? <div className="sc-widget-demo__field">Your name</div> : null}
                  {settings.require_email ? <div className="sc-widget-demo__field">Email address</div> : null}
                  <div className="sc-widget-demo__field sc-widget-demo__field--message">
                    {enabled ? "How can we help today?" : "Leave a message and our team will reply soon."}
                  </div>
                  <button type="button" className="sc-widget-demo__primary" style={{ backgroundColor: settings.primary_color }}>
                    {enabled ? "Start conversation" : "Leave message"}
                  </button>
                </div>
                {settings.privacy_note ? <p className="sc-widget-demo__note">{settings.privacy_note}</p> : null}
                {featureLabels.length ? (
                  <div className="sc-widget-demo__features">
                    {featureLabels.map((label) => (
                      <span key={label}>{label}</span>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>

            <button type="button" className="sc-widget-demo__launcher" style={{ backgroundColor: settings.primary_color }}>
              {launcherLabel}
            </button>
          </div>
        </div>
      </div>

      <div className="sc-widget-demo__meta">
        <strong>Live widget preview</strong>
        <small>
          Panel 380 × 620 px · Launcher 54 px · {settings.position === "left" ? "Bottom left" : "Bottom right"} · {settings.theme === "auto" ? "Auto theme" : `${settings.theme} theme`}
        </small>
      </div>
    </div>
  );
}

export function SupportWebsitesPage({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const queryClient = useQueryClient();
  const isOwner = bootstrap.role === "owner";
  const [selectedId, setSelectedId] = useState(bootstrap.websites[0]?.id ?? "");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "live" | "off">("all");
  const [tab, setTab] = useState("setup");
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const selected = bootstrap.websites.find((website) => website.id === selectedId) ?? bootstrap.websites[0];

  useEffect(() => {
    if (!selected && bootstrap.websites[0]) setSelectedId(bootstrap.websites[0].id);
  }, [bootstrap.websites, selected]);

  const filtered = useMemo(
    () =>
      bootstrap.websites.filter((website) => {
        const matchesSearch = `${website.name} ${website.domain}`.toLowerCase().includes(search.toLowerCase());
        const matchesStatus = statusFilter === "all" || (statusFilter === "live" ? website.widget_enabled : !website.widget_enabled);
        return matchesSearch && matchesStatus;
      }),
    [bootstrap.websites, search, statusFilter],
  );

  const createMutation = useMutation({
    mutationFn: () => supportApi.createWebsite({ name: name.trim(), domain: domain.trim() }),
    onSuccess: async (website) => {
      setCreateOpen(false);
      setName("");
      setDomain("");
      setSelectedId(website.id);
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
    },
    onError: (reason) => setError(parseApiError(reason, "The website could not be added.").message),
  });

  return (
    <div className="sc-websites-page">
      <header className="sc-websites-toolbar sc-websites-toolbar--controls">
        <div className="sc-websites-toolbar__summary">
          <strong>{bootstrap.websites.length} connected website{bootstrap.websites.length === 1 ? "" : "s"}</strong>
          <span>Search, filter, and manage widget installations.</span>
        </div>
        <div className="sc-websites-toolbar__actions">
          <label className="sc-search-field">
            <span aria-hidden="true">⌕</span>
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search websites" />
          </label>
          <select className="sc-control" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}>
            <option value="all">All statuses</option>
            <option value="live">Live</option>
            <option value="off">Disabled</option>
          </select>
          {isOwner ? <SupportButton onClick={() => setCreateOpen(true)}>＋ New website</SupportButton> : null}
        </div>
      </header>

      {!bootstrap.websites.length ? (
        <SupportState
          kind="empty"
          title="No support websites"
          description="Add a website to create its isolated widget installation and visitor inbox."
          actionLabel={isOwner ? "Add website" : undefined}
          onAction={isOwner ? () => setCreateOpen(true) : undefined}
        />
      ) : (
        <>
          <section className="sc-website-table-wrap">
            <table className="sc-website-table">
              <thead>
                <tr>
                  <th>Website</th>
                  <th>Domain</th>
                  <th>Widget</th>
                  <th>Allowed origins</th>
                  <th>Active agents</th>
                  <th>Conversations today</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {filtered.map((website) => (
                  <WebsiteRow
                    key={website.id}
                    website={website}
                    selected={website.id === selected?.id}
                    onSelect={() => {
                      setSelectedId(website.id);
                      setTab("setup");
                    }}
                  />
                ))}
              </tbody>
            </table>
            {!filtered.length ? <div className="sc-table-empty">No websites match the current filters.</div> : null}
          </section>
          {selected ? <WebsiteWorkspace website={selected} tab={tab} setTab={setTab} isOwner={isOwner} /> : null}
        </>
      )}

      <SupportModal
        open={createOpen}
        title="New support website"
        description="The widget and all visitor data remain isolated to this website."
        onClose={() => {
          if (!createMutation.isPending) setCreateOpen(false);
        }}
        secondaryAction={{ label: "Cancel" }}
        primaryAction={{
          label: "Create website",
          onClick: () => createMutation.mutate(),
          disabled: !name.trim() || !domain.trim(),
          isLoading: createMutation.isPending,
        }}
      >
        <form
          className="sc-modal-form"
          onSubmit={(e) => {
            e.preventDefault();
            createMutation.mutate();
          }}
        >
          <label>
            Website name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="A2T Development" />
          </label>
          <label>
            Domain
            <input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="a2tdev.com" />
          </label>
          {error ? <div className="sc-inline-error">{error}</div> : null}
        </form>
      </SupportModal>
    </div>
  );
}

function WebsiteRow({ website, selected, onSelect }: { website: SupportWebsite; selected: boolean; onSelect: () => void }) {
  const usage = useQuery({ queryKey: ["support-website-usage", website.id], queryFn: ({ signal }) => supportApi.getWebsiteUsage(website.id, signal), staleTime: 30_000 });
  return (
    <tr
      className={selected ? "is-selected" : ""}
      onClick={onSelect}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect();
      }}
    >
      <td>
        <div className="sc-website-name">
          <span>{website.name.slice(0, 2).toUpperCase()}</span>
          <div>
            <strong>{website.name}</strong>
            <small>{website.is_active ? "Active website" : "Archived"}</small>
          </div>
        </div>
      </td>
      <td>{website.domain}</td>
      <td>
        <SupportBadge tone={website.widget_enabled ? "success" : "neutral"}>{website.widget_enabled ? "Live" : "Disabled"}</SupportBadge>
      </td>
      <td>{website.allowed_origins.length || 1}</td>
      <td>{usage.data?.active_agents ?? "—"}</td>
      <td>{usage.data?.conversations_today ?? "—"}</td>
      <td>
        <button className="sc-icon-action" aria-label={`Manage ${website.name}`}>
          ⋯
        </button>
      </td>
    </tr>
  );
}

function WebsiteWorkspace({ website, tab, setTab, isOwner }: { website: SupportWebsite; tab: string; setTab: (tab: string) => void; isOwner: boolean }) {
  const queryClient = useQueryClient();
  const [settings, setSettings] = useState(website.widget_settings);
  const [origins, setOrigins] = useState(website.allowed_origins.join("\n"));
  const [enabled, setEnabled] = useState(website.widget_enabled);
  const [copied, setCopied] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const usage = useQuery({ queryKey: ["support-website-usage", website.id], queryFn: ({ signal }) => supportApi.getWebsiteUsage(website.id, signal), staleTime: 30_000 });

  useEffect(() => {
    setSettings(website.widget_settings);
    setOrigins(website.allowed_origins.join("\n"));
    setEnabled(website.widget_enabled);
    setMessage(null);
    setError(null);
  }, [website]);

  const update = <K extends keyof SupportWidgetSettings>(key: K, value: SupportWidgetSettings[K]) =>
    setSettings((current) => ({ ...current, [key]: value }));
  const save = useMutation({
    mutationFn: () => supportApi.updateWebsiteWidgetConfiguration(website.id, { allowed_origins: originsFromText(origins), widget_enabled: enabled, settings }),
    onSuccess: async () => {
      setMessage("Website settings saved.");
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
    },
    onError: (reason) => setError(parseApiError(reason, "Website settings could not be saved.").message),
  });
  const regenerate = useMutation({
    mutationFn: () => supportApi.regenerateWebsiteSiteKey(website.id),
    onSuccess: async () => {
      setMessage("Site key regenerated. Replace the previous installation code.");
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
    },
    onError: (reason) => setError(parseApiError(reason, "Site key could not be regenerated.").message),
  });
  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setError("Copying is not available in this browser.");
    }
  };

  return (
    <section className="sc-website-workspace">
      <header className="sc-website-workspace__head">
        <div className="sc-website-identity">
          <span>{website.name.slice(0, 2).toUpperCase()}</span>
          <div>
            <div>
              <h2>{website.name}</h2>
              <SupportBadge tone={enabled ? "success" : "neutral"}>{enabled ? "Live" : "Disabled"}</SupportBadge>
            </div>
            <p>
              https://{website.domain} · Created {website.created_at ? new Date(website.created_at).toLocaleDateString() : "recently"}
            </p>
          </div>
        </div>
        <div>
          <SupportButton variant="secondary" onClick={() => window.open(`https://${website.domain}`, "_blank", "noopener,noreferrer")}>
            Preview website ↗
          </SupportButton>
        </div>
      </header>
      <SupportTabs tabs={tabs} value={tab} onChange={setTab} ariaLabel="Website settings" />
      <div className="sc-website-workspace__body">
        {tab === "setup" ? (
          <div className="sc-website-setup-layout">
            <div className="sc-website-setup-main">
              <section className="sc-setting-card">
                <header>
                  <div>
                    <span className="sc-page-eyebrow">Installation</span>
                    <h3>Connect the widget</h3>
                    <p>Install this script once before the closing body tag on your website.</p>
                  </div>
                </header>
                <div className="sc-install-grid">
                  <label className="sc-install-key">
                    <span>Site key</span>
                    <div className="sc-copy-field">
                      <input value={website.site_key} readOnly />
                      <button type="button" onClick={() => void copy(website.site_key)}>Copy</button>
                    </div>
                    <small>Keep this key private. Regenerating it disables the previous installation.</small>
                  </label>
                  <label className="sc-install-code">
                    <span>Install script</span>
                    <textarea value={website.install_code} readOnly rows={5} />
                    <div className="sc-install-actions">
                      <button type="button" className="sc-copy-code" onClick={() => void copy(website.install_code)}>
                        {copied ? "Copied" : "Copy script"}
                      </button>
                      <button
                        type="button"
                        className="sc-danger-link"
                        disabled={!isOwner || regenerate.isPending}
                        onClick={() => {
                          if (window.confirm("Regenerate this key? Existing widget sessions will stop working.")) regenerate.mutate();
                        }}
                      >
                        Regenerate site key
                      </button>
                    </div>
                  </label>
                </div>
              </section>

              <section className="sc-setting-card">
                <header>
                  <div>
                    <span className="sc-page-eyebrow">Widget essentials</span>
                    <h3>Visitor-facing content</h3>
                    <p>These values update the preview immediately and are saved together.</p>
                  </div>
                  <div className="sc-widget-status-control">
                    <span>Widget status</span>
                    <SupportToggle label="Enable widget" checked={enabled} onChange={setEnabled} />
                  </div>
                </header>
                <div className="sc-essential-fields">
                  <SettingText label="Brand name" value={settings.brand_name} onChange={(v) => update("brand_name", v)} />
                  <SettingText label="Welcome message" value={settings.welcome_text} onChange={(v) => update("welcome_text", v)} />
                  <SettingText label="Offline message" value={settings.offline_text} onChange={(v) => update("offline_text", v)} />
                </div>
              </section>
            </div>

            <aside className="sc-website-preview-panel">
              <header>
                <div>
                  <span className="sc-page-eyebrow">Widget preview</span>
                  <h3>Exact visitor panel</h3>
                </div>
                <SupportBadge tone={enabled ? "success" : "neutral"}>{enabled ? "Live" : "Disabled"}</SupportBadge>
              </header>
              <WidgetPreview website={website} settings={settings} enabled={enabled} />
            </aside>
          </div>
        ) : null}

        {tab === "appearance" ? (
          <div className="sc-settings-list">
            <SettingText label="Launcher text" value={settings.launcher_text} onChange={(v) => update("launcher_text", v)} />
            <label className="sc-settings-row">
              <span>
                <strong>Theme</strong>
                <small>Follow the visitor device or use a fixed theme.</small>
              </span>
              <select value={settings.theme} onChange={(e) => update("theme", e.target.value as SupportWidgetSettings["theme"])}>
                <option value="auto">Automatic</option>
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select>
            </label>
            <label className="sc-settings-row">
              <span>
                <strong>Launcher position</strong>
                <small>Place the launcher at the bottom edge.</small>
              </span>
              <select value={settings.position} onChange={(e) => update("position", e.target.value as SupportWidgetSettings["position"])}>
                <option value="right">Bottom right</option>
                <option value="left">Bottom left</option>
              </select>
            </label>
            <label className="sc-settings-row">
              <span>
                <strong>Primary color</strong>
                <small>Used for the launcher and key accents.</small>
              </span>
              <input type="color" value={settings.primary_color} onChange={(e) => update("primary_color", e.target.value)} />
            </label>
          </div>
        ) : null}

        {tab === "behavior" ? (
          <div className="sc-settings-list">
            <SettingToggle label="Require visitor name" description="Collect a name before the first message." checked={settings.require_name} onChange={(v) => update("require_name", v)} />
            <SettingToggle label="Require visitor email" description="Useful for follow-up after the visitor leaves." checked={settings.require_email} onChange={(v) => update("require_email", v)} />
            <SettingToggle label="Allow attachments" description="Use the existing protected Support upload pipeline." checked={settings.allow_attachments} onChange={(v) => update("allow_attachments", v)} />
            <SettingToggle label="Allow audio calls" description="Agents may start audio calls from Support conversations." checked={settings.allow_audio_calls} onChange={(v) => update("allow_audio_calls", v)} />
            <SettingToggle label="Allow video calls" description="Visitors still control camera permission." checked={settings.allow_video_calls} onChange={(v) => update("allow_video_calls", v)} />
          </div>
        ) : null}

        {tab === "access" ? (
          <div className="sc-settings-list">
            <label className="sc-origin-editor">
              <span>
                <strong>Allowed origins</strong>
                <small>One complete HTTPS origin per line. Requests from other origins are rejected.</small>
              </span>
              <textarea value={origins} onChange={(e) => setOrigins(e.target.value)} rows={7} placeholder={`https://${website.domain}`} />
            </label>
            <SettingText label="Privacy note" value={settings.privacy_note} onChange={(v) => update("privacy_note", v)} />
          </div>
        ) : null}

        {tab === "usage" ? (
          usage.isLoading ? (
            <SupportState kind="loading" title="Loading website usage" />
          ) : usage.isError ? (
            <SupportState kind="error" title="Usage could not be loaded" description="The widget settings remain available." actionLabel="Retry" onAction={() => void usage.refetch()} />
          ) : (
            <div className="sc-usage-grid">
              <Usage label="Conversations today" value={String(usage.data?.conversations_today ?? 0)} />
              <Usage label="Messages today" value={String(usage.data?.messages_today ?? 0)} />
              <Usage label="Active agents" value={String(usage.data?.active_agents ?? 0)} />
              <Usage label="Average resolution" value={duration(usage.data?.average_resolution_seconds)} />
            </div>
          )
        ) : null}
      </div>
      {error ? (
        <div className="sc-inline-error" role="alert">
          {error}
        </div>
      ) : null}
      {message ? (
        <div className="sc-inline-success" role="status">
          {message}
        </div>
      ) : null}
      {isOwner && tab !== "usage" ? (
        <footer className="sc-website-save">
          <SupportButton isLoading={save.isPending} onClick={() => save.mutate()}>
            Save website settings
          </SupportButton>
        </footer>
      ) : null}
    </section>
  );
}

function SettingText({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="sc-settings-row">
      <span>
        <strong>{label}</strong>
      </span>
      <input value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function SettingToggle({ label, description, checked, onChange }: { label: string; description: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <div className="sc-settings-row">
      <span>
        <strong>{label}</strong>
        <small>{description}</small>
      </span>
      <SupportToggle checked={checked} onChange={onChange} label={label} />
    </div>
  );
}

function Usage({ label, value }: { label: string; value: string }) {
  return (
    <article>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}
