import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type {
  SupportBootstrap,
  SupportKnowledgeArticle,
  SupportKnowledgeArticleInput,
  SupportKnowledgeSettings,
} from "../../types/support";
import { MessengerSectionHeader } from "../pages/MessengerPageHeader";

const EMPTY_ARTICLE: SupportKnowledgeArticleInput = {
  category_id: null,
  title: "",
  summary: "",
  body: "",
  status: "draft",
  all_websites: true,
  website_ids: [],
  is_featured: false,
};

function ArticleEditor({
  bootstrap,
  article,
  onDone,
}: {
  bootstrap: SupportBootstrap;
  article: SupportKnowledgeArticle | null;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const categories = useQuery({
    queryKey: ["support-knowledge-categories", true],
    queryFn: ({ signal }) => supportApi.listKnowledgeCategories(true, signal),
  });
  const [form, setForm] = useState<SupportKnowledgeArticleInput>(EMPTY_ARTICLE);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!article) {
      setForm(EMPTY_ARTICLE);
      return;
    }
    setForm({
      category_id: article.category || null,
      title: article.title,
      summary: article.summary,
      body: article.body,
      status: article.status,
      all_websites: article.all_websites,
      website_ids: article.website_ids,
      is_featured: article.is_featured,
    });
  }, [article]);

  const save = useMutation({
    mutationFn: () =>
      article
        ? supportApi.updateKnowledgeArticle(article.id, form)
        : supportApi.createKnowledgeArticle(form),
    onMutate: () => setError(null),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["support-knowledge-articles"] }),
        queryClient.invalidateQueries({ queryKey: ["support-knowledge-categories"] }),
      ]);
      setForm(EMPTY_ARTICLE);
      onDone();
    },
    onError: (reason) => setError(parseApiError(reason, "The article could not be saved.").message),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!form.title.trim() || !form.body.trim()) return;
    save.mutate();
  };

  const toggleWebsite = (websiteId: string) => {
    setForm((current) => ({
      ...current,
      website_ids: current.website_ids.includes(websiteId)
        ? current.website_ids.filter((id) => id !== websiteId)
        : [...current.website_ids, websiteId],
    }));
  };

  return (
    <form className="ms-support-kb-editor" onSubmit={submit}>
      <div className="ms-support-kb-editor__grid">
        <label>
          <span>Article title</span>
          <input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} maxLength={180} required />
        </label>
        <label>
          <span>Category</span>
          <select value={form.category_id || ""} onChange={(event) => setForm({ ...form, category_id: event.target.value || null })}>
            <option value="">No category</option>
            {categories.data?.filter((item) => item.is_active).map((category) => <option value={category.id} key={category.id}>{category.name}</option>)}
          </select>
        </label>
        <label>
          <span>Status</span>
          <select value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value as SupportKnowledgeArticleInput["status"] })}>
            <option value="draft">Draft</option>
            <option value="published">Published</option>
            <option value="archived">Archived</option>
          </select>
        </label>
      </div>
      <label>
        <span>Short summary</span>
        <input value={form.summary || ""} onChange={(event) => setForm({ ...form, summary: event.target.value })} maxLength={320} placeholder="A concise answer preview for search results" />
      </label>
      <label>
        <span>Article answer</span>
        <textarea value={form.body} onChange={(event) => setForm({ ...form, body: event.target.value })} rows={10} maxLength={30000} required placeholder="Write the complete answer visitors and agents can use." />
      </label>
      <div className="ms-support-kb-scope">
        <label className="ms-support-toggle-row">
          <input type="checkbox" checked={form.all_websites} onChange={(event) => setForm({ ...form, all_websites: event.target.checked, website_ids: event.target.checked ? [] : form.website_ids })} />
          <span><strong>Available on all websites</strong><small>Turn this off to limit the article to selected websites.</small></span>
        </label>
        <label className="ms-support-toggle-row">
          <input type="checkbox" checked={form.is_featured} onChange={(event) => setForm({ ...form, is_featured: event.target.checked })} />
          <span><strong>Featured answer</strong><small>Show this article before normal search results.</small></span>
        </label>
      </div>
      {!form.all_websites ? (
        <fieldset className="ms-support-fieldset">
          <legend>Website availability</legend>
          <div className="ms-support-website-choices">
            {bootstrap.websites.map((website) => (
              <label className="ms-support-choice" key={website.id}>
                <input type="checkbox" checked={form.website_ids.includes(website.id)} onChange={() => toggleWebsite(website.id)} />
                <span><strong>{website.name}</strong><small>{website.domain}</small></span>
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}
      {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
      <div className="ms-support-form-actions">
        {article ? <button type="button" className="ms-button ms-button--ghost" onClick={onDone}>Cancel</button> : null}
        <button type="submit" className="ms-button ms-button--primary" disabled={save.isPending || !form.title.trim() || !form.body.trim() || (!form.all_websites && !form.website_ids.length)}>
          {save.isPending ? "Saving…" : article ? "Save article" : "Create article"}
        </button>
      </div>
    </form>
  );
}

function CategoryManager() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const categories = useQuery({
    queryKey: ["support-knowledge-categories", true],
    queryFn: ({ signal }) => supportApi.listKnowledgeCategories(true, signal),
  });
  const create = useMutation({
    mutationFn: () => supportApi.createKnowledgeCategory({ name: name.trim(), description: description.trim() }),
    onMutate: () => setError(null),
    onSuccess: async () => {
      setName("");
      setDescription("");
      await queryClient.invalidateQueries({ queryKey: ["support-knowledge-categories"] });
    },
    onError: (reason) => setError(parseApiError(reason, "The category could not be created.").message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => supportApi.removeKnowledgeCategory(id),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["support-knowledge-categories"] }),
        queryClient.invalidateQueries({ queryKey: ["support-knowledge-articles"] }),
      ]);
    },
  });

  return (
    <section className="ms-page-surface ms-page-surface--padded">
      <MessengerSectionHeader eyebrow="Organization" title="Article categories" description="Categories are shared across Support Chat and only published categories appear to visitors." />
      <div className="ms-support-kb-category-form">
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Category name" maxLength={100} />
        <input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Optional description" maxLength={255} />
        <button type="button" className="ms-button ms-button--primary" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>Add category</button>
      </div>
      {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
      <div className="ms-support-kb-category-list">
        {categories.data?.filter((item) => item.is_active).map((category) => (
          <div className="ms-page-row" key={category.id}>
            <div className="ms-page-row__copy"><strong>{category.name}</strong><span>{category.description || "No description"} · {category.article_count || 0} published</span></div>
            <button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={() => remove.mutate(category.id)} disabled={remove.isPending}>Archive</button>
          </div>
        ))}
      </div>
    </section>
  );
}

