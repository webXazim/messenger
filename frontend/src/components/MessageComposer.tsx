import { useCallback, useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { safeId } from "../lib/safeId";
import { readConversationDraft, removeConversationDraft, writeConversationDraft } from "../lib/conversationDrafts";
import type { Message } from "../types/chat";
import { ComposerContext } from "./composer/ComposerContext";
import { UploadQueue } from "./composer/UploadQueue";
import type { ComposerUploadRequestOptions, ComposerUploadResult, PendingComposerUpload } from "./composer/types";
import { uploadPolicyFromCapabilities, validateComposerUpload, type ComposerUploadPolicy } from "./composer/uploadPolicy";
import { VoiceNoteRecorder } from "./VoiceNoteRecorder";
import type { VoiceNotePayload } from "./VoiceNoteRecorder";

const COMPOSER_MAX_HEIGHT = 160;

function extractEntities(text: string) {
  const entities: Array<Record<string, unknown>> = [];
  const linkRegex = /https?:\/\/\S+/g;
  const mentionRegex = /@(\w+)/g;
  for (const match of text.matchAll(linkRegex)) {
    entities.push({ type: "link", offset: match.index ?? 0, length: match[0].length, url: match[0] });
  }
  for (const match of text.matchAll(mentionRegex)) {
    entities.push({ type: "mention", offset: match.index ?? 0, length: match[0].length, username: match[1] });
  }
  return entities;
}

function getMessagePreviewLabel(message: Message | null) {
  if (!message) return "";
  if (message.text?.trim()) return message.text.trim();
  if (message.voice_note?.is_voice_note) return "Voice note";
  if (message.attachments?.length) {
    if (message.attachments.length === 1) return message.attachments[0]?.original_name || "Attachment";
    return `${message.attachments.length} attachments`;
  }
  if (message.call_event) return message.call_event.summary_text || "Call update";
  return "Attachment or voice note";
}

function AttachIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M8 12.5 14.8 5.7a3.5 3.5 0 1 1 5 5L11 19.5a5 5 0 1 1-7-7L13 3.5" />
    </svg>
  );
}

function SendIcon({ editing }: { editing: boolean }) {
  if (editing) {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6" /></svg>;
  }
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 11.5 19.5 4l-4.2 16-3.6-5-4.6-3.5L4 11.5Z" /></svg>;
}

