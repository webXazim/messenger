import { useEffect, useRef } from "react";

type WakeLockSentinelLike = {
  released?: boolean;
  release: () => Promise<void>;
  addEventListener?: (type: "release", listener: () => void) => void;
};

type WakeLockNavigator = Navigator & {
  wakeLock?: {
    request: (type: "screen") => Promise<WakeLockSentinelLike>;
  };
};

export function useCallWakeLock(enabled: boolean) {
  const sentinelRef = useRef<WakeLockSentinelLike | null>(null);

  useEffect(() => {
    if (!enabled || typeof navigator === "undefined" || !(navigator as WakeLockNavigator).wakeLock) return;
    let cancelled = false;

    const release = async () => {
      const sentinel = sentinelRef.current;
      sentinelRef.current = null;
      if (sentinel && !sentinel.released) await sentinel.release().catch(() => undefined);
    };

    const request = async () => {
      if (cancelled || document.visibilityState !== "visible" || sentinelRef.current) return;
      try {
        const sentinel = await (navigator as WakeLockNavigator).wakeLock?.request("screen");
        if (!sentinel) return;
        if (cancelled) {
          await sentinel.release().catch(() => undefined);
          return;
        }
        sentinelRef.current = sentinel;
        sentinel.addEventListener?.("release", () => {
          if (sentinelRef.current === sentinel) sentinelRef.current = null;
        });
      } catch {
        // Wake Lock is an optional enhancement. Calls must continue without it.
      }
    };

    const handleVisibility = () => {
      if (document.visibilityState === "visible") void request();
      else void release();
    };

    void request();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", handleVisibility);
      void release();
    };
  }, [enabled]);
}
