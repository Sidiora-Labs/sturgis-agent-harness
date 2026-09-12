import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { EventPage, PublicEvent } from "../lib/types";
export function useEvents(agent: string, conversation: string | null) {
  const [events, setEvents] = useState<PublicEvent[]>([]);
  const [connection, setConnection] = useState<
    "live" | "reconnecting" | "idle"
  >("idle");
  useEffect(() => {
    setEvents([]);
    if (!conversation) {
      setConnection("idle");
      return;
    }
    const controller = new AbortController();
    let cursor = 0;
    const seen = new Set<string>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const page = await api<EventPage>(
          `/agents/${agent}/events?conversation_id=${conversation}&after=${cursor}&wait=2`,
          undefined,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        cursor = page.cursor;
        const fresh = page.items.filter((event) => {
          if (seen.has(event.id)) return false;
          seen.add(event.id);
          return true;
        });
        if (fresh.length) setEvents((previous) => [...previous, ...fresh]);
        setConnection("live");
        timer = setTimeout(poll, page.has_more ? 0 : 20);
      } catch {
        if (!controller.signal.aborted) {
          setConnection("reconnecting");
          timer = setTimeout(poll, 1200);
        }
      }
    }
    poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [agent, conversation]);
  return { events, connection };
}
