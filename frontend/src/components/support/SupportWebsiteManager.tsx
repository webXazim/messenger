import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { ConfirmDialog } from "../ConfirmDialog";
import { parseApiError } from "../../lib/apiErrors";
import type { SupportWebsite, SupportWidgetSettings } from "../../types/support";

function normalizeOriginInput(value: string) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function WidgetPreview({ website, settings }: { website: SupportWebsite; settings: SupportWidgetSettings }) {
  const previewTheme = settings.theme === "dark" ? " is-dark" : "";
  return (
    <div className={`ms-support-widget-preview${previewTheme}`}>
      <div className="ms-support-widget-preview__panel">
        <div className="ms-support-widget-preview__header">
          <span className="ms-support-widget-preview__avatar" style={{ background: settings.primary_color }} aria-hidden="true">
            {settings.brand_name.slice(0, 1).toUpperCase() || "S"}
          </span>
          <span><strong>{settings.brand_name}</strong><small>{website.domain}</small></span>
        </div>
        <div className="ms-support-widget-preview__body">
          <p>{settings.welcome_text}</p>
          {settings.require_name ? <span className="ms-support-widget-preview__field">Your name</span> : null}
          {settings.require_email ? <span className="ms-support-widget-preview__field">Email address</span> : null}
          <span className="ms-support-widget-preview__composer">Write a message…</span>
          {settings.privacy_note ? <small>{settings.privacy_note}</small> : null}
        </div>
      </div>
      <span className="ms-support-widget-preview__launcher" style={{ background: settings.primary_color }}>
        {settings.launcher_text || "Chat"}
      </span>
    </div>
  );
}

