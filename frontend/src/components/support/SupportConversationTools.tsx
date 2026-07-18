import { useMemo, useState, type CSSProperties, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi } from "../../api/support";
import { ConfirmDialog } from "../ConfirmDialog";
import { parseApiError } from "../../lib/apiErrors";
import type { SupportBootstrap, SupportConversation } from "../../types/support";

function formatActivityTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function SupportConversationTools({ conversation }: { conversation: SupportConversation }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [deleteVisitorOpen, setDeleteVisitorOpen] = useState(false);
  const bootstrap = queryClient.getQueryData<SupportBootstrap>(["support-bootstrap"]);
  const isOwner = bootstrap?.role === "owner";
  const tagsQuery = useQuery({
    queryKey: ["support-tags"],
    queryFn: ({ signal }) => supportApi.listTags(signal),
  });
  const activityQuery = useQuery({
    queryKey: ["support-conversation-activity", conversation.id],
    queryFn: ({ signal }) => supportApi.getConversationActivity(conversation.id, signal),
    staleTime: 10_000,
  });

  const refreshConversation = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["support-conversations"] }),
      queryClient.invalidateQueries({ queryKey: ["support-conversation-messages", conversation.id] }),
      queryClient.invalidateQueries({ queryKey: ["support-conversation-activity", conversation.id] }),
    ]);
  };

  const tagsMutation = useMutation({
    mutationFn: (tagIds: string[]) => supportApi.updateConversationTags(conversation.id, tagIds),
    onMutate: () => setError(null),
    onSuccess: refreshConversation,
    onError: (mutationError) =>
      setError(parseApiError(mutationError, "Conversation tags could not be updated.").message),
  });
  const deleteVisitorMutation = useMutation({
    mutationFn: () => supportApi.requestVisitorDeletion(conversation.visitor.id),
    onMutate: () => setError(null),
    onSuccess: async () => {
      setDeleteVisitorOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["support-conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["support-deletion-requests"] });
    },
    onError: (mutationError) =>
      setError(parseApiError(mutationError, "Visitor data deletion could not be requested.").message),
  });
  const noteMutation = useMutation({
    mutationFn: (body: string) => supportApi.addConversationNote(conversation.id, body),
    onMutate: () => setError(null),
    onSuccess: async () => {
      setNote("");
      await refreshConversation();
    },
    onError: (mutationError) =>
      setError(parseApiError(mutationError, "The internal note could not be saved.").message),
  });

  const activityItems = useMemo(() => {
    const notes = (activityQuery.data?.notes || []).map((item) => ({
      id: `note-${item.id}`,
      kind: "note" as const,
      createdAt: item.created_at,
      title: `${item.author.display_name} added a note`,
      body: item.body,
    }));
    const events = (activityQuery.data?.events || [])
      .filter((item) => item.action !== "conversation.note_added")
      .map((item) => ({
        id: `event-${item.id}`,
        kind: "event" as const,
        createdAt: item.created_at,
        title: item.summary,
        body: "",
      }));
    return [...notes, ...events].sort(
      (left, right) => new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime(),
    );
  }, [activityQuery.data]);

  const selectedTagIds = new Set((conversation.tags || []).map((tag) => tag.id));
  const toggleTag = (tagId: string) => {
    const next = new Set(selectedTagIds);
    if (next.has(tagId)) next.delete(tagId);
    else next.add(tagId);
    tagsMutation.mutate(Array.from(next));
  };
  const submitNote = (event: FormEvent) => {
    event.preventDefault();
    if (!note.trim() || noteMutation.isPending) return;
    noteMutation.mutate(note.trim());
  };

  return (
    <div className="ms-support-conversation-tools">
      <section className="ms-support-detail-section">
        <div className="ms-support-detail-section__heading"><strong>Tags</strong><span>Team-only organization</span></div>
        <div className="ms-support-tag-picker">
          {tagsQuery.data?.length ? tagsQuery.data.map((tag) => (
            <button
              type="button"
              className={`ms-support-tag${selectedTagIds.has(tag.id) ? " is-selected" : ""}`}
              style={{ "--support-tag-color": tag.color } as CSSProperties}
              disabled={tagsMutation.isPending}
              onClick={() => toggleTag(tag.id)}
              aria-pressed={selectedTagIds.has(tag.id)}
              key={tag.id}
            >
              {tag.name}
            </button>
          )) : <span className="ms-support-detail-empty">No tags configured.</span>}
        </div>
      </section>

      <section className="ms-support-detail-section">
        <div className="ms-support-detail-section__heading"><strong>Internal note</strong><span>Never shown to visitors</span></div>
        <form className="ms-support-note-form" onSubmit={submitNote}>
          <textarea rows={3} value={note} maxLength={10000} onChange={(event) => setNote(event.target.value)} placeholder="Add context for the support team" />
          <button className="ms-button ms-button--ghost ms-button--compact" type="submit" disabled={!note.trim() || noteMutation.isPending}>{noteMutation.isPending ? "Saving…" : "Add note"}</button>
        </form>
      </section>

      {isOwner ? (
        <section className="ms-support-detail-section">
          <div className="ms-support-detail-section__heading"><strong>Visitor privacy</strong><span>Owner-only destructive action</span></div>
          <p className="ms-support-detail-copy">Delete this visitor identity, widget sessions, Support messages, and Support attachments. Personal Messenger is never affected.</p>
          <button className="ms-button ms-button--danger ms-button--compact" type="button" onClick={() => setDeleteVisitorOpen(true)}>Delete visitor data</button>
        </section>
      ) : null}

      <section className="ms-support-detail-section">
        <div className="ms-support-detail-section__heading"><strong>Team activity</strong><span>Audited actions and notes</span></div>
        {error ? <div className="ms-support-detail-error" role="alert">{error}</div> : null}
        <div className="ms-support-activity-list">
          {activityItems.map((item) => (
            <article className={`ms-support-activity-item${item.kind === "note" ? " is-note" : ""}`} key={item.id}>
              <strong>{item.title}</strong>
              {item.body ? <p>{item.body}</p> : null}
              <time>{formatActivityTime(item.createdAt)}</time>
            </article>
          ))}
          {!activityQuery.isLoading && !activityItems.length ? (
            <span className="ms-support-detail-empty">No team activity yet.</span>
          ) : null}
        </div>
      </section>
      <ConfirmDialog
        open={deleteVisitorOpen}
        title="Delete visitor data?"
        description="This permanently removes this website visitor and their Support conversation history. This action cannot be undone."
        confirmLabel="Delete visitor data"
        tone="danger"
        pending={deleteVisitorMutation.isPending}
        error={deleteVisitorMutation.isError ? parseApiError(deleteVisitorMutation.error, "Deletion could not be requested.").message : null}
        onClose={() => setDeleteVisitorOpen(false)}
        onConfirm={() => deleteVisitorMutation.mutate()}
      />
    </div>
  );
}
