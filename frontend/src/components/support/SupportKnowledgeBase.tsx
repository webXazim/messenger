import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type {
  SupportBootstrap,
  SupportKnowledgeArticle,
  SupportKnowledgeArticleInput,
  SupportKnowledgeRevision,
  SupportKnowledgeSettings,
} from "../../types/support";
import {
  SupportBadge,
  SupportButton,
  SupportModal,
  SupportPage,
  SupportState,
  SupportSurface,
  SupportToggle,
} from "../../support/components";

const EMPTY_ARTICLE: SupportKnowledgeArticleInput = {
  category_id: null,
  title: "",
  summary: "",
  seo_description: "",
  language: "en",
  body: "",
  status: "draft",
  all_websites: true,
  website_ids: [],
  is_featured: false,
  related_article_ids: [],
  change_note: "",
};

function ArticleEditor({ bootstrap, article, articles, onDone }: { bootstrap: SupportBootstrap; article: SupportKnowledgeArticle | null; articles: SupportKnowledgeArticle[]; onDone: () => void }) {
  const queryClient = useQueryClient();
  const categories = useQuery({ queryKey: ["support-knowledge-categories", true], queryFn: ({ signal }) => supportApi.listKnowledgeCategories(true, signal) });
  const [form, setForm] = useState<SupportKnowledgeArticleInput>(EMPTY_ARTICLE);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!article) { setForm(EMPTY_ARTICLE); return; }
    setForm({
      category_id: article.category || null,
      title: article.title,
      summary: article.summary,
      seo_description: article.seo_description,
      language: article.language || "en",
      body: article.body,
      status: article.status,
      all_websites: article.all_websites,
      website_ids: article.website_ids,
      is_featured: article.is_featured,
      related_article_ids: article.related_articles.map((item) => item.id),
      change_note: "",
    });
  }, [article]);

  const save = useMutation({
    mutationFn: () => article ? supportApi.updateKnowledgeArticle(article.id, form) : supportApi.createKnowledgeArticle(form),
    onMutate: () => setError(null),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["support-knowledge-articles"] }),
        queryClient.invalidateQueries({ queryKey: ["support-knowledge-categories"] }),
        queryClient.invalidateQueries({ queryKey: ["support-knowledge-revisions"] }),
      ]);
      setForm(EMPTY_ARTICLE); onDone();
    },
    onError: (reason) => setError(parseApiError(reason, "The article could not be saved.").message),
  });

  const submit = (event: FormEvent) => { event.preventDefault(); if (form.title.trim() && form.body.trim()) save.mutate(); };
  const toggleWebsite = (id: string) => setForm((current) => ({ ...current, website_ids: current.website_ids.includes(id) ? current.website_ids.filter((value) => value !== id) : [...current.website_ids, id] }));
  const toggleRelated = (id: string) => setForm((current) => ({ ...current, related_article_ids: current.related_article_ids?.includes(id) ? current.related_article_ids.filter((value) => value !== id) : [...(current.related_article_ids || []), id] }));

  return <form className="sc-kb-editor" onSubmit={submit}>
    <div className="sc-kb-form-grid">
      <label className="sc-kb-field sc-kb-field--wide"><span>Article title</span><input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} maxLength={180} required /></label>
      <label className="sc-kb-field"><span>Category</span><select value={form.category_id || ""} onChange={(event) => setForm({ ...form, category_id: event.target.value || null })}><option value="">No category</option>{categories.data?.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label className="sc-kb-field"><span>Status</span><select value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value as SupportKnowledgeArticleInput["status"] })}><option value="draft">Draft</option><option value="published">Published</option><option value="archived">Archived</option></select></label>
      <label className="sc-kb-field"><span>Language</span><select value={form.language || "en"} onChange={(event) => setForm({ ...form, language: event.target.value })}><option value="en">English</option><option value="ar">Arabic</option></select></label>
      <label className="sc-kb-field sc-kb-field--wide"><span>Short summary</span><input value={form.summary || ""} onChange={(event) => setForm({ ...form, summary: event.target.value })} maxLength={320} /></label>
      <label className="sc-kb-field sc-kb-field--wide"><span>SEO description</span><input value={form.seo_description || ""} onChange={(event) => setForm({ ...form, seo_description: event.target.value })} maxLength={160} /><small>{(form.seo_description || "").length}/160</small></label>
      <label className="sc-kb-field sc-kb-field--full"><span>Article content</span><textarea value={form.body} onChange={(event) => setForm({ ...form, body: event.target.value })} rows={12} maxLength={30000} required /></label>
      <label className="sc-kb-field sc-kb-field--wide"><span>Change note</span><input value={form.change_note || ""} onChange={(event) => setForm({ ...form, change_note: event.target.value })} maxLength={255} placeholder="What changed in this version?" /></label>
    </div>
    <div className="sc-kb-toggle-grid">
      <SupportToggle checked={form.all_websites} onChange={(checked) => setForm({ ...form, all_websites: checked, website_ids: checked ? [] : form.website_ids })} label="Available on all websites" description="Limit this article only when different websites need different answers." />
      <SupportToggle checked={form.is_featured} onChange={(checked) => setForm({ ...form, is_featured: checked })} label="Featured answer" description="Show before ordinary search results." />
    </div>
    {!form.all_websites ? <fieldset className="sc-kb-choices"><legend>Website availability</legend>{bootstrap.websites.map((website) => <label key={website.id}><input type="checkbox" checked={form.website_ids.includes(website.id)} onChange={() => toggleWebsite(website.id)} /><span><strong>{website.name}</strong><small>{website.domain}</small></span></label>)}</fieldset> : null}
    <fieldset className="sc-kb-choices"><legend>Related articles</legend>{articles.filter((item) => item.id !== article?.id && item.status !== "archived").slice(0, 12).map((item) => <label key={item.id}><input type="checkbox" checked={form.related_article_ids?.includes(item.id) || false} onChange={() => toggleRelated(item.id)} /><span><strong>{item.title}</strong><small>{item.category_name || "Uncategorized"}</small></span></label>)}</fieldset>
    {error ? <SupportState kind="error" title="Article could not be saved" description={error} /> : null}
    <div className="sc-kb-actions"><SupportButton type="button" variant="secondary" onClick={onDone}>Cancel</SupportButton><SupportButton type="submit" isLoading={save.isPending} disabled={!form.title.trim() || !form.body.trim() || (!form.all_websites && !form.website_ids.length)}>{article ? "Save article" : "Create article"}</SupportButton></div>
  </form>;
}

