import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { authApi } from "../api/auth";
import { chatApi } from "../api/chat";
import { useAuth } from "../contexts/AuthContext";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { parseApiError } from "../lib/apiErrors";
import { MessengerPageHeader } from "../components/pages/MessengerPageHeader";
import { UserAvatar } from "../components/UserAvatar";
import { personPresenceText, personPresenceToneClass } from "../lib/personPresentation";
import { conversationPath } from "../lib/conversationRoute";
import type { CurrentUser, FriendRequest, UserSearchResult } from "../types/auth";
import type { Conversation } from "../types/chat";

type ContactTab = "friends" | "requests" | "find" | "nearby";
type NearbyCoordinates = { latitude: number; longitude: number };

function displayName(person: UserSearchResult) {
  return person.display_name
    || person.full_name
    || [person.first_name, person.last_name].filter(Boolean).join(" ")
    || person.username;
}

function sameId(a: string | number | undefined | null, b: string | number | undefined | null) {
  return String(a ?? "") === String(b ?? "");
}

function sameHandle(a: string | undefined | null, b: string | undefined | null) {
  return Boolean(a && b && a.trim().toLowerCase() === b.trim().toLowerCase());
}

function isCurrentUserResult(person: UserSearchResult | undefined | null, currentUser: CurrentUser | null | undefined) {
  if (!person || !currentUser) return false;
  return Boolean(person.is_current_user || sameId(person.id, currentUser.id) || sameHandle(person.username, currentUser.username));
}

function findDirectConversation(conversations: Conversation[], userId: string) {
  return conversations.find(
    (conversation) =>
      conversation.type === "direct"
      && conversation.participants.some((participant) => sameId(participant.user.id, userId)),
  );
}

function requestStatusLabel(status: FriendRequest["status"]) {
  if (status === "accepted") return "Friends";
  if (status === "rejected") return "Declined";
  if (status === "cancelled") return "Cancelled";
  return "Pending";
}

