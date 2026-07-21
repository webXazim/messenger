import { useEffect, useMemo, useState, type FormEvent } from "react";
import { plainTextFromHtml, SupportRichTextEditor } from "./SupportRichTextEditor";
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

function ArticleEditor({ bootstrap, article, articles: _articles, onDone }: { bootstrap: SupportBootstrap; article: SupportKnowledgeArticle | null; articles: SupportKnowledgeArticle[]; onDone: () => void }) {
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
      status: article.status === "archived" ? "draft" : article.status,
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
      setForm(EMPTY_ARTICLE);
      onDone();
    },
    onError: (reason) => setError(parseApiError(reason, "The article could not be saved.").message),
  });

  const bodyText = plainTextFromHtml(form.body);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (form.title.trim() && bodyText && (form.all_websites || form.website_ids.length)) save.mutate();
  };
  const toggleWebsite = (id: string) => setForm((current) => ({ ...current, website_ids: current.website_ids.includes(id) ? current.website_ids.filter((value) => value !== id) : [...current.website_ids, id] }));

  return <form className="sc-kb-editor sc-kb-editor--focused" onSubmit={submit}>
    <main className="sc-kb-editor__main">
      <section className="sc-kb-article-basics">
        <header className="sc-kb-editor-card__head">
          <span className="sc-kb-editor-card__step">1</span>
          <div><h3>Article details</h3><p>Give customers a clear reason to open this article.</p></div>
        </header>
        <label className="sc-kb-field sc-kb-field--title">
          <span>Article title</span>
          <input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} maxLength={180} required placeholder="Write a clear title customers will recognize" autoFocus />
          <small>{form.title.length}/180</small>
        </label>
        <label className="sc-kb-field sc-kb-field--summary">
          <span>Summary</span>
          <textarea value={form.summary || ""} onChange={(event) => setForm({ ...form, summary: event.target.value })} maxLength={320} rows={2} placeholder="Briefly state what the customer will learn or complete." />
          <small>{(form.summary || "").length}/320</small>
        </label>
      </section>

      <section className="sc-kb-editor-section sc-kb-editor-section--content sc-kb-editor-section--wide">
        <div className="sc-kb-editor-section__head">
          <div className="sc-kb-editor-card__head">
            <span className="sc-kb-editor-card__step">2</span>
            <div><span>Article content</span><h3>Customer-facing answer</h3></div>
          </div>
          <small>{bodyText.split(/\s+/).filter(Boolean).length.toLocaleString()} words · {bodyText.length.toLocaleString()} characters</small>
        </div>
        <SupportRichTextEditor value={form.body} onChange={(body) => setForm((current) => ({ ...current, body }))} direction={form.language === "ar" ? "rtl" : "ltr"} />
        <p className="sc-kb-security-note">Formatting is sanitized on the server. Scripts, unsafe embeds, inline styles, and unsupported HTML are removed before the article is stored.</p>
      </section>
    </main>

    <aside className="sc-kb-editor__sidebar sc-kb-editor__sidebar--compact">
      <div className="sc-kb-sidebar-heading"><div><span>Article settings</span><p>Control where and when this answer appears.</p></div><SupportBadge tone={form.status === "published" ? "success" : "neutral"}>{form.status === "published" ? "Published" : "Draft"}</SupportBadge></div>

      <section className="sc-kb-sidebar-section">
        <div className="sc-kb-sidebar-section__head"><span>Publishing</span><small>Visibility</small></div>
        <label className="sc-kb-field"><span>Status</span><select value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value as SupportKnowledgeArticleInput["status"] })}><option value="draft">Draft</option><option value="published">Published</option></select><small>{form.status === "published" ? "Visible to customers after saving." : "Visible only to the support team."}</small></label>
        {article ? <label className="sc-kb-field sc-kb-field--version-note"><span>Version note</span><textarea value={form.change_note || ""} onChange={(event) => setForm({ ...form, change_note: event.target.value })} maxLength={255} rows={3} placeholder="Briefly record what changed in this version." /><small>Internal only.</small></label> : null}
      </section>

      <section className="sc-kb-sidebar-section">
        <div className="sc-kb-sidebar-section__head"><span>Organization</span><small>Findability</small></div>
        <label className="sc-kb-field"><span>Category</span><select value={form.category_id || ""} onChange={(event) => setForm({ ...form, category_id: event.target.value || null })}><option value="">Uncategorized</option>{categories.data?.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="sc-kb-field"><span>Language</span><select value={form.language || "en"} onChange={(event) => setForm({ ...form, language: event.target.value })}><option value="en">English</option><option value="ar">Arabic</option></select></label>
      </section>

      <section className="sc-kb-sidebar-section">
        <div className="sc-kb-sidebar-section__head"><span>Distribution</span><small>Websites</small></div>
        <div className="sc-kb-availability-control">
          <SupportToggle checked={form.all_websites} onChange={(checked) => setForm({ ...form, all_websites: checked, website_ids: checked ? [] : form.website_ids })} label="Available on all websites" description="Turn off to choose specific support websites." />
          {!form.all_websites ? <div className="sc-kb-sidebar-choices sc-kb-sidebar-choices--compact">{bootstrap.websites.map((website) => <label key={website.id}><input type="checkbox" checked={form.website_ids.includes(website.id)} onChange={() => toggleWebsite(website.id)} /><span><strong>{website.name}</strong><small>{website.domain}</small></span></label>)}</div> : null}
        </div>
      </section>
    </aside>

    {error ? <SupportState kind="error" title="Article could not be saved" description={error} /> : null}
    <footer className="sc-kb-actions sc-kb-actions--sticky">
      <div><strong>{form.status === "published" ? "Ready to publish" : "Internal draft"}</strong><small>{form.all_websites ? "Available on all websites" : `${form.website_ids.length} selected website${form.website_ids.length === 1 ? "" : "s"}`}</small></div>
      <div><SupportButton type="button" variant="secondary" onClick={onDone}>Cancel</SupportButton><SupportButton type="submit" isLoading={save.isPending} disabled={!form.title.trim() || !bodyText || (!form.all_websites && !form.website_ids.length)}>{form.status === "published" ? (article ? "Save and publish" : "Publish article") : (article ? "Save draft" : "Create draft")}</SupportButton></div>
    </footer>
  </form>;
}

