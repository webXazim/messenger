import { useEffect, useRef } from "react";

type MessageIdentity = {
  id: string;
  client_temp_id?: string | null;
};

export function stableMessageRenderKey(message: MessageIdentity) {
  return message.client_temp_id
    ? `client:${message.client_temp_id}`
    : `message:${message.id}`;
}

export function useMessageEntranceKeys(
  scope: string,
  messages: MessageIdentity[],
  ready: boolean,
) {
  const stateRef = useRef<{
    scope: string;
    hydrated: boolean;
    known: Set<string>;
    entering: Set<string>;
  }>({
    scope,
    hydrated: false,
    known: new Set(),
    entering: new Set(),
  });

  if (stateRef.current.scope !== scope) {
    stateRef.current = {
      scope,
      hydrated: false,
      known: new Set(),
      entering: new Set(),
    };
  }

  const state = stateRef.current;
  if (ready && state.hydrated) {
    let lastKnownIndex = -1;
    for (let index = 0; index < messages.length; index += 1) {
      if (state.known.has(stableMessageRenderKey(messages[index]))) {
        lastKnownIndex = index;
      }
    }
    for (let index = 0; index < messages.length; index += 1) {
      const message = messages[index];
      const key = stableMessageRenderKey(message);
      if (!state.known.has(key) && index > lastKnownIndex) {
        state.entering.add(key);
      }
    }
  }

  useEffect(() => {
    const current = stateRef.current;
    if (!ready || current.scope !== scope) return;
    for (const message of messages) {
      current.known.add(stableMessageRenderKey(message));
    }
    current.hydrated = true;
  }, [messages, ready, scope]);

  return state.entering;
}