export function FriendsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user, refreshMe } = useAuth();
  const [activeTab, setActiveTab] = useState<ContactTab>("friends");
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query.trim(), 300);
  const [noteTargetId, setNoteTargetId] = useState<string | null>(null);
  const [requestNotes, setRequestNotes] = useState<Record<string, string>>({});
  const [radiusKm, setRadiusKm] = useState(25);
  const [isLocating, setIsLocating] = useState(false);
  const [nearbyCoordinates, setNearbyCoordinates] = useState<NearbyCoordinates | null>(null);
  const nearbyResultsQuery = useQuery<UserSearchResult[]>({
    queryKey: ["nearby-users"],
    queryFn: async () => [],
    enabled: false,
    initialData: [],
  });
  const nearbyResults = nearbyResultsQuery.data ?? [];
  const [shareNearby, setShareNearby] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [optimisticOutgoingIds, setOptimisticOutgoingIds] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    setShareNearby(Boolean(user?.profile?.nearby_discovery_enabled));
  }, [user?.profile?.nearby_discovery_enabled]);

  const conversationsQuery = useQuery({
    queryKey: ["conversations"],
    queryFn: ({ signal }) => chatApi.listConversations(signal),
  });

  const searchQuery = useQuery({
    queryKey: ["user-search", debouncedQuery],
    queryFn: ({ signal }) => authApi.searchUsers(debouncedQuery, signal),
    enabled: debouncedQuery.length >= 2,
    placeholderData: (previous) => previous,
    staleTime: 15_000,
  });

  const requestsQuery = useQuery({
    queryKey: ["friend-requests", "all"],
    queryFn: ({ signal }) => authApi.listFriendRequests("all", signal),
  });

  const sendRequestMutation = useMutation({
    mutationFn: ({ userId, message }: { userId: string; message?: string }) => authApi.createFriendRequest(userId, message),
    onMutate: ({ userId }) => {
      setActionError(null);
      setActionMessage(null);
      setOptimisticOutgoingIds((current) => new Set(current).add(String(userId)));
    },
    onSuccess: (request, variables) => {
      const userId = String(variables.userId);
      setRequestNotes((current) => {
        const next = { ...current };
        delete next[userId];
        return next;
      });
      setNoteTargetId((current) => current === userId ? null : current);
      setActionMessage(
        isCurrentUserResult(request.from_user, user)
          ? "Friend request sent."
          : "This person already sent you a request. Review it in Requests.",
      );
      queryClient.setQueryData<FriendRequest[]>(["friend-requests", "all"], (current = []) => {
        const withoutExisting = current.filter((item) => item.id !== request.id);
        return [request, ...withoutExisting];
      });
    },
    onError: (error, variables) => {
      setOptimisticOutgoingIds((current) => {
        const next = new Set(current);
        next.delete(String(variables.userId));
        return next;
      });
      setActionError(parseApiError(error, "Unable to send the friend request.").message);
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["friend-requests"] }),
        queryClient.invalidateQueries({ queryKey: ["user-search"] }),
      ]);
    },
  });

  const respondMutation = useMutation({
    mutationFn: ({ requestId, action }: { requestId: string; action: "accept" | "reject" | "cancel" }) =>
      authApi.respondToFriendRequest(requestId, action),
    onMutate: async ({ requestId, action }) => {
      setActionError(null);
      setActionMessage(null);
      await queryClient.cancelQueries({ queryKey: ["friend-requests", "all"] });
      const previous = queryClient.getQueryData<FriendRequest[]>(["friend-requests", "all"]);
      const nextStatus: FriendRequest["status"] = action === "accept" ? "accepted" : action === "reject" ? "rejected" : "cancelled";
      queryClient.setQueryData<FriendRequest[]>(["friend-requests", "all"], (current = []) =>
        current.map((item) => item.id === requestId ? { ...item, status: nextStatus } : item),
      );
      return { previous };
    },
    onSuccess: (request, variables) => {
      const label = variables.action === "accept"
        ? "Friend request accepted."
        : variables.action === "reject"
          ? "Friend request declined."
          : "Friend request cancelled.";
      setActionMessage(label);
      setOptimisticOutgoingIds(new Set());
      queryClient.setQueryData<FriendRequest[]>(["friend-requests", "all"], (current = []) =>
        current.map((item) => item.id === request.id ? request : item),
      );
    },
    onError: (error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(["friend-requests", "all"], context.previous);
      setActionError(parseApiError(error, "Unable to update the friend request.").message);
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["friend-requests"] }),
        queryClient.invalidateQueries({ queryKey: ["user-search"] }),
      ]);
      await refreshMe().catch(() => undefined);
    },
  });

  const startChatMutation = useMutation({
    mutationFn: async (person: UserSearchResult) => {
      const existing = findDirectConversation(conversationsQuery.data ?? [], person.id);
      if (existing) return existing;
      return chatApi.createDirectConversation(person.id);
    },
    onMutate: () => {
      setActionError(null);
      setActionMessage(null);
    },
    onSuccess: (conversation) => {
      queryClient.setQueryData<Conversation[]>(["conversations"], (current = []) => {
        const next = current.filter((item) => item.id !== conversation.id);
        return [conversation, ...next];
      });
      navigate(conversationPath(conversation, user));
    },
    onError: (error) => setActionError(parseApiError(error, "Unable to start the conversation.").message),
  });

  const nearbyMutation = useMutation({
    mutationFn: ({ coordinates, share }: { coordinates: NearbyCoordinates; share: boolean }) =>
      authApi.nearbyUsers(coordinates.latitude, coordinates.longitude, radiusKm, 50, share),
    onMutate: () => {
      setActionError(null);
      setActionMessage(null);
    },
    onSuccess: async (results, variables) => {
      queryClient.setQueryData(["nearby-users"], results);
      if (variables.share) {
        setActionMessage("Nearby visibility is on. Your precise coordinates are not shown to other users.");
        await refreshMe().catch(() => undefined);
      }
    },
    onError: (error) => setActionError(parseApiError(error, "Unable to find nearby people.").message),
  });

  const stopNearbyMutation = useMutation({
    mutationFn: () => authApi.updateMe({
      profile: {
        nearby_discovery_enabled: false,
        latitude: null,
        longitude: null,
      },
    }),
    onMutate: () => {
      setActionError(null);
      setActionMessage(null);
    },
    onSuccess: async () => {
      setShareNearby(false);
      setNearbyCoordinates(null);
      queryClient.setQueryData(["nearby-users"], []);
      setActionMessage("Nearby visibility is off and the saved location was removed.");
      await refreshMe().catch(() => undefined);
    },
    onError: (error) => setActionError(parseApiError(error, "Unable to turn off nearby visibility.").message),
  });

  const incomingRequests = useMemo(
    () => (requestsQuery.data ?? []).filter((item) => item.status === "pending" && isCurrentUserResult(item.to_user, user)),
    [requestsQuery.data, user],
  );
  const outgoingRequests = useMemo(
    () => (requestsQuery.data ?? []).filter((item) => item.status === "pending" && isCurrentUserResult(item.from_user, user)),
    [requestsQuery.data, user],
  );
  const friends = useMemo(
    () => (requestsQuery.data ?? []).filter((item) => item.status === "accepted"),
    [requestsQuery.data],
  );
  const recentResolvedRequests = useMemo(
    () => (requestsQuery.data ?? []).filter((item) => item.status === "rejected" || item.status === "cancelled").slice(0, 8),
    [requestsQuery.data],
  );

  const friendIds = useMemo(() => {
    const ids = new Set<string>();
    friends.forEach((item) => {
      const other = isCurrentUserResult(item.from_user, user) ? item.to_user : item.from_user;
      ids.add(String(other.id));
    });
    return ids;
  }, [friends, user]);

  const isSearchSettling = query.trim() !== debouncedQuery || searchQuery.isFetching;

  const captureLocation = () => {
    if (shareNearby && user?.profile?.is_discoverable === false) {
      setActionError("Turn on account discovery in Settings before allowing nearby people to find you.");
      return;
    }
    if (!("geolocation" in navigator)) {
      setActionError("Location is not available in this browser.");
      return;
    }
    setActionError(null);
    setActionMessage(null);
    setIsLocating(true);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const coordinates = { latitude: position.coords.latitude, longitude: position.coords.longitude };
        setNearbyCoordinates(coordinates);
        setIsLocating(false);
        nearbyMutation.mutate({ coordinates, share: shareNearby });
      },
      (error) => {
        setIsLocating(false);
        setActionError(error.message || "Unable to access your location.");
      },
      { enableHighAccuracy: false, maximumAge: 120_000, timeout: 10_000 },
    );
  };

  const repeatNearbySearch = () => {
    if (!nearbyCoordinates) {
      captureLocation();
      return;
    }
    nearbyMutation.mutate({ coordinates: nearbyCoordinates, share: shareNearby });
  };

  const pendingStatusForUser = (person: UserSearchResult) => {
    const incoming = incomingRequests.find((item) => sameId(item.from_user.id, person.id));
    if (incoming) return { type: "incoming" as const, request: incoming };
    const outgoing = outgoingRequests.find((item) => sameId(item.to_user.id, person.id));
    if (outgoing) return { type: "outgoing" as const, request: outgoing };
    if (optimisticOutgoingIds.has(String(person.id))) return { type: "outgoing" as const, request: null };
    if (person.request_status === "incoming_request") return { type: "incoming" as const, request: null };
    if (person.request_status === "outgoing_request" || person.request_status === "pending") return { type: "outgoing" as const, request: null };
    return null;
  };

  const UserCard = ({ person, context }: { person: UserSearchResult; context: "search" | "nearby" | "friend" }) => {
    const personId = String(person.id);
    const isCurrentUser = isCurrentUserResult(person, user);
    const isFriend = person.is_friend || person.request_status === "friends" || friendIds.has(personId);
    const pending = pendingStatusForUser(person);
    const requestId = pending?.request?.id;
    const responsePending = Boolean(requestId && respondMutation.isPending && respondMutation.variables?.requestId === requestId);
    const sendingPending = sendRequestMutation.isPending && sameId(sendRequestMutation.variables?.userId, person.id);
    const chatPending = startChatMutation.isPending && sameId(startChatMutation.variables?.id, person.id);
    const noteOpen = noteTargetId === personId;
    const note = requestNotes[personId] ?? "";

    return (
      <article className="ms-contact-card" key={`${context}-${person.id}`}>
        <div className="ms-contact-card__top">
          <UserAvatar person={person} size="lg" showPresence className="ms-contact-avatar" decorative />
          <div className="ms-contact-card__main">
            <div className="ms-contact-card__title">
              <strong>{displayName(person)}</strong>
              {person.distance_km != null ? <span className="ms-page-badge">{person.distance_km.toFixed(1)} km away</span> : null}
            </div>
            <div className="muted">@{person.username}</div>
            <div className={`ms-contact-presence ${personPresenceToneClass(person)}`}>{personPresenceText(person)}</div>
            {person.bio ? <p className="ms-contact-card__bio">{person.bio}</p> : null}
            {person.status_message ? <div className="muted">{person.status_message}</div> : null}
          </div>
        </div>

        {!isCurrentUser && !isFriend && !pending && noteOpen ? (
          <label className="ms-contact-note">
            <span>Optional note for {displayName(person)}</span>
            <textarea
              value={note}
              maxLength={255}
              rows={2}
              onChange={(event) => setRequestNotes((current) => ({ ...current, [personId]: event.target.value }))}
              placeholder="Add a short personal note"
            />
            <small>{note.length}/255</small>
          </label>
        ) : null}

        <div className="ms-contact-actions">
          {isCurrentUser ? <span className="ms-page-badge">This is you</span> : null}
          {!isCurrentUser && isFriend ? (
            <>
              <span className="ms-page-badge ms-page-badge--strong">Friend</span>
              <button
                type="button"
                className="ms-button ms-button--primary ms-button--compact"
                disabled={chatPending}
                onClick={() => startChatMutation.mutate(person)}
              >
                {chatPending ? "Opening…" : "Message"}
              </button>
            </>
          ) : null}
          {!isCurrentUser && !isFriend && pending?.type === "incoming" ? (
            <>
              <span className="ms-page-badge ms-page-badge--danger">Request received</span>
              {pending.request ? (
                <>
                  <button
                    type="button"
                    className="ms-button ms-button--primary ms-button--compact"
                    disabled={responsePending}
                    onClick={() => respondMutation.mutate({ requestId: pending.request!.id, action: "accept" })}
                  >
                    {responsePending && respondMutation.variables?.action === "accept" ? "Accepting…" : "Accept"}
                  </button>
                  <button
                    type="button"
                    className="ms-button ms-button--compact"
                    disabled={responsePending}
                    onClick={() => respondMutation.mutate({ requestId: pending.request!.id, action: "reject" })}
                  >
                    Decline
                  </button>
                </>
              ) : (
                <button type="button" className="ms-button ms-button--compact" onClick={() => setActiveTab("requests")}>Review request</button>
              )}
            </>
          ) : null}
          {!isCurrentUser && !isFriend && pending?.type === "outgoing" ? (
            <>
              <span className="ms-page-badge">{sendingPending ? "Sending…" : "Request sent"}</span>
              {pending.request ? (
                <button
                  type="button"
                  className="ms-button ms-button--compact"
                  disabled={responsePending}
                  onClick={() => respondMutation.mutate({ requestId: pending.request!.id, action: "cancel" })}
                >
                  {responsePending ? "Cancelling…" : "Cancel"}
                </button>
              ) : null}
            </>
          ) : null}
          {!isCurrentUser && !isFriend && !pending ? (
            <>
              <button
                type="button"
                className="ms-button ms-button--compact"
                disabled={sendingPending}
                onClick={() => setNoteTargetId((current) => current === personId ? null : personId)}
              >
                {noteOpen ? "Hide note" : "Add note"}
              </button>
              <button
                type="button"
                className="ms-button ms-button--primary ms-button--compact"
                disabled={sendingPending}
                onClick={() => sendRequestMutation.mutate({ userId: person.id, message: note.trim() || undefined })}
              >
                {sendingPending ? "Sending…" : "Add friend"}
              </button>
            </>
          ) : null}
        </div>
      </article>
    );
  };

  const RequestCard = ({ item, incoming }: { item: FriendRequest; incoming: boolean }) => {
    const other = incoming ? item.from_user : item.to_user;
    const isPending = respondMutation.isPending && respondMutation.variables?.requestId === item.id;
    return (
      <article className="ms-contact-request" key={item.id}>
        <div className="ms-contact-request__identity">
          <UserAvatar person={other} size="lg" showPresence className="ms-contact-avatar" decorative />
          <div>
            <strong>{displayName(other)}</strong>
            <div className="muted">@{other.username}</div>
            <div className={`ms-contact-presence ${personPresenceToneClass(other)}`}>{personPresenceText(other)}</div>
            {item.message ? <p className="ms-contact-request__message">“{item.message}”</p> : null}
          </div>
        </div>
        <div className="ms-contact-actions">
          <span className="ms-page-badge">{incoming ? "Received" : "Sent"}</span>
          {incoming ? (
            <>
              <button
                type="button"
                className="ms-button ms-button--primary ms-button--compact"
                disabled={isPending}
                onClick={() => respondMutation.mutate({ requestId: item.id, action: "accept" })}
              >
                {isPending && respondMutation.variables?.action === "accept" ? "Accepting…" : "Accept"}
              </button>
              <button
                type="button"
                className="ms-button ms-button--compact"
                disabled={isPending}
                onClick={() => respondMutation.mutate({ requestId: item.id, action: "reject" })}
              >
                Decline
              </button>
            </>
          ) : (
            <button
              type="button"
              className="ms-button ms-button--compact"
              disabled={isPending}
              onClick={() => respondMutation.mutate({ requestId: item.id, action: "cancel" })}
            >
              {isPending ? "Cancelling…" : "Cancel request"}
            </button>
          )}
        </div>
      </article>
    );
  };

  const tabs: Array<{ id: ContactTab; label: string; count?: number }> = [
    { id: "friends", label: "Friends", count: friends.length },
    { id: "requests", label: "Requests", count: incomingRequests.length + outgoingRequests.length },
    { id: "find", label: "Find people" },
    { id: "nearby", label: "Nearby" },
  ];

  return (
    <div className="ms-workspace-page ms-contacts-page">
      <MessengerPageHeader
        eyebrow="Contacts"
        title="Contacts"
        description="Manage people you know, requests waiting for you, and private discovery controls."
        stats={[
          { label: "friends", value: friends.length },
          { label: "pending requests", value: incomingRequests.length + outgoingRequests.length },
        ]}
      />

      <nav className="ms-page-surface ms-contact-tabs" role="tablist" aria-label="Contact sections">
        {tabs.map((tab, index) => (
          <button
            key={tab.id}
            id={`contacts-tab-${tab.id}`}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`contacts-panel-${tab.id}`}
            tabIndex={activeTab === tab.id ? 0 : -1}
            className={activeTab === tab.id ? "is-active" : ""}
            onClick={() => setActiveTab(tab.id)}
            onKeyDown={(event) => {
              let nextIndex = index;
              if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
              else if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
              else if (event.key === "Home") nextIndex = 0;
              else if (event.key === "End") nextIndex = tabs.length - 1;
              else return;
              event.preventDefault();
              const nextTab = tabs[nextIndex];
              setActiveTab(nextTab.id);
              window.requestAnimationFrame(() => document.getElementById(`contacts-tab-${nextTab.id}`)?.focus());
            }}
          >
            <span>{tab.label}</span>
            {tab.count ? <b>{tab.count}</b> : null}
          </button>
        ))}
      </nav>

      {actionError ? <div className="ms-page-error" role="alert">{actionError}</div> : null}
      {actionMessage ? <div className="ms-page-success" role="status">{actionMessage}</div> : null}

      {activeTab === "friends" ? (
        <section id="contacts-panel-friends" className="ms-page-surface ms-page-surface--padded ms-contacts-card" role="tabpanel" aria-labelledby="contacts-tab-friends" tabIndex={0}>
          <div className="ms-section-header">
            <div>
              <div className="ms-section-header__eyebrow">People you know</div>
              <h3 id="friends-heading">Friends</h3>
            </div>
          </div>
          {requestsQuery.isLoading ? <div className="ms-page-empty">Loading friends…</div> : null}
          {requestsQuery.isError ? (
            <div className="ms-page-error">
              Friends could not be loaded. <button type="button" onClick={() => void requestsQuery.refetch()}>Retry</button>
            </div>
          ) : null}
          <div className="ms-contacts-list ms-contacts-list--cards">
            {friends.map((item) => {
              const other = isCurrentUserResult(item.from_user, user) ? item.to_user : item.from_user;
              return <UserCard key={`friend-${item.id}`} person={other} context="friend" />;
            })}
            {!requestsQuery.isLoading && !friends.length ? <div className="ms-page-empty">No friends yet. Use Find people to connect with someone.</div> : null}
          </div>
        </section>
      ) : null}

      {activeTab === "requests" ? (
        <div id="contacts-panel-requests" className="ms-contact-request-columns" role="tabpanel" aria-labelledby="contacts-tab-requests" tabIndex={0}>
          <section className="ms-page-surface ms-page-surface--padded ms-contacts-card" aria-labelledby="incoming-heading">
            <div className="ms-section-header">
              <div><div className="ms-section-header__eyebrow">Waiting for you</div><h3 id="incoming-heading">Incoming</h3></div>
            </div>
            <div className="ms-contacts-list ms-contacts-list--compact">
              {incomingRequests.map((item) => <RequestCard key={item.id} item={item} incoming />)}
              {!requestsQuery.isLoading && !incomingRequests.length ? <div className="ms-page-empty">No incoming requests.</div> : null}
            </div>
          </section>

          <section className="ms-page-surface ms-page-surface--padded ms-contacts-card" aria-labelledby="outgoing-heading">
            <div className="ms-section-header">
              <div><div className="ms-section-header__eyebrow">Sent by you</div><h3 id="outgoing-heading">Outgoing</h3></div>
            </div>
            <div className="ms-contacts-list ms-contacts-list--compact">
              {outgoingRequests.map((item) => <RequestCard key={item.id} item={item} incoming={false} />)}
              {!requestsQuery.isLoading && !outgoingRequests.length ? <div className="ms-page-empty">No outgoing requests.</div> : null}
            </div>
          </section>

          {recentResolvedRequests.length ? (
            <section className="ms-page-surface ms-page-surface--padded ms-contacts-card ms-contact-request-columns__full" aria-labelledby="recent-requests-heading">
              <div className="ms-section-header">
                <div><div className="ms-section-header__eyebrow">Recent</div><h3 id="recent-requests-heading">Completed requests</h3></div>
              </div>
              <div className="ms-contact-resolution-list">
                {recentResolvedRequests.map((item) => {
                  const other = isCurrentUserResult(item.from_user, user) ? item.to_user : item.from_user;
                  return (
                    <div key={item.id}>
                      <span>{displayName(other)}</span>
                      <b>{requestStatusLabel(item.status)}</b>
                    </div>
                  );
                })}
              </div>
            </section>
          ) : null}
        </div>
      ) : null}

      {activeTab === "find" ? (
        <section id="contacts-panel-find" className="ms-page-surface ms-page-surface--padded ms-contacts-card" role="tabpanel" aria-labelledby="contacts-tab-find" tabIndex={0}>
          <div className="ms-section-header">
            <div>
              <div className="ms-section-header__eyebrow">Discovery</div>
              <h3 id="find-people-heading">Find people</h3>
            </div>
            <div className="ms-section-header__description">Search by name or username. Notes are private to each request.</div>
          </div>
          <label className="ms-field-stack">
            <span>Search</span>
            <input
              className="ms-page-field"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Name or username"
              autoComplete="off"
            />
          </label>
          {isSearchSettling ? <div className="muted" role="status">Searching…</div> : null}
          {searchQuery.isError ? (
            <div className="ms-page-error">Search is unavailable. <button type="button" onClick={() => void searchQuery.refetch()}>Retry</button></div>
          ) : null}
          <div className="ms-contacts-list ms-contacts-list--cards">
            {(searchQuery.data ?? []).map((person) => <UserCard key={`search-${person.id}`} person={person} context="search" />)}
            {query.trim().length < 2 ? <div className="ms-page-empty">Enter at least two characters to search.</div> : null}
            {query.trim().length >= 2 && query.trim() === debouncedQuery && !searchQuery.isFetching && !(searchQuery.data ?? []).length ? <div className="ms-page-empty">No matching people found.</div> : null}
          </div>
        </section>
      ) : null}

      {activeTab === "nearby" ? (
        <section id="contacts-panel-nearby" className="ms-page-surface ms-page-surface--padded ms-contacts-card" role="tabpanel" aria-labelledby="contacts-tab-nearby" tabIndex={0}>
          <div className="ms-section-header">
            <div>
              <div className="ms-section-header__eyebrow">Private by default</div>
              <h3 id="nearby-heading">Nearby people</h3>
            </div>
          </div>

          <div className="ms-nearby-privacy">
            <div>
              <strong>Use this device’s location</strong>
              <p>Your browser asks for permission only after you press Find nearby. Your exact coordinates are never shown in user cards.</p>
            </div>
            <label className="ms-nearby-share-toggle">
              <input
                type="checkbox"
                checked={shareNearby}
                disabled={stopNearbyMutation.isPending}
                onChange={(event) => {
                  const enabled = event.target.checked;
                  if (!enabled && user?.profile?.nearby_discovery_enabled) {
                    stopNearbyMutation.mutate();
                    return;
                  }
                  if (enabled && user?.profile?.is_discoverable === false) {
                    setActionError("Turn on account discovery in Settings before enabling nearby visibility.");
                    return;
                  }
                  setShareNearby(enabled);
                }}
              />
              <span>
                <strong>Let nearby people find me</strong>
                <small>{user?.profile?.nearby_discovery_enabled ? "Currently on" : "Currently off"}</small>
              </span>
            </label>
          </div>

          <div className="ms-nearby-controls">
            <label className="ms-field-stack">
              <span>Distance</span>
              <select value={radiusKm} onChange={(event) => setRadiusKm(Number(event.target.value))}>
                <option value={5}>Within 5 km</option>
                <option value={10}>Within 10 km</option>
                <option value={25}>Within 25 km</option>
                <option value={50}>Within 50 km</option>
                <option value={100}>Within 100 km</option>
              </select>
            </label>
            <button
              type="button"
              className="ms-button ms-button--primary"
              disabled={isLocating || nearbyMutation.isPending}
              onClick={repeatNearbySearch}
            >
              {isLocating ? "Requesting location…" : nearbyMutation.isPending ? "Searching…" : nearbyCoordinates ? "Search again" : "Find nearby"}
            </button>
            {user?.profile?.nearby_discovery_enabled ? (
              <button
                type="button"
                className="ms-button ms-button--ghost"
                disabled={stopNearbyMutation.isPending}
                onClick={() => stopNearbyMutation.mutate()}
              >
                {stopNearbyMutation.isPending ? "Turning off…" : "Stop sharing location"}
              </button>
            ) : null}
          </div>

          <div className="ms-contacts-list ms-contacts-list--cards">
            {nearbyResults.map((person) => <UserCard key={`nearby-${person.id}`} person={person} context="nearby" />)}
            {!nearbyMutation.isPending && nearbyCoordinates && !nearbyResults.length ? <div className="ms-page-empty">No nearby people are visible within this distance.</div> : null}
            {!nearbyCoordinates ? <div className="ms-page-empty">No location has been requested on this page.</div> : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}