export function SupportWebsiteManager({ website }: { website: SupportWebsite }) {
  const queryClient = useQueryClient();
  const [settings, setSettings] = useState(website.widget_settings);
  const [allowedOrigins, setAllowedOrigins] = useState(website.allowed_origins.join("\n"));
  const [widgetEnabled, setWidgetEnabled] = useState(website.widget_enabled);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [confirmRegenerate, setConfirmRegenerate] = useState(false);

  useEffect(() => {
    setSettings(website.widget_settings);
    setAllowedOrigins(website.allowed_origins.join("\n"));
    setWidgetEnabled(website.widget_enabled);
  }, [website]);

  const originCount = useMemo(() => normalizeOriginInput(allowedOrigins).length, [allowedOrigins]);

  const saveMutation = useMutation({
    mutationFn: () => supportApi.updateWebsiteWidgetConfiguration(website.id, {
      allowed_origins: normalizeOriginInput(allowedOrigins),
      widget_enabled: widgetEnabled,
      settings,
    }),
    onMutate: () => { setError(null); setSuccess(null); },
    onSuccess: async () => {
      setSuccess("Website widget settings saved.");
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
    },
    onError: (reason) => setError(parseApiError(reason, "The widget settings could not be saved.").message),
  });

  const regenerateMutation = useMutation({
    mutationFn: () => supportApi.regenerateWebsiteSiteKey(website.id),
    onMutate: () => setError(null),
    onSuccess: async () => {
      setConfirmRegenerate(false);
      setSuccess("A new site key was generated. Replace the previous installation code on your website.");
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
    },
    onError: (reason) => setError(parseApiError(reason, "The site key could not be regenerated.").message),
  });

  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!saveMutation.isPending) saveMutation.mutate();
  };

  const copyInstallCode = async () => {
    if (!website.install_code) return;
    try {
      await navigator.clipboard.writeText(website.install_code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("Copying is unavailable in this browser. Select the installation code manually.");
    }
  };

  const updateSetting = <K extends keyof SupportWidgetSettings>(key: K, value: SupportWidgetSettings[K]) => {
    setSettings((current) => ({ ...current, [key]: value }));
  };

  return (
    <details className="ms-support-website-card">
      <summary>
        <div className="ms-page-row__copy">
          <strong>{website.name}</strong>
          <span>{website.domain}</span>
        </div>
        <div className="ms-page-actions">
          <span className={`ms-page-badge${widgetEnabled ? " ms-page-badge--strong" : ""}`}>
            {widgetEnabled ? "Widget active" : "Widget off"}
          </span>
          <span className="ms-support-disclosure" aria-hidden="true">⌄</span>
        </div>
      </summary>

      <form className="ms-support-website-card__body" onSubmit={save}>
        <div className="ms-support-widget-layout">
          <div className="ms-support-widget-fields">
            <div className="ms-support-form-grid ms-support-form-grid--widget">
              <label><span>Brand name</span><input className="ms-page-field" value={settings.brand_name} maxLength={120} onChange={(event) => updateSetting("brand_name", event.target.value)} /></label>
              <label><span>Launcher text</span><input className="ms-page-field" value={settings.launcher_text} maxLength={60} onChange={(event) => updateSetting("launcher_text", event.target.value)} /></label>
              <label><span>Primary color</span><div className="ms-support-color-field"><input type="color" value={settings.primary_color} onChange={(event) => updateSetting("primary_color", event.target.value)} /><input className="ms-page-field" value={settings.primary_color} maxLength={7} onChange={(event) => updateSetting("primary_color", event.target.value)} /></div></label>
              <label><span>Position</span><select className="ms-page-field" value={settings.position} onChange={(event) => updateSetting("position", event.target.value as SupportWidgetSettings["position"])}><option value="right">Bottom right</option><option value="left">Bottom left</option></select></label>
              <label><span>Theme</span><select className="ms-page-field" value={settings.theme} onChange={(event) => updateSetting("theme", event.target.value as SupportWidgetSettings["theme"])}><option value="auto">Match visitor device</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
            </div>

            <label className="ms-support-full-field"><span>Welcome message</span><input className="ms-page-field" value={settings.welcome_text} maxLength={255} onChange={(event) => updateSetting("welcome_text", event.target.value)} /></label>
            <label className="ms-support-full-field"><span>Offline message</span><input className="ms-page-field" value={settings.offline_text} maxLength={255} onChange={(event) => updateSetting("offline_text", event.target.value)} /></label>
            <label className="ms-support-full-field"><span>Privacy note</span><input className="ms-page-field" value={settings.privacy_note} maxLength={180} onChange={(event) => updateSetting("privacy_note", event.target.value)} placeholder="Optional short privacy notice" /></label>

            <div className="ms-support-permission-grid">
              <label className="ms-support-toggle-row"><input type="checkbox" checked={widgetEnabled} onChange={(event) => setWidgetEnabled(event.target.checked)} /><span><strong>Enable widget</strong><small>Allows this website to use its support installation.</small></span></label>
              <label className="ms-support-toggle-row"><input type="checkbox" checked={settings.require_name} onChange={(event) => updateSetting("require_name", event.target.checked)} /><span><strong>Require visitor name</strong><small>Ask for a name before starting a support session.</small></span></label>
              <label className="ms-support-toggle-row"><input type="checkbox" checked={settings.require_email} onChange={(event) => updateSetting("require_email", event.target.checked)} /><span><strong>Require visitor email</strong><small>Useful when agents may reply later.</small></span></label>
              <label className="ms-support-toggle-row"><input type="checkbox" checked={settings.allow_attachments} onChange={(event) => updateSetting("allow_attachments", event.target.checked)} /><span><strong>Allow attachments</strong><small>Uses Messenger’s protected upload system when messaging is connected.</small></span></label>
              <label className="ms-support-toggle-row"><input type="checkbox" checked={settings.allow_audio_calls} onChange={(event) => updateSetting("allow_audio_calls", event.target.checked)} /><span><strong>Allow audio calls</strong><small>Agents can call visitors from conversations on this website.</small></span></label>
              <label className="ms-support-toggle-row"><input type="checkbox" checked={settings.allow_video_calls} disabled={!settings.allow_audio_calls} onChange={(event) => updateSetting("allow_video_calls", event.target.checked)} /><span><strong>Allow video calls</strong><small>Visitors still choose whether to grant camera access.</small></span></label>
            </div>
          </div>

          <WidgetPreview website={website} settings={settings} />
        </div>

        <fieldset className="ms-support-installation">
          <legend>Website security and installation</legend>
          <label className="ms-support-full-field">
            <span>Allowed website origins</span>
            <textarea className="ms-page-field ms-support-origin-field" value={allowedOrigins} onChange={(event) => setAllowedOrigins(event.target.value)} placeholder={`https://${website.domain}`} />
            <small>{originCount ? `${originCount} approved origin${originCount === 1 ? "" : "s"}` : `Defaults to https://${website.domain}`}. Enter one full origin per line.</small>
          </label>
          <label className="ms-support-full-field">
            <span>Installation code</span>
            <textarea className="ms-page-field ms-support-code-field" value={website.install_code} readOnly spellCheck={false} />
          </label>
          <div className="ms-support-install-actions">
            <button className="ms-button ms-button--ghost" type="button" onClick={() => void copyInstallCode()} disabled={!website.install_code}>{copied ? "Copied" : "Copy code"}</button>
            <button className="ms-button ms-button--ghost" type="button" onClick={() => setConfirmRegenerate(true)}>Regenerate site key</button>
          </div>
        </fieldset>

        {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
        {success ? <div className="ms-support-success" role="status">{success}</div> : null}
        <div className="ms-support-form-actions">
          <button className="ms-button ms-button--primary" type="submit" disabled={saveMutation.isPending}>{saveMutation.isPending ? "Saving…" : "Save website widget"}</button>
        </div>
      </form>

      <ConfirmDialog
        open={confirmRegenerate}
        title="Regenerate this website key?"
        description="The current installation code and every active visitor session for this website will stop working. Messenger and other support websites are not affected."
        confirmLabel="Regenerate key"
        tone="danger"
        pending={regenerateMutation.isPending}
        error={error}
        onConfirm={() => regenerateMutation.mutate()}
        onClose={() => { if (!regenerateMutation.isPending) setConfirmRegenerate(false); }}
      />
    </details>
  );
}