function RevisionPanel({ article, onClose }: { article: SupportKnowledgeArticle; onClose: () => void }) {
  const queryClient = useQueryClient();
  const revisions = useQuery({ queryKey: ["support-knowledge-revisions", article.id], queryFn: ({ signal }) => supportApi.listKnowledgeRevisions(article.id, signal) });
  const restore = useMutation({ mutationFn: (revision: SupportKnowledgeRevision) => supportApi.restoreKnowledgeRevision(article.id, revision.id), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["support-knowledge-articles"] }); onClose(); } });
  return <div className="sc-kb-revisions"><div className="sc-kb-revisions__head"><div><strong>Version history</strong><span>{article.title}</span></div><button type="button" onClick={onClose} aria-label="Close">×</button></div>{revisions.isLoading ? <SupportState kind="loading" title="Loading versions" /> : revisions.data?.map((revision) => <article key={revision.id}><div><strong>Version {revision.version}</strong><span>{new Date(revision.created_at).toLocaleString()} · {revision.created_by?.username || "System"}</span><small>{revision.change_note || "No change note"}</small></div><SupportButton variant="secondary" size="sm" onClick={() => restore.mutate(revision)} disabled={restore.isPending}>Restore</SupportButton></article>)}</div>;
}

function CategoryManager() {
  const queryClient = useQueryClient(); const [name, setName] = useState(""); const [description, setDescription] = useState("");
  const categories = useQuery({ queryKey: ["support-knowledge-categories", true], queryFn: ({ signal }) => supportApi.listKnowledgeCategories(true, signal) });
  const create = useMutation({ mutationFn: () => supportApi.createKnowledgeCategory({ name: name.trim(), description: description.trim() }), onSuccess: async () => { setName(""); setDescription(""); await queryClient.invalidateQueries({ queryKey: ["support-knowledge-categories"] }); } });
  const remove = useMutation({ mutationFn: (id: string) => supportApi.removeKnowledgeCategory(id), onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-knowledge-categories"] }) });
  return <SupportSurface className="sc-kb-categories"><div className="sc-kb-section-head"><div><span>Organization</span><h2>Categories</h2></div></div><div className="sc-kb-category-create"><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Category name" /><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Description" /><SupportButton size="sm" onClick={() => create.mutate()} disabled={!name.trim()}>Add</SupportButton></div>{categories.data?.filter((item) => item.is_active).map((item) => <div className="sc-kb-category-row" key={item.id}><div><strong>{item.name}</strong><span>{item.description || "No description"} · {item.article_count || 0} published</span></div><SupportButton variant="ghost" size="sm" onClick={() => remove.mutate(item.id)}>Archive</SupportButton></div>)}</SupportSurface>;
}

export function SupportKnowledgeBase({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const queryClient = useQueryClient(); const owner = bootstrap.role === "owner";
  const [search, setSearch] = useState(""); const [website, setWebsite] = useState(""); const [status, setStatus] = useState(owner ? "" : "published"); const [category, setCategory] = useState("");
  const [selected, setSelected] = useState<SupportKnowledgeArticle | null>(null); const [editing, setEditing] = useState<SupportKnowledgeArticle | null>(null); const [editorOpen, setEditorOpen] = useState(false); const [revisionArticle, setRevisionArticle] = useState<SupportKnowledgeArticle | null>(null);
  const settings = useQuery({ queryKey: ["support-knowledge-settings"], queryFn: ({ signal }) => supportApi.getKnowledgeSettings(signal) });
  const categories = useQuery({ queryKey: ["support-knowledge-categories", false], queryFn: ({ signal }) => supportApi.listKnowledgeCategories(false, signal) });
  const articles = useQuery({ queryKey: ["support-knowledge-articles", search, website, status, category], queryFn: ({ signal }) => supportApi.listKnowledgeArticles({ q: search || undefined, website: website || undefined, status: status || undefined, category: category || undefined }, signal) });
  useEffect(() => { if (!selected && articles.data?.length) setSelected(articles.data[0]); else if (selected) setSelected(articles.data?.find((item) => item.id === selected.id) || articles.data?.[0] || null); }, [articles.data]);
  const updateSettings = useMutation({ mutationFn: (payload: Partial<SupportKnowledgeSettings>) => supportApi.updateKnowledgeSettings(payload), onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-knowledge-settings"] }) });
  const archive = useMutation({ mutationFn: (id: string) => supportApi.removeKnowledgeArticle(id), onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-knowledge-articles"] }) });
  const restore = useMutation({ mutationFn: (id: string) => supportApi.restoreKnowledgeArticle(id), onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-knowledge-articles"] }) });
  const metrics = useMemo(() => { const rows = articles.data || []; const views = rows.reduce((sum, item) => sum + item.view_count, 0); const feedback = rows.filter((item) => item.helpful_rate != null); return { published: rows.filter((item) => item.status === "published").length, views, helpful: feedback.length ? Math.round(feedback.reduce((sum, item) => sum + (item.helpful_rate || 0), 0) / feedback.length) : 0 }; }, [articles.data]);

  return <SupportPage title="Knowledge" description="Create approved answers for agents and customer self-service." actions={owner ? <SupportButton onClick={() => { setEditing(null); setEditorOpen(true); }}>New article</SupportButton> : undefined}>
    <div className="sc-kb-metrics"><article><span>Published</span><strong>{metrics.published}</strong></article><article><span>Article views</span><strong>{metrics.views.toLocaleString()}</strong></article><article><span>Helpful rate</span><strong>{metrics.helpful}%</strong></article><article><span>Widget self-service</span><strong>{settings.data?.show_in_widget ? "On" : "Off"}</strong></article></div>
    {owner && settings.data ? <SupportSurface className="sc-kb-settings"><SupportToggle checked={settings.data.enabled} onChange={(checked) => updateSettings.mutate({ enabled: checked })} label="Knowledge base enabled" /><SupportToggle checked={settings.data.show_in_widget} onChange={(checked) => updateSettings.mutate({ show_in_widget: checked })} label="Show in widget" /><SupportToggle checked={settings.data.allow_article_feedback} onChange={(checked) => updateSettings.mutate({ allow_article_feedback: checked })} label="Collect article feedback" /></SupportSurface> : null}
    <div className="sc-kb-layout">
      <aside className="sc-kb-sidebar"><div className="sc-kb-sidebar__title">Categories</div><button className={!category ? "is-active" : ""} onClick={() => setCategory("")}>All articles</button>{categories.data?.map((item) => <button className={category === item.id ? "is-active" : ""} key={item.id} onClick={() => setCategory(item.id)}>{item.name}<span>{item.article_count || 0}</span></button>)}</aside>
      <SupportSurface className="sc-kb-list-panel"><div className="sc-kb-toolbar"><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search articles" /><select value={website} onChange={(event) => setWebsite(event.target.value)}><option value="">All websites</option>{bootstrap.websites.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select>{owner ? <select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option><option value="published">Published</option><option value="draft">Draft</option><option value="archived">Archived</option></select> : null}</div>{articles.isLoading ? <SupportState kind="loading" title="Loading articles" /> : !articles.data?.length ? <SupportState title="No articles found" description="Create an article or change the current filters." /> : <div className="sc-kb-table">{articles.data.map((article) => <button className={selected?.id === article.id ? "is-selected" : ""} key={article.id} onClick={() => setSelected(article)}><div><strong>{article.title}</strong><span>{article.summary || article.body.slice(0, 100)}</span></div><small>{article.category_name || "Uncategorized"}</small><SupportBadge tone={article.status === "published" ? "success" : article.status === "archived" ? "danger" : "neutral"}>{article.status}</SupportBadge><small>{article.helpful_rate == null ? "—" : `${article.helpful_rate}%`}</small><small>{article.view_count}</small></button>)}</div>}</SupportSurface>
      <aside className="sc-kb-detail">{selected ? <><div className="sc-kb-detail__head"><div><SupportBadge tone={selected.status === "published" ? "success" : selected.status === "archived" ? "danger" : "neutral"}>{selected.status}</SupportBadge><h2>{selected.title}</h2><p>{selected.category_name || "Uncategorized"} · {selected.language.toUpperCase()} · Updated {new Date(selected.updated_at).toLocaleDateString()}</p></div></div><div className="sc-kb-detail__body"><p>{selected.summary}</p><div className="sc-kb-content-preview">{selected.body}</div></div><dl className="sc-kb-facts"><div><dt>Websites</dt><dd>{selected.all_websites ? "All websites" : selected.website_names.join(", ")}</dd></div><div><dt>Feedback</dt><dd>{selected.helpful_rate == null ? "No feedback" : `${selected.helpful_rate}% helpful`}</dd></div><div><dt>Versions</dt><dd>{selected.revision_count}</dd></div><div><dt>Related</dt><dd>{selected.related_articles.length || "None"}</dd></div></dl>{owner ? <div className="sc-kb-detail__actions"><SupportButton variant="secondary" onClick={() => { setEditing(selected); setEditorOpen(true); }}>Edit</SupportButton><SupportButton variant="ghost" onClick={() => setRevisionArticle(selected)}>Versions</SupportButton>{selected.status === "archived" ? <SupportButton onClick={() => restore.mutate(selected.id)}>Restore draft</SupportButton> : <SupportButton variant="danger" onClick={() => archive.mutate(selected.id)}>Archive</SupportButton>}</div> : null}</> : <SupportState title="Select an article" />}</aside>
    </div>
    {owner ? <CategoryManager /> : null}
    <SupportModal open={editorOpen} title={editing ? "Edit article" : "New article"} onClose={() => setEditorOpen(false)} size="lg"><ArticleEditor bootstrap={bootstrap} article={editing} articles={articles.data || []} onDone={() => setEditorOpen(false)} /></SupportModal>
    {revisionArticle ? <RevisionPanel article={revisionArticle} onClose={() => setRevisionArticle(null)} /> : null}
  </SupportPage>;
}