export function SupportKnowledgeBase({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const queryClient = useQueryClient();
  const owner = bootstrap.role === "owner";
  const [search, setSearch] = useState("");
  const [website, setWebsite] = useState("");
  const [status, setStatus] = useState(owner ? "" : "published");
  const [editing, setEditing] = useState<SupportKnowledgeArticle | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const settings = useQuery({
    queryKey: ["support-knowledge-settings"],
    queryFn: ({ signal }) => supportApi.getKnowledgeSettings(signal),
  });
  const articles = useQuery({
    queryKey: ["support-knowledge-articles", search, website, status],
    queryFn: ({ signal }) => supportApi.listKnowledgeArticles({ q: search || undefined, website: website || undefined, status: status || undefined }, signal),
  });
  const updateSettings = useMutation({
    mutationFn: (payload: Partial<SupportKnowledgeSettings>) => supportApi.updateKnowledgeSettings(payload),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-knowledge-settings"] }),
  });
  const archive = useMutation({
    mutationFn: (id: string) => supportApi.removeKnowledgeArticle(id),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["support-knowledge-articles"] }),
  });

  const publishedCount = useMemo(() => articles.data?.filter((article) => article.status === "published").length || 0, [articles.data]);

  return (
    <div className="ms-support-kb-stack">
      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader eyebrow="Self-service" title="Knowledge base" description="Visitors can search published answers before starting a chat. Agents can insert the same approved answers while replying." actions={owner ? <button type="button" className="ms-button ms-button--primary ms-button--compact" onClick={() => { setEditing(null); setShowCreate((value) => !value); }}>{showCreate ? "Close editor" : "New article"}</button> : undefined} />
        <div className="ms-support-kb-summary">
          <span><strong>{publishedCount}</strong><small>Published in this view</small></span>
          <span><strong>{settings.data?.show_in_widget ? "On" : "Off"}</strong><small>Widget self-service</small></span>
          <span><strong>{settings.data?.max_suggestions || 0}</strong><small>Search suggestions</small></span>
        </div>
        {owner && settings.data ? (
          <div className="ms-support-kb-settings">
            <label className="ms-support-toggle-row"><input type="checkbox" checked={settings.data.enabled} onChange={(event) => updateSettings.mutate({ enabled: event.target.checked })} /><span><strong>Enable knowledge base</strong><small>Keep articles available to the Support team.</small></span></label>
            <label className="ms-support-toggle-row"><input type="checkbox" checked={settings.data.show_in_widget} onChange={(event) => updateSettings.mutate({ show_in_widget: event.target.checked })} /><span><strong>Show in website widget</strong><small>Let visitors search answers before starting a conversation.</small></span></label>
            <label className="ms-support-toggle-row"><input type="checkbox" checked={settings.data.allow_article_feedback} onChange={(event) => updateSettings.mutate({ allow_article_feedback: event.target.checked })} /><span><strong>Article feedback</strong><small>Allow visitors to mark answers helpful or not helpful.</small></span></label>
            <label className="ms-support-kb-limit"><span>Maximum suggestions</span><input type="number" min={1} max={10} value={settings.data.max_suggestions} onChange={(event) => updateSettings.mutate({ max_suggestions: Math.max(1, Math.min(10, Number(event.target.value) || 1)) })} /></label>
          </div>
        ) : null}
      </section>

      {owner && showCreate ? <section className="ms-page-surface ms-page-surface--padded"><MessengerSectionHeader eyebrow="Article editor" title={editing ? `Edit ${editing.title}` : "Create a support answer"} description="Only published articles can appear in the website widget or agent reply picker." /><ArticleEditor bootstrap={bootstrap} article={editing} onDone={() => { setEditing(null); setShowCreate(false); }} /></section> : null}

      <section className="ms-page-surface ms-page-surface--padded">
        <MessengerSectionHeader eyebrow="Answers" title="Support articles" description={owner ? "Search, review, publish, and limit answers by website." : "Search published answers available to your assigned websites."} />
        <div className="ms-support-kb-filters">
          <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search article title or answer" />
          <select value={website} onChange={(event) => setWebsite(event.target.value)}><option value="">All websites</option>{bootstrap.websites.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select>
          {owner ? <select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option><option value="published">Published</option><option value="draft">Draft</option><option value="archived">Archived</option></select> : null}
        </div>
        <div className="ms-support-kb-list">
          {articles.isLoading ? <div className="ms-support-empty">Loading articles…</div> : null}
          {!articles.isLoading && !articles.data?.length ? <div className="ms-support-empty">No knowledge articles match this view.</div> : null}
          {articles.data?.map((article) => (
            <article className="ms-support-kb-card" key={article.id}>
              <div className="ms-support-kb-card__top"><div><span>{article.category_name || "Uncategorized"}</span><h2>{article.title}</h2></div><span className={`ms-page-badge${article.status === "published" ? " ms-page-badge--strong" : ""}`}>{article.status}</span></div>
              <p>{article.summary || article.body.slice(0, 180)}</p>
              <div className="ms-support-kb-card__meta"><span>{article.all_websites ? "All websites" : article.website_names.join(", ")}</span><span>{article.view_count} views</span><span>{article.helpful_rate == null ? "No feedback" : `${article.helpful_rate}% helpful`}</span></div>
              {owner ? <div className="ms-support-form-actions"><button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={() => { setEditing(article); setShowCreate(true); }}>Edit</button>{article.status !== "archived" ? <button type="button" className="ms-button ms-button--danger ms-button--compact" onClick={() => archive.mutate(article.id)} disabled={archive.isPending}>Archive</button> : null}</div> : null}
            </article>
          ))}
        </div>
      </section>
      {owner ? <CategoryManager /> : null}
    </div>
  );
}
