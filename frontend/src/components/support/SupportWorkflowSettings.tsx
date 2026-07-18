import { useState, type CSSProperties, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type { SupportBootstrap, SupportCannedReply, SupportTag } from "../../types/support";

export function SupportWorkflowSettings({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState({ id: "", name: "", color: "#4f46e5" });
  const [replyDraft, setReplyDraft] = useState({
    id: "",
    website_id: "",
    shortcut: "",
    title: "",
    body: "",
  });

  const tagsQuery = useQuery({
    queryKey: ["support-tags"],
    queryFn: ({ signal }) => supportApi.listTags(signal),
  });
  const repliesQuery = useQuery({
    queryKey: ["support-canned-replies"],
    queryFn: ({ signal }) => supportApi.listCannedReplies(undefined, signal),
  });

  const refreshTags = () => queryClient.invalidateQueries({ queryKey: ["support-tags"] });
  const refreshReplies = () => queryClient.invalidateQueries({ queryKey: ["support-canned-replies"] });

  const saveTagMutation = useMutation({
    mutationFn: async () => {
      const payload = { name: tagDraft.name.trim(), color: tagDraft.color };
      return tagDraft.id
        ? supportApi.updateTag(tagDraft.id, payload)
        : supportApi.createTag(payload);
    },
    onMutate: () => setError(null),
    onSuccess: async () => {
      setTagDraft({ id: "", name: "", color: "#4f46e5" });
      await refreshTags();
    },
    onError: (mutationError) =>
      setError(parseApiError(mutationError, "The tag could not be saved.").message),
  });
  const removeTagMutation = useMutation({
    mutationFn: (tagId: string) => supportApi.removeTag(tagId),
    onSuccess: refreshTags,
    onError: (mutationError) =>
      setError(parseApiError(mutationError, "The tag could not be removed.").message),
  });

  const saveReplyMutation = useMutation({
    mutationFn: async () => {
      const payload = {
        website_id: replyDraft.website_id || null,
        shortcut: replyDraft.shortcut.trim(),
        title: replyDraft.title.trim(),
        body: replyDraft.body.trim(),
      };
      return replyDraft.id
        ? supportApi.updateCannedReply(replyDraft.id, payload)
        : supportApi.createCannedReply(payload);
    },
    onMutate: () => setError(null),
    onSuccess: async () => {
      setReplyDraft({ id: "", website_id: "", shortcut: "", title: "", body: "" });
      await refreshReplies();
    },
    onError: (mutationError) =>
      setError(parseApiError(mutationError, "The canned reply could not be saved.").message),
  });
  const removeReplyMutation = useMutation({
    mutationFn: (replyId: string) => supportApi.removeCannedReply(replyId),
    onSuccess: refreshReplies,
    onError: (mutationError) =>
      setError(parseApiError(mutationError, "The canned reply could not be removed.").message),
  });

  const submitTag = (event: FormEvent) => {
    event.preventDefault();
    if (!tagDraft.name.trim() || saveTagMutation.isPending) return;
    saveTagMutation.mutate();
  };
  const submitReply = (event: FormEvent) => {
    event.preventDefault();
    if (!replyDraft.shortcut.trim() || !replyDraft.title.trim() || !replyDraft.body.trim() || saveReplyMutation.isPending) return;
    saveReplyMutation.mutate();
  };

  const editTag = (tag: SupportTag) => setTagDraft({ id: tag.id, name: tag.name, color: tag.color });
  const editReply = (reply: SupportCannedReply) =>
    setReplyDraft({
      id: reply.id,
      website_id: reply.website_id || "",
      shortcut: reply.shortcut,
      title: reply.title,
      body: reply.body,
    });

  return (
    <div className="ms-support-workflow-settings">
      {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
      <section className="ms-page-surface ms-page-surface--padded">
        <div className="ms-support-workflow-heading">
          <div><span>Conversation organization</span><h2>Tags</h2><p>Create a small shared label set for every Support website.</p></div>
        </div>
        <div className="ms-support-workflow-grid">
          <form className="ms-support-workflow-form" onSubmit={submitTag}>
            <label><span>Name</span><input value={tagDraft.name} maxLength={80} onChange={(event) => setTagDraft((value) => ({ ...value, name: event.target.value }))} placeholder="Billing" /></label>
            <label><span>Color</span><input type="color" value={tagDraft.color} onChange={(event) => setTagDraft((value) => ({ ...value, color: event.target.value }))} /></label>
            <div className="ms-support-workflow-actions">
              <button className="ms-button ms-button--primary ms-button--compact" type="submit" disabled={!tagDraft.name.trim() || saveTagMutation.isPending}>{tagDraft.id ? "Save tag" : "Add tag"}</button>
              {tagDraft.id ? <button className="ms-button ms-button--ghost ms-button--compact" type="button" onClick={() => setTagDraft({ id: "", name: "", color: "#4f46e5" })}>Cancel</button> : null}
            </div>
          </form>
          <div className="ms-support-workflow-list">
            {tagsQuery.data?.length ? tagsQuery.data.map((tag) => (
              <div className="ms-support-workflow-row" key={tag.id}>
                <span className="ms-support-tag" style={{ "--support-tag-color": tag.color } as CSSProperties}>{tag.name}</span>
                <div><button type="button" onClick={() => editTag(tag)}>Edit</button><button type="button" onClick={() => removeTagMutation.mutate(tag.id)}>Remove</button></div>
              </div>
            )) : <div className="ms-support-empty">No tags have been created.</div>}
          </div>
        </div>
      </section>

      <section className="ms-page-surface ms-page-surface--padded">
        <div className="ms-support-workflow-heading">
          <div><span>Faster replies</span><h2>Canned replies</h2><p>Use one shortcut across all websites or restrict it to one website.</p></div>
        </div>
        <div className="ms-support-workflow-grid ms-support-workflow-grid--reply">
          <form className="ms-support-workflow-form" onSubmit={submitReply}>
            <label><span>Website</span><select value={replyDraft.website_id} onChange={(event) => setReplyDraft((value) => ({ ...value, website_id: event.target.value }))}><option value="">All websites</option>{bootstrap.websites.map((website) => <option value={website.id} key={website.id}>{website.name}</option>)}</select></label>
            <label><span>Shortcut</span><input value={replyDraft.shortcut} maxLength={40} onChange={(event) => setReplyDraft((value) => ({ ...value, shortcut: event.target.value }))} placeholder="/hello" /></label>
            <label><span>Title</span><input value={replyDraft.title} maxLength={120} onChange={(event) => setReplyDraft((value) => ({ ...value, title: event.target.value }))} placeholder="Welcome" /></label>
            <label className="ms-support-workflow-form__wide"><span>Reply text</span><textarea rows={5} value={replyDraft.body} maxLength={10000} onChange={(event) => setReplyDraft((value) => ({ ...value, body: event.target.value }))} placeholder="Hello, how can we help?" /></label>
            <div className="ms-support-workflow-actions ms-support-workflow-form__wide">
              <button className="ms-button ms-button--primary ms-button--compact" type="submit" disabled={!replyDraft.shortcut.trim() || !replyDraft.title.trim() || !replyDraft.body.trim() || saveReplyMutation.isPending}>{replyDraft.id ? "Save reply" : "Add reply"}</button>
              {replyDraft.id ? <button className="ms-button ms-button--ghost ms-button--compact" type="button" onClick={() => setReplyDraft({ id: "", website_id: "", shortcut: "", title: "", body: "" })}>Cancel</button> : null}
            </div>
          </form>
          <div className="ms-support-workflow-list">
            {repliesQuery.data?.length ? repliesQuery.data.map((reply) => (
              <div className="ms-support-workflow-row ms-support-workflow-row--reply" key={reply.id}>
                <div><strong>{reply.shortcut} · {reply.title}</strong><span>{reply.website_name || "All websites"}</span><p>{reply.body}</p></div>
                <div><button type="button" onClick={() => editReply(reply)}>Edit</button><button type="button" onClick={() => removeReplyMutation.mutate(reply.id)}>Remove</button></div>
              </div>
            )) : <div className="ms-support-empty">No canned replies have been created.</div>}
          </div>
        </div>
      </section>
    </div>
  );
}