function RevisionPanel({ article, onClose }: { article: SupportKnowledgeArticle; onClose: () => void }) {
  const queryClient = useQueryClient();
  const revisions = useQuery({ queryKey: ["support-knowledge-revisions", article.id], queryFn: ({ signal }) => supportApi.listKnowledgeRevisions(article.id, signal) });
  const restore = useMutation({ mutationFn: (revision: SupportKnowledgeRevision) => supportApi.restoreKnowledgeRevision(article.id, revision.id), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["support-knowledge-articles"] }); onClose(); } });
  return <div className="sc-kb-revisions"><div className="sc-kb-revisions__head"><div><strong>Version history</strong><span>{article.title}</span></div><button type="button" onClick={onClose} aria-label="Close">×</button></div>{revisions.isLoading ? <SupportState kind="loading" title="Loading versions" /> : revisions.data?.map((revision) => <article key={revision.id}><div><strong>Version {revision.version}</strong><span>{new Date(revision.created_at).toLocaleString()} · {revision.created_by?.username || "System"}</span><small>{revision.change_note || "Version note not provided"}</small></div><SupportButton variant="secondary" size="sm" onClick={() => restore.mutate(revision)} disabled={restore.isPending}>Restore</SupportButton></article>)}</div>;
}

function CategoryManager() {
  const queryClient = useQueryClient(); const [name, setName] = useState(""); const [description, setDescription] = useState("");
  const categories = useQuery({ queryKey: ["support-knowledge-categories", true], queryFn: ({ signal }) => supportApi.listKnowledgeCategories(true, signal) });
  const create = useMutation({ mutationFn: () => supportApi.createKnowledgeCategory({ name: name.trim(), description: description.trim() }), onSuccess: async () => { setName(""); setDescription(""); await queryClient.invalidateQueries({ queryKey: ["support-knowledge-categories"] }); } });
  const remove = useMutation({ mutationFn: (id: string) => supportApi.removeKnowledgeCategory(id), onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-knowledge-categories"] }) });
  return <SupportSurface className="sc-kb-categories"><div className="sc-kb-section-head"><div><span>Organization</span><h2>Categories</h2></div></div><div className="sc-kb-category-create"><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Category name" /><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Description" /><SupportButton size="sm" onClick={() => create.mutate()} disabled={!name.trim()}>Add</SupportButton></div>{categories.data?.filter((item) => item.is_active).map((item) => <div className="sc-kb-category-row" key={item.id}><div><strong>{item.name}</strong><span>{item.description || "Category description not provided"} · {item.article_count || 0} published</span></div><SupportButton variant="ghost" size="sm" onClick={() => remove.mutate(item.id)}>Archive</SupportButton></div>)}</SupportSurface>;
}

export function SupportKnowledgeBase({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const queryClient = useQueryClient();
  const owner = bootstrap.role === "owner";
  const canManage = owner || Boolean(bootstrap.agents[0]?.can_manage_knowledge);
  const [search, setSearch] = useState("");
  const [website, setWebsite] = useState("");
  const [status, setStatus] = useState(canManage ? "" : "published");
  const [category, setCategory] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [categoryOpen, setCategoryOpen] = useState(false);
  const [revisionArticle, setRevisionArticle] = useState<SupportKnowledgeArticle | null>(null);
  const [routeArticleId, setRouteArticleId] = useState<string | "new" | null>(() => {
    const match = window.location.pathname.match(/\/support\/knowledge\/articles\/([^/]+)/);
    return match?.[1] || null;
  });

  const settings = useQuery({
    queryKey: ["support-knowledge-settings"],
    queryFn: ({ signal }) => supportApi.getKnowledgeSettings(signal),
  });
  const categories = useQuery({
    queryKey: ["support-knowledge-categories", false],
    queryFn: ({ signal }) => supportApi.listKnowledgeCategories(false, signal),
  });
  const articles = useQuery({
    queryKey: ["support-knowledge-articles", search, website, status, category],
    queryFn: ({ signal }) =>
      supportApi.listKnowledgeArticles(
        {
          q: search || undefined,
          website: website || undefined,
          status: status || undefined,
          category: category || undefined,
        },
        signal,
      ),
  });

  useEffect(() => {
    const handlePopState = () => {
      const match = window.location.pathname.match(/\/support\/knowledge\/articles\/([^/]+)/);
      setRouteArticleId(match?.[1] || null);
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    const visibleIds = new Set((articles.data || []).map((article) => article.id));
    setSelectedIds((current) => current.filter((id) => visibleIds.has(id)));
  }, [articles.data]);

  const updateSettings = useMutation({
    mutationFn: (payload: Partial<SupportKnowledgeSettings>) => supportApi.updateKnowledgeSettings(payload),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-knowledge-settings"] }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => supportApi.removeKnowledgeArticle(id),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["support-knowledge-articles"] }),
        queryClient.invalidateQueries({ queryKey: ["support-knowledge-categories"] }),
      ]);
    },
  });
  const bulkRemove = useMutation({
    mutationFn: (ids: string[]) => supportApi.bulkDeleteKnowledgeArticles(ids),
    onSuccess: async () => {
      setSelectedIds([]);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["support-knowledge-articles"] }),
        queryClient.invalidateQueries({ queryKey: ["support-knowledge-categories"] }),
      ]);
    },
  });

  const metrics = useMemo(() => {
    const rows = articles.data || [];
    const feedback = rows.filter((item) => item.helpful_rate != null);
    return {
      published: rows.filter((item) => item.status === "published").length,
      drafts: rows.filter((item) => item.status === "draft").length,
      views: rows.reduce((sum, item) => sum + item.view_count, 0),
      helpful: feedback.length
        ? Math.round(feedback.reduce((sum, item) => sum + (item.helpful_rate || 0), 0) / feedback.length)
        : 0,
    };
  }, [articles.data]);

  const navigateToArticle = (articleId: string | "new") => {
    const nextPath = `/support/knowledge/articles/${articleId}`;
    window.history.pushState({}, "", nextPath);
    setRouteArticleId(articleId);
  };
  const returnToList = () => {
    window.history.pushState({}, "", "/support/knowledge");
    setRouteArticleId(null);
  };

  const routeArticle = routeArticleId && routeArticleId !== "new"
    ? articles.data?.find((article) => article.id === routeArticleId) || null
    : null;

  if (routeArticleId) {
    if (routeArticleId !== "new" && articles.isLoading) {
      return <SupportState kind="loading" title="Loading article" description="Preparing the article workspace." />;
    }
    if (routeArticleId !== "new" && !routeArticle) {
      return (
        <SupportState
          kind="error"
          title="Article unavailable"
          description="This article may have been deleted or is outside your website access."
          actionLabel="Back to articles"
          onAction={returnToList}
        />
      );
    }
    if (!canManage && routeArticle) {
      return (
        <article className="sc-kb-reader-page">
          <header className="sc-kb-article-page__head">
            <button className="sc-kb-back" type="button" onClick={returnToList}>← Articles</button>
            <div>
              <span>{routeArticle.category_name || "Knowledge article"}</span>
              <h2>{routeArticle.title}</h2>
              <p>
                Written by {routeArticle.created_by?.display_name || routeArticle.created_by?.username || "Support team"}
                {` · Updated ${new Date(routeArticle.updated_at).toLocaleDateString()}`}
              </p>
            </div>
          </header>
          {routeArticle.summary ? <p className="sc-kb-reader-page__summary">{routeArticle.summary}</p> : null}
          <div className="sc-kb-reader-page__content sc-article-content" dangerouslySetInnerHTML={{ __html: routeArticle.body }} />
        </article>
      );
    }
    if (!canManage && routeArticleId === "new") {
      return <SupportState kind="error" title="Article management access required" description="Your Support Chat permissions do not allow article creation." actionLabel="Back to articles" onAction={returnToList} />;
    }
    return (
      <div className="sc-kb-article-page">
        <header className="sc-kb-article-page__head">
          <button className="sc-kb-back" type="button" onClick={returnToList}>← Articles</button>
          <div>
            <span>{routeArticle ? "Knowledge article" : "New knowledge article"}</span>
            <h2>{routeArticle?.title || "Create article"}</h2>
            {routeArticle ? (
              <p>
                Written by {routeArticle.created_by?.display_name || routeArticle.created_by?.username || "Support team"}
                {routeArticle.updated_by ? ` · Last updated by ${routeArticle.updated_by.display_name || routeArticle.updated_by.username}` : ""}
              </p>
            ) : (
              <p>Create a customer-ready answer with controlled publishing and website availability.</p>
            )}
          </div>
        </header>
        <ArticleEditor
          bootstrap={bootstrap}
          article={routeArticle}
          articles={articles.data || []}
          onDone={returnToList}
        />
      </div>
    );
  }

  const allVisibleSelected = Boolean(articles.data?.length) && articles.data!.every((article) => selectedIds.includes(article.id));
  const toggleAll = () => {
    const visible = articles.data || [];
    setSelectedIds(allVisibleSelected ? [] : visible.map((article) => article.id));
  };

  return (
    <div className="sc-kb-page sc-kb-page--professional">
      <div className="sc-kb-topline">
        <div className="sc-kb-metrics sc-kb-metrics--flat">
          <article><span>Published articles</span><strong>{metrics.published}</strong></article>
          <article><span>Drafts</span><strong>{metrics.drafts}</strong></article>
          <article><span>Article views</span><strong>{metrics.views.toLocaleString()}</strong></article>
          <article><span>Helpful rate</span><strong>{metrics.helpful}%</strong></article>
        </div>
        {canManage ? (
          <div className="sc-kb-primary-actions">
            <details className="sc-kb-controls">
              <summary>Controls</summary>
              {settings.data ? (
                <div className="sc-kb-controls__menu">
                  <SupportToggle
                    checked={settings.data.enabled}
                    onChange={(checked) => updateSettings.mutate({ enabled: checked })}
                    label="Knowledge base enabled"
                    description="Allow approved articles to be used by the support team."
                  />
                  <SupportToggle
                    checked={settings.data.show_in_widget}
                    onChange={(checked) => updateSettings.mutate({ show_in_widget: checked })}
                    label="Show in widget"
                    description="Make published articles available to website visitors."
                  />
                  <SupportToggle
                    checked={settings.data.allow_article_feedback}
                    onChange={(checked) => updateSettings.mutate({ allow_article_feedback: checked })}
                    label="Collect article feedback"
                    description="Allow visitors to rate published answers."
                  />
                  <button type="button" onClick={() => setCategoryOpen(true)}>Manage categories</button>
                </div>
              ) : null}
            </details>
            <SupportButton onClick={() => navigateToArticle("new")}>＋ New article</SupportButton>
          </div>
        ) : null}
      </div>

      <div className="sc-kb-search-rail">
        <label className="sc-search-field">
          <span aria-hidden="true">⌕</span>
          <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search articles" />
        </label>
        <select value={website} onChange={(event) => setWebsite(event.target.value)}>
          <option value="">All websites</option>
          {bootstrap.websites.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
        </select>
        {canManage ? (
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            <option value="published">Published</option>
            <option value="draft">Draft</option>
            <option value="archived">Archived</option>
          </select>
        ) : null}
      </div>

      <section className="sc-kb-workspace-flat">
        <aside className="sc-kb-categories-rail">
          <div className="sc-kb-categories-rail__head">
            <strong>Categories</strong>
            {canManage ? <button type="button" onClick={() => setCategoryOpen(true)}>＋</button> : null}
          </div>
          <button className={!category ? "is-active" : ""} onClick={() => setCategory("")}>
            <span>All articles</span><small>{articles.data?.length || 0}</small>
          </button>
          {categories.data?.map((item) => (
            <button className={category === item.id ? "is-active" : ""} key={item.id} onClick={() => setCategory(item.id)}>
              <span>{item.name}</span><small>{item.article_count || 0}</small>
            </button>
          ))}
        </aside>

        <div className="sc-kb-articles-section">
          <header className="sc-kb-articles-section__head">
            <div><strong>{category ? categories.data?.find((item) => item.id === category)?.name : "All articles"}</strong><span>{articles.data?.length || 0} articles</span></div>
            {selectedIds.length ? (
              <div className="sc-kb-bulk-actions">
                <span>{selectedIds.length} selected</span>
                <SupportButton
                  variant="danger"
                  size="sm"
                  isLoading={bulkRemove.isPending}
                  onClick={() => {
                    if (window.confirm(`Delete ${selectedIds.length} selected article${selectedIds.length === 1 ? "" : "s"}? This cannot be undone.`)) {
                      bulkRemove.mutate(selectedIds);
                    }
                  }}
                >
                  Delete selected
                </SupportButton>
              </div>
            ) : null}
          </header>

          <div className="sc-kb-article-table" role="table" aria-label="Knowledge articles">
            <div className="sc-kb-article-row sc-kb-article-row--head" role="row">
              {canManage ? <input type="checkbox" checked={allVisibleSelected} onChange={toggleAll} aria-label="Select all visible articles" /> : null}
              <span>Title</span><span>Category</span><span>Website</span><span>Status</span><span>Written by</span><span>Updated</span><span />
            </div>
            {articles.isLoading ? (
              <SupportState kind="loading" title="Loading articles" />
            ) : !articles.data?.length ? (
              <SupportState
                title="No articles found"
                description={canManage ? "Create the first approved article or adjust the current filters." : "No published articles match the current filters."}
                actionLabel={canManage ? "New article" : undefined}
                onAction={canManage ? () => navigateToArticle("new") : undefined}
              />
            ) : (
              articles.data.map((article) => (
                <div className="sc-kb-article-row" role="row" key={article.id}>
                  {canManage ? (
                    <input
                      type="checkbox"
                      checked={selectedIds.includes(article.id)}
                      onChange={(event) => {
                        event.stopPropagation();
                        setSelectedIds((current) => event.target.checked ? [...current, article.id] : current.filter((id) => id !== article.id));
                      }}
                      aria-label={`Select ${article.title}`}
                    />
                  ) : null}
                  <button type="button" className="sc-kb-article-title" onClick={() => navigateToArticle(article.id)}>
                    <strong>{article.title}</strong>
                    <small>{article.summary || plainTextFromHtml(article.body).slice(0, 90)}</small>
                  </button>
                  <span>{article.category_name || "Uncategorized"}</span>
                  <span>{article.all_websites ? "All websites" : article.website_names.join(", ")}</span>
                  <span><SupportBadge tone={article.status === "published" ? "success" : article.status === "archived" ? "danger" : "neutral"}>{article.status}</SupportBadge></span>
                  <span>{article.created_by?.display_name || article.created_by?.username || "Support team"}</span>
                  <span>{new Date(article.updated_at).toLocaleDateString()}</span>
                  {canManage ? (
                    <button
                      type="button"
                      className="sc-kb-delete-one"
                      aria-label={`Delete ${article.title}`}
                      onClick={() => {
                        if (window.confirm(`Delete “${article.title}”? This cannot be undone.`)) remove.mutate(article.id);
                      }}
                    >
                      Delete
                    </button>
                  ) : <span />}
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      <SupportModal
        open={categoryOpen}
        title="Manage categories"
        description="Organize articles using customer-ready category names."
        onClose={() => setCategoryOpen(false)}
        size="lg"
      >
        <CategoryManager />
      </SupportModal>
      {revisionArticle ? <RevisionPanel article={revisionArticle} onClose={() => setRevisionArticle(null)} /> : null}
    </div>
  );
}
