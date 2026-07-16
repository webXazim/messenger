import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { InfiniteData, QueryClient } from "@tanstack/react-query";
import { chatApi, type MessagePage } from "../api/chat";
import { mergeMessageContextPages } from "../lib/messageTimeline";
import type { Message } from "../types/chat";

export type ConversationTimelineNotice = {
  tone: "neutral" | "danger" | "success";
  message: string;
};

type TimelineScrollAnchor = {
  messageId: string;
  top: number;
};

type UseConversationTimelineOptions = {
  conversationId: string;
  messages: Message[];
  queryClient: QueryClient;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  pageCount: number;
  fetchNextPage: () => Promise<{ isError: boolean }>;
  getErrorMessage: (error: unknown, fallback: string) => string;
};

export function useConversationTimeline({
  conversationId,
  messages,
  queryClient,
  hasNextPage,
  isFetchingNextPage,
  pageCount,
  fetchNextPage,
  getErrorMessage,
}: UseConversationTimelineOptions) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const messageRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const initialScrollDoneRef = useRef(false);
  const stickToBottomRef = useRef(true);
  const restoreScrollAfterOlderLoadRef = useRef<TimelineScrollAnchor | null>(null);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [timelineAtLatest, setTimelineAtLatest] = useState(true);
  const [replyJumpMessageId, setReplyJumpMessageId] = useState<string | null>(null);
  const [timelineNotice, setTimelineNotice] = useState<ConversationTimelineNotice | null>(null);
  const [highlightedMessageId, setHighlightedMessageId] = useState<string | null>(null);

  useEffect(() => {
    initialScrollDoneRef.current = false;
    stickToBottomRef.current = true;
    restoreScrollAfterOlderLoadRef.current = null;
    messageRefs.current = {};
    setShowJumpToLatest(false);
    setTimelineAtLatest(true);
    setReplyJumpMessageId(null);
    setTimelineNotice(null);
    setHighlightedMessageId(null);
  }, [conversationId]);

  useLayoutEffect(() => {
    const node = scrollerRef.current;
    if (!node) return;
    const restore = restoreScrollAfterOlderLoadRef.current;
    if (restore) {
      const anchorNode = messageRefs.current[restore.messageId];
      if (anchorNode) {
        const nextTop = anchorNode.getBoundingClientRect().top;
        node.scrollTop += nextTop - restore.top;
      }
      restoreScrollAfterOlderLoadRef.current = null;
      return;
    }

    if (!initialScrollDoneRef.current && messages.length) {
      node.scrollTop = node.scrollHeight;
      initialScrollDoneRef.current = true;
      stickToBottomRef.current = true;
      setTimelineAtLatest(true);
      setShowJumpToLatest(false);
      const frame = window.requestAnimationFrame(() => {
        if (scrollerRef.current) scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
      });
      return () => window.cancelAnimationFrame(frame);
    }

    if (stickToBottomRef.current) {
      node.scrollTop = node.scrollHeight;
      setTimelineAtLatest(true);
      setShowJumpToLatest(false);
    }
  }, [conversationId, messages.length, pageCount]);

  useEffect(() => {
    const node = scrollerRef.current;
    if (!node) return;
    const handleScroll = () => {
      const nearBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 180;
      stickToBottomRef.current = nearBottom;
      setTimelineAtLatest(nearBottom);
      setShowJumpToLatest(!nearBottom);
      if (
        node.scrollTop < 96
        && hasNextPage
        && !isFetchingNextPage
        && !restoreScrollAfterOlderLoadRef.current
      ) {
        const containerTop = node.getBoundingClientRect().top;
        const anchorMessage = messages.find((message) => {
          const messageNode = messageRefs.current[message.id];
          return Boolean(messageNode && messageNode.getBoundingClientRect().bottom >= containerTop);
        });
        const anchorNode = anchorMessage ? messageRefs.current[anchorMessage.id] : null;
        if (anchorMessage && anchorNode) {
          restoreScrollAfterOlderLoadRef.current = {
            messageId: anchorMessage.id,
            top: anchorNode.getBoundingClientRect().top,
          };
        }
        void fetchNextPage().then((result) => {
          if (result.isError) restoreScrollAfterOlderLoadRef.current = null;
        });
      }
    };
    node.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();
    return () => node.removeEventListener("scroll", handleScroll);
  }, [fetchNextPage, hasNextPage, isFetchingNextPage, messages]);

  useEffect(() => {
    const node = scrollerRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      if (!stickToBottomRef.current) return;
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        node.scrollTop = node.scrollHeight;
      });
    });
    observer.observe(node);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [conversationId]);

  const revealMessage = useCallback((messageId: string) => {
    const node = messageRefs.current[messageId];
    if (!node) return false;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlightedMessageId(messageId);
    window.setTimeout(() => {
      setHighlightedMessageId((current) => (current === messageId ? null : current));
    }, 1800);
    return true;
  }, []);

  const jumpToMessage = useCallback(async (messageId: string) => {
    if (!messageId || replyJumpMessageId) return;
    setTimelineNotice(null);
    if (revealMessage(messageId)) return;

    setReplyJumpMessageId(messageId);
    try {
      const context = await chatApi.getMessageContext(messageId);
      queryClient.setQueryData<InfiniteData<MessagePage>>(
        ["messages", conversationId],
        (current) => mergeMessageContextPages(current, context.results),
      );
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve())));
      if (!revealMessage(messageId)) {
        throw new Error("The original message is not available in this conversation.");
      }
    } catch (error) {
      setTimelineNotice({ tone: "danger", message: getErrorMessage(error, "The original message could not be loaded.") });
    } finally {
      setReplyJumpMessageId(null);
    }
  }, [conversationId, getErrorMessage, queryClient, replyJumpMessageId, revealMessage]);

  const scrollToLatest = useCallback(() => {
    const node = scrollerRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
    stickToBottomRef.current = true;
    setTimelineAtLatest(true);
    setShowJumpToLatest(false);
  }, []);

  const registerMessageRef = useCallback((messageId: string, node: HTMLDivElement | null) => {
    if (node) messageRefs.current[messageId] = node;
    else delete messageRefs.current[messageId];
  }, []);

  return {
    scrollerRef,
    showJumpToLatest,
    timelineAtLatest,
    replyJumpMessageId,
    timelineNotice,
    highlightedMessageId,
    setTimelineNotice,
    jumpToMessage,
    scrollToLatest,
    registerMessageRef,
  };
}
