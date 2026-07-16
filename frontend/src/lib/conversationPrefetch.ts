import type { QueryClient } from "@tanstack/react-query";
import { chatApi, type MessagePage } from "../api/chat";

export function prefetchConversationResources(
  queryClient: QueryClient,
  conversationId: string,
  userId?: string | number,
) {
  if (!conversationId) return;

  void queryClient.prefetchQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => chatApi.getConversation(conversationId),
    staleTime: 60_000,
  });
  void queryClient.prefetchInfiniteQuery({
    queryKey: ["messages", conversationId],
    queryFn: ({ pageParam, signal }) => chatApi.listMessages(
      conversationId,
      typeof pageParam === "string" ? pageParam : null,
      signal,
    ),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage: MessagePage) => lastPage.next || undefined,
    staleTime: 5 * 60_000,
  });
  if (userId) {
    void queryClient.prefetchQuery({
      queryKey: ["conversation-e2ee", conversationId],
      queryFn: () => chatApi.getConversationE2EEKeys(conversationId),
      staleTime: 10_000,
    });
  }
}