export function MessageComposer({
  onUpload,
  onDiscardUpload,
  onSend,
  replyTo,
  onClearReply,
  editingMessage,
  onCancelEdit,
  draftKey,
  legacyDraftKey,
  uploadPolicy = uploadPolicyFromCapabilities(),
  onTyping,
  onSendVoiceNote,
  disabledReason,
}: {
  onUpload: (file: File, options: ComposerUploadRequestOptions) => Promise<ComposerUploadResult>;
  onDiscardUpload?: (uploadId: string) => void;
  onSend: (payload: Record<string, unknown>) => Promise<void>;
  onSendVoiceNote: (payload: VoiceNotePayload) => Promise<void>;
  replyTo: Message | null;
  onClearReply: () => void;
  editingMessage: Message | null;
  onCancelEdit: () => void;
  draftKey?: string;
  legacyDraftKey?: string;
  uploadPolicy?: ComposerUploadPolicy;
  onTyping?: () => void;
  disabledReason?: string | null;
}) {
  const [text, setText] = useState("");
  const [pendingUploads, setPendingUploads] = useState<PendingComposerUpload[]>([]);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [voiceActive, setVoiceActive] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const pendingUploadsRef = useRef<PendingComposerUpload[]>([]);
  const uploadControllersRef = useRef<Record<string, AbortController>>({});
  const activeUploadIdsRef = useRef(new Set<string>());
  const pendingClientTempIdRef = useRef<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const keyboardHelpId = useId();
  const submitErrorId = useId();

  useEffect(() => {
    pendingUploadsRef.current = pendingUploads;
  }, [pendingUploads]);

  const resizeTextarea = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, COMPOSER_MAX_HEIGHT)}px`;
  };

  const focusTextarea = () => {
    window.requestAnimationFrame(() => textareaRef.current?.focus({ preventScroll: true }));
  };

  useEffect(() => {
    setSubmitError(null);
    pendingClientTempIdRef.current = null;
    if (!editingMessage && draftKey) {
      setText(readConversationDraft(draftKey, legacyDraftKey));
    }
  }, [draftKey, editingMessage?.id, legacyDraftKey]);

  useEffect(() => {
    if (editingMessage) {
      setText(editingMessage.text || "");
      setPendingUploads((current) => {
        current.forEach((item) => {
          uploadControllersRef.current[item.localId]?.abort();
          if (item.uploadId) onDiscardUpload?.(item.uploadId);
          if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
        });
        return [];
      });
      onClearReply();
      pendingClientTempIdRef.current = null;
    }
  }, [editingMessage?.id]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(resizeTextarea);
    return () => window.cancelAnimationFrame(frame);
  }, [text]);

  useEffect(() => {
    if (!draftKey || editingMessage) return;
    const timer = window.setTimeout(() => writeConversationDraft(draftKey, text), 250);
    return () => window.clearTimeout(timer);
  }, [draftKey, editingMessage?.id, text]);

  const uploadedAttachmentIds = useMemo(
    () => pendingUploads.filter((item) => item.status === "uploaded" && item.uploadId).map((item) => item.uploadId as string),
    [pendingUploads],
  );
  const hasPendingUpload = pendingUploads.some((item) => item.status === "queued" || item.status === "uploading");
  const hasFailedUpload = pendingUploads.some((item) => item.status === "failed");
  const hasBlockingUpload = hasPendingUpload || hasFailedUpload;
  const composerDisabled = Boolean(disabledReason);
  const canSend = useMemo(
    () => !composerDisabled && !isSubmitting && !hasBlockingUpload && (text.trim().length > 0 || uploadedAttachmentIds.length > 0),
    [composerDisabled, hasBlockingUpload, isSubmitting, text, uploadedAttachmentIds],
  );

  const revokeUploadPreview = (upload?: PendingComposerUpload) => {
    if (upload?.previewUrl) URL.revokeObjectURL(upload.previewUrl);
    if (upload?.thumbnailUrl && upload.thumbnailUrl !== upload.previewUrl) URL.revokeObjectURL(upload.thumbnailUrl);
  };

  const buildPreviewUrl = (file: File) => {
    const mime = file.type.toLowerCase();
    if (mime.startsWith("image/") || mime.startsWith("video/") || mime.startsWith("audio/") || mime === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) {
      return URL.createObjectURL(file);
    }
    return undefined;
  };

  const performUpload = useCallback(async (localId: string) => {
    if (activeUploadIdsRef.current.has(localId)) return;
    const target = pendingUploadsRef.current.find((item) => item.localId === localId);
    if (!target || target.status !== "queued") return;

    activeUploadIdsRef.current.add(localId);
    const controller = new AbortController();
    uploadControllersRef.current[localId] = controller;
    setPendingUploads((current) => current.map((item) => item.localId === localId
      ? { ...item, status: "uploading", progress: 0, error: undefined }
      : item));

    try {
      const upload = await onUpload(target.file, {
        signal: controller.signal,
        onProgress: (progress) => {
          setPendingUploads((current) => current.map((item) => item.localId === localId
            ? { ...item, progress: Math.max(0, Math.min(100, Math.round(progress))) }
            : item));
        },
      });
      if (controller.signal.aborted) return;
      const thumbnailUrl = upload.thumbnailBlob?.size ? URL.createObjectURL(upload.thumbnailBlob) : target.thumbnailUrl;
      setPendingUploads((current) => current.map((item) => item.localId === localId
        ? {
            ...item,
            status: "uploaded",
            uploadId: upload.uploadId,
            mediaKind: upload.mediaKind,
            width: upload.width,
            height: upload.height,
            rotation: upload.rotation,
            durationSeconds: upload.durationSeconds,
            thumbnailUrl,
            progress: 100,
          }
        : item));
    } catch (error) {
      if (controller.signal.aborted) return;
      setPendingUploads((current) => current.map((item) => item.localId === localId
        ? { ...item, status: "failed", progress: undefined, error: error instanceof Error ? error.message : "Upload failed" }
        : item));
    } finally {
      activeUploadIdsRef.current.delete(localId);
      delete uploadControllersRef.current[localId];
    }
  }, [onUpload]);

  useEffect(() => {
    const activeCount = pendingUploads.filter((item) => item.status === "uploading").length;
    const availableSlots = Math.max(0, uploadPolicy.maxParallelUploads - activeCount);
    if (!availableSlots) return;
    pendingUploads
      .filter((item) => item.status === "queued")
      .slice(0, availableSlots)
      .forEach((item) => void performUpload(item.localId));
  }, [pendingUploads, performUpload, uploadPolicy.maxParallelUploads]);

  const uploadFiles = (files: FileList | File[]) => {
    const nextUploads: PendingComposerUpload[] = [];
    const errors: string[] = [];
    Array.from(files).forEach((file) => {
      const validation = validateComposerUpload(file, uploadPolicy);
      if (!validation.valid) {
        errors.push(validation.message || `${file.name} cannot be uploaded.`);
        return;
      }
      nextUploads.push({
        localId: safeId("upload"),
        file,
        fileName: file.name,
        previewUrl: buildPreviewUrl(file),
        status: "queued",
      });
    });
    if (nextUploads.length) setPendingUploads((current) => [...current, ...nextUploads]);
    setSubmitError(errors.length ? errors.join(" ") : null);
  };

  const retryUpload = (localId: string) => {
    setSubmitError(null);
    setPendingUploads((current) => current.map((item) => item.localId === localId
      ? { ...item, status: "queued", progress: undefined, error: undefined }
      : item));
  };

  const removeUpload = (localId: string) => setPendingUploads((current) => {
    const target = current.find((item) => item.localId === localId);
    uploadControllersRef.current[localId]?.abort();
    delete uploadControllersRef.current[localId];
    activeUploadIdsRef.current.delete(localId);
    if (target?.uploadId) onDiscardUpload?.(target.uploadId);
    revokeUploadPreview(target);
    return current.filter((item) => item.localId !== localId);
  });

  const toggleViewOnce = (localId: string) => {
    setPendingUploads((current) => current.map((item) => item.localId === localId ? { ...item, viewOnce: !item.viewOnce } : item));
  };

  useEffect(() => () => {
    Object.values(uploadControllersRef.current).forEach((controller) => controller.abort());
    pendingUploadsRef.current.forEach(revokeUploadPreview);
  }, []);

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files?.length) uploadFiles(event.target.files);
    event.target.value = "";
  };

  const handleDrop = (event: DragEvent<HTMLFormElement>) => {
    event.preventDefault();
    setDraggingFiles(false);
    if (composerDisabled || editingMessage || !event.dataTransfer.files.length) return;
    uploadFiles(event.dataTransfer.files);
  };

  return (
    <form
      role="form"
      aria-label="Message composer"
      className={`ms-message-composer ${draggingFiles ? "is-dragging" : ""} ${composerDisabled ? "is-disabled" : ""}`}
      onDragEnter={(event) => { event.preventDefault(); if (!composerDisabled && !editingMessage) setDraggingFiles(true); }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDraggingFiles(false); }}
      onDrop={handleDrop}
      onSubmit={async (event) => {
        event.preventDefault();
        if (composerDisabled || !canSend || hasBlockingUpload || isSubmitting) return;
        const clientTempId = pendingClientTempIdRef.current || safeId("message");
        pendingClientTempIdRef.current = clientTempId;
        const submittedText = text;
        const submittedUploads = pendingUploads.filter((item) => item.status === "uploaded" && item.uploadId);
        const optimisticAttachments = submittedUploads.map((item) => ({
          id: String(item.uploadId),
          original_name: item.fileName,
          mime_type: item.file.type || (item.fileName.toLowerCase().endsWith(".pdf") ? "application/pdf" : "application/octet-stream"),
          media_kind: item.mediaKind || (item.file.type.startsWith("video/") ? "video" : item.file.type.startsWith("image/") ? "image" : item.file.type.startsWith("audio/") ? "audio" : "file"),
          size: item.file.size,
          width: item.width,
          height: item.height,
          rotation: item.rotation,
          duration_seconds: item.durationSeconds,
          file_url: item.previewUrl,
          preview_url: item.previewUrl,
          thumbnail_url: item.thumbnailUrl || (item.file.type.startsWith("image/") ? item.previewUrl : null),
          view_once: Boolean(item.viewOnce),
          view_once_opened: false,
          can_open_view_once: false,
        }));
        pendingUploadsRef.current = [];
        setPendingUploads([]);
        setText("");
        focusTextarea();
        try {
          setSubmitError(null);
          setIsSubmitting(true);
          await onSend({
            type: "text",
            text: submittedText,
            attachment_ids: uploadedAttachmentIds,
            view_once_attachment_ids: submittedUploads.filter((item) => item.viewOnce && item.uploadId).map((item) => item.uploadId as string),
            _optimistic_attachments: optimisticAttachments,
            client_temp_id: clientTempId,
            reply_to_id: editingMessage ? null : (replyTo?.id ?? null),
            entities: extractEntities(submittedText),
          });
          pendingClientTempIdRef.current = null;
          submittedUploads.forEach(revokeUploadPreview);
          removeConversationDraft(draftKey);
          onClearReply();
          onCancelEdit();
        } catch (error) {
          pendingUploadsRef.current = submittedUploads;
          setPendingUploads(submittedUploads);
          setText(submittedText);
          setSubmitError(error instanceof Error ? error.message : "Could not send this message. Your draft and attachments are still here.");
        } finally {
          setIsSubmitting(false);
          focusTextarea();
        }
      }}
    >
      {editingMessage ? (
        <ComposerContext
          mode="edit"
          title="Editing message"
          preview={editingMessage.text || "Update this message"}
          onDismiss={() => {
            onCancelEdit();
            setText("");
            removeConversationDraft(draftKey);
            pendingClientTempIdRef.current = null;
          }}
        />
      ) : replyTo ? (
        <ComposerContext
          mode="reply"
          title="Replying to"
          meta={replyTo.sender.display_name || replyTo.sender.username}
          preview={getMessagePreviewLabel(replyTo)}
          onDismiss={onClearReply}
        />
      ) : null}

      <UploadQueue uploads={pendingUploads} onRetry={retryUpload} onRemove={removeUpload} onToggleViewOnce={toggleViewOnce} />

      {disabledReason ? (
        <div className="ms-message-composer__disabled-note" role="status" aria-live="polite">
          {disabledReason}
        </div>
      ) : null}
      {submitError ? (
        <div id={submitErrorId} className="ms-message-composer__error" role="alert">
          {submitError}
        </div>
      ) : null}

      <div className={`ms-message-composer__surface ${voiceActive ? "is-voice-active" : ""} ${text.length ? "has-text" : ""} ${text.trim() || pendingUploads.length || editingMessage ? "has-draft" : ""}`}>
        {!voiceActive ? <>
        <label className={`ms-composer-icon-button ms-message-composer__attach ${editingMessage || composerDisabled ? "is-disabled" : ""}`} aria-label="Attach files" title={disabledReason || "Attach files"}>
          <input type="file" multiple onChange={handleFileChange} disabled={composerDisabled || Boolean(editingMessage)} />
          <AttachIcon />
        </label>

        <span id={keyboardHelpId} className="ms-visually-hidden">Press Enter to send. Press Shift and Enter for a new line.</span>
        <div className="ms-message-composer__input-shell">
          <textarea
            ref={textareaRef}
            className="ms-message-composer__input"
            rows={1}
            value={text}
            onChange={(event) => {
              if (isSubmitting) return;
              setText(event.target.value);
              setSubmitError(null);
              onTyping?.();
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder={disabledReason ? "Secure messaging unavailable" : editingMessage ? "Edit message" : "Write a message…"}
            aria-label={editingMessage ? "Edit message" : "Write a message"}
            aria-describedby={`${keyboardHelpId}${submitError ? ` ${submitErrorId}` : ""}`}
            aria-keyshortcuts="Enter"
            aria-busy={isSubmitting}
            disabled={composerDisabled}
          />
        </div>
        </> : null}

        <VoiceNoteRecorder
          onSendVoiceNote={onSendVoiceNote}
          variant="inline"
          disabled={composerDisabled || Boolean(editingMessage) || Boolean(text.trim()) || pendingUploads.length > 0}
          onActiveChange={setVoiceActive}
        />

        {!voiceActive ? (
        <button
          className="ms-message-composer__send"
          type="submit"
          onPointerDown={(event) => event.preventDefault()}
          disabled={!canSend || hasBlockingUpload || isSubmitting}
          aria-label={isSubmitting
            ? "Sending message"
            : editingMessage
              ? "Save message"
              : hasFailedUpload
                ? "Remove or retry failed attachments before sending"
                : hasPendingUpload
                  ? "Wait for attachments to finish uploading"
                  : "Send message"}
          title={disabledReason || (isSubmitting
            ? "Sending…"
            : editingMessage
              ? "Save message"
              : hasFailedUpload
                ? "Remove or retry failed attachments"
                : hasPendingUpload
                  ? "Attachments are still uploading"
                  : "Send message")}
        >
          <SendIcon editing={Boolean(editingMessage)} />
        </button>
        ) : null}

        {draggingFiles ? <div className="ms-message-composer__drop-zone">Drop files to attach</div> : null}
      </div>
    </form>
  );
}
