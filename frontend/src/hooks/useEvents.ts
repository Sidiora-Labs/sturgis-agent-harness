import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { EventPage, PublicEvent } from "../lib/types";
type Replay = {
  identity: string;
  events: PublicEvent[];
  cursor: number;
  seen: Set<string>;
  caughtUp: boolean;
  connection: "live" | "reconnecting" | "idle";
  error: string | null;
};
function emptyReplay(identity: string): Replay {
  return {
    identity,
    events: [],
    cursor: 0,
    seen: new Set(),
    caughtUp: false,
    connection: "idle",
    error: null,
  };
}

/** Replay history before reporting readiness; pausing never discards loaded messages. */
export function useEvents(
  agent: string,
  conversation: string | null,
  { enabled = true }: { enabled?: boolean } = {},
) {
  const identity = JSON.stringify([agent, conversation]);
  const progress = useRef(emptyReplay(identity));
  const [snapshot, setSnapshot] = useState(progress.current);
  useEffect(() => {
    if (progress.current.identity !== identity)
      progress.current = emptyReplay(identity);
    const replay = progress.current;
    replay.connection = "idle";
    setSnapshot({ ...replay });
    if (!conversation || !enabled) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const page = await api<EventPage>(
          `/agents/${agent}/events?conversation_id=${conversation}&after=${replay.cursor}&wait=2`,
          undefined,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        replay.cursor = page.cursor;
        const fresh = page.items.filter((event) => {
          if (replay.seen.has(event.id)) return false;
          replay.seen.add(event.id);
          return true;
        });
        if (fresh.length) replay.events = [...replay.events, ...fresh];
        if (!page.has_more) replay.caughtUp = true;
        replay.connection = replay.caughtUp ? "live" : "idle";
        replay.error = null;
        setSnapshot({ ...replay });
        timer = setTimeout(poll, page.has_more ? 0 : 20);
      } catch (error) {
        if (!controller.signal.aborted) {
          replay.connection = "reconnecting";
          replay.error =
            error instanceof Error
              ? error.message
              : "Could not load conversation updates.";
          setSnapshot({ ...replay });
          timer = setTimeout(poll, 1200);
        }
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [agent, conversation, identity, enabled]);
  // Never render the preceding room's messages before the effect resets replay.
  const current =
    snapshot.identity === identity ? snapshot : emptyReplay(identity);
  return {
    events: current.events,
    connection: enabled ? current.connection : ("idle" as const),
    loading: !!conversation && !current.caughtUp,
    error: current.error,
  };
}
