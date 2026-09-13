import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AnimatePresence, m } from "motion/react";
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  Check,
  ChevronDown,
  Compass,
  FileText,
  Lightbulb,
  MoreHorizontal,
  Paperclip,
  Settings2,
  Sparkles,
  Square,
  Target,
  X,
} from "lucide-react";
import {
  Button,
  Dropdown,
  Modal,
  Select,
  Skeleton,
  Textarea,
  Tooltip,
} from "../components/ui";
import {
  useAgent,
  useAction,
  useAgentQuery,
  useRuntimeState,
  canWakeRuntime,
} from "../hooks/useAgent";
import { useEvents } from "../hooks/useEvents";
import { api, command, fileBase64, requestId } from "../lib/api";
import type {
  Conversation,
  MessageReceipt,
  Overview,
  Page,
  Params,
  PublicEvent,
  Source,
} from "../lib/types";
import { FormDialog } from "./shared";
import "./conversation.css";
type Draft = {
  text: string;
  attachments: Source[];
  pending?: { requestId: string; params: Params };
};
type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  status?: string;
  steer?: boolean;
  historical?: boolean;
};
const suggestions = [
  {
    icon: Lightbulb,
    title: "Shape an idea",
    description: "Turn a possibility into a direction",
    prompt: "Help me shape an idea",
  },
  {
    icon: Target,
    title: "Make a plan",
    description: "Find a clear next step",
    prompt: "Help me plan my week",
  },
  {
    icon: BookOpen,
    title: "Connect the dots",
    description: "Make sense of your source material",
    prompt: "Help me make sense of my notes",
  },
  {
    icon: Compass,
    title: "Explore a question",
    description: "Follow your curiosity further",
    prompt: "Explore a question with me",
  },
];
function messagesFrom(events: PublicEvent[]): Message[] {
  const messages: Message[] = [];
  const streams = new Map<string, Message>();
  for (const event of events) {
    const p = event.payload;
    if (event.kind.startsWith("user.") && p.text)
      messages.push({
        id: event.id,
        role: "user",
        text: p.text,
        steer: event.kind === "user.steer",
        historical: event.origin === "portable.import",
      });
    if (event.kind === "output.persisted" && p.stream_id) {
      let message = streams.get(p.stream_id);
      if (!message) {
        message = {
          id: p.stream_id,
          role: "assistant",
          text: "",
          status: "Receiving output",
          historical: event.origin === "portable.import",
        };
        messages.push(message);
        streams.set(p.stream_id, message);
      }
      if (p.content?.type === "text") message.text += p.content.text || "";
      if (p.content?.type === "status")
        message.status =
          p.content.state +
          (p.content.error
            ? " · " + (p.content.error.message || p.content.error.code)
            : "");
    }
  }
  return messages;
}
export default function ConversationPage({
  selectedConversation,
  onSelect,
  onConnection,
  onConfigure,
}: {
  selectedConversation: string | null;
  onSelect: (id: string | null) => void;
  onConnection: (state: "live" | "reconnecting" | "idle") => void;
  onConfigure: () => void;
}) {
  const { agentId, accountId, notify } = useAgent();
  const client = useQueryClient();
  const runtime = useRuntimeState(accountId, agentId);
  const available = canWakeRuntime(runtime.data);
  const [resuming, setResuming] = useState(false);
  const rooms = useAgentQuery<Page<Conversation>>("conversations.list");
  const overview = useAgentQuery<Overview>("overview");
  const selected = selectedConversation,
    setSelected = onSelect;
  const [renaming, setRenaming] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const room =
    rooms.data?.items.find((r) => r.id === selected) || rooms.data?.items[0];
  const {
    events,
    connection,
    loading: historyLoading,
    error: historyError,
  } = useEvents(agentId, room?.id || null, { enabled: available });
  useEffect(() => onConnection(connection), [connection, onConnection]);
  const messages = useMemo(() => messagesFrom(events), [events]);
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [away, setAway] = useState(false);
  const composer = useRef<HTMLTextAreaElement>(null);
  const [prompt, setPrompt] = useState("");
  const refresh = () =>
    client.invalidateQueries({
      queryKey: ["agent", agentId, "conversations.list"],
    });
  useEffect(() => {
    follow.current = true;
    setAway(false);
  }, [room?.id]);
  useEffect(() => {
    if (follow.current)
      scroll.current?.scrollTo({
        top: scroll.current.scrollHeight,
        behavior: "instant",
      });
  }, [messages]);
  const active = overview.data?.provider_settings.find(
    (provider) => provider.active,
  );
  const choosePrompt = (text: string) => {
    setPrompt(text);
    composer.current?.focus();
  };
  return (
    <div className="conversation-layout">
      <section className="conversation-panel">
        <header className="thread-header">
          <div>
            <span className="thread-header-label">
              {room?.title || "A new beginning"}
            </span>
            <span className="thread-private">
              <span />
              Private to this agent
            </span>
          </div>
          <div className="row">
            <Button variant="ghost" size="sm" onClick={onConfigure}>
              <Settings2 size={14} />
              <span className="model-name">
                {active?.model || "Connect a model"}
              </span>
              <ChevronDown size={12} />
            </Button>
            {room && available && (
              <Dropdown
                trigger={
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Conversation options"
                  >
                    <MoreHorizontal size={18} />
                  </Button>
                }
                items={[
                  {
                    id: "rename",
                    label: "Rename conversation",
                    onSelect: () => setRenaming(true),
                  },
                  {
                    id: "archive",
                    label: "Archive conversation",
                    onSelect: () => setArchiving(true),
                    destructive: true,
                  },
                ]}
              />
            )}
          </div>
        </header>
        {!available && !runtime.isPending && (
          <div className="runtime-notice" role="status">
            <span>
              {runtime.error?.message ||
                (runtime.data?.private_queries_paused
                  ? "Conversation updates are paused. Your loaded messages and draft stay here."
                  : `Your agent is ${runtime.data?.observed || "unavailable"}. Your draft stays here.`)}
            </span>
            {runtime.data?.desired === "suspended" ? (
              <Button
                size="sm"
                loading={resuming}
                onClick={async () => {
                  setResuming(true);
                  try {
                    await api(`/agents/${agentId}/lifecycle/start`, {});
                    await runtime.refetch();
                    await client.invalidateQueries({
                      queryKey: ["agent", agentId],
                    });
                  } catch (error) {
                    notify((error as Error).message, "error");
                  } finally {
                    setResuming(false);
                  }
                }}
              >
                Resume agent
              </Button>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => runtime.refetch()}
              >
                Check status
              </Button>
            )}
          </div>
        )}
        {historyError && available && (
          <div className="runtime-notice" role="alert">
            <span>
              {historyError}{" "}
              {messages.length
                ? "Showing loaded messages while reconnecting."
                : "Reconnecting to load your saved messages."}
            </span>
          </div>
        )}
        <div
          className="message-scroll"
          role="region"
          tabIndex={0}
          ref={scroll}
          onScroll={() => {
            const element = scroll.current!;
            follow.current =
              element.scrollHeight - element.scrollTop - element.clientHeight <
              100;
            setAway(!follow.current);
          }}
          aria-label="Conversation history"
        >
          <div className="message-width">
            {messages.length ? (
              messages.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))
            ) : rooms.isPending || (room && historyLoading) ? (
              <div
                className="stack py-12"
                role="status"
                aria-label="Opening conversation"
              >
                <span className="muted text-sm">
                  {available
                    ? "Opening your conversation…"
                    : "Waiting to load conversation history…"}
                </span>
                <Skeleton className="h-5 w-3/4" />
                <Skeleton className="h-5 w-1/2" />
              </div>
            ) : (
              <m.div
                className="conversation-welcome"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25 }}
              >
                <div className="welcome-orbit">
                  <div className="orbit-line" />
                  <Sparkles size={27} />
                  <span className="orbit-dot" />
                </div>
                <div className="eyebrow">A little space to think</div>
                <h1>
                  Where should we
                  <br />
                  <span>take things next?</span>
                </h1>
                <p>
                  Bring an idea, a question, or something worth doing.
                  <br className="desktop-break" /> Your agent will keep the
                  thread.
                </p>
                <div className="suggestion-grid">
                  {suggestions.map((s) => (
                    <button
                      className="suggestion"
                      key={s.title}
                      onClick={() => choosePrompt(s.prompt)}
                    >
                      <s.icon size={18} />
                      <strong>{s.title}</strong>
                      <span>{s.description}</span>
                      <ArrowUp size={13} className="suggestion-arrow" />
                    </button>
                  ))}
                </div>
              </m.div>
            )}
          </div>
        </div>
        <AnimatePresence>
          {away && (
            <m.div
              className="jump-latest"
              initial={{ opacity: 0, y: 5 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
            >
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  follow.current = true;
                  setAway(false);
                  scroll.current?.scrollTo({
                    top: scroll.current.scrollHeight,
                    behavior: "smooth",
                  });
                }}
              >
                <ArrowDown size={13} />
                Jump to latest
              </Button>
            </m.div>
          )}
        </AnimatePresence>
        <Composer
          key={room?.id || "new"}
          agent={agentId}
          account={accountId}
          conversation={room?.id || null}
          onConversation={async () => {
            const created = await command<Conversation>(
              agentId,
              "conversations.create",
              { title: "A fresh start" },
            );
            return created.id;
          }}
          onSent={async (id) => {
            await refresh();
            setSelected(id);
          }}
          prompt={prompt}
          clearPrompt={() => setPrompt("")}
          inputRef={composer}
          notify={notify}
          ready={!!active}
          available={available}
          onConfigure={onConfigure}
          connection={connection}
        />
      </section>
      {room && (
        <FormDialog
          open={renaming}
          onOpenChange={setRenaming}
          title="Rename conversation"
          fields={[
            {
              name: "title",
              label: "Conversation title",
              value: room.title,
              required: true,
            },
          ]}
          onSubmit={async (data) => {
            try {
              await command(agentId, "conversations.update", {
                id: room.id,
                title: data.title,
                expected_revision: room.revision,
              });
              setRenaming(false);
              await refresh();
            } catch (error) {
              notify((error as Error).message, "error");
            }
          }}
        />
      )}
      <Modal
        open={archiving}
        onOpenChange={setArchiving}
        title="Archive this conversation?"
        description="It will leave your active list. Its history stays in your private agent records."
      >
        <div className="form-actions">
          <Button variant="ghost" onClick={() => setArchiving(false)}>
            Keep conversation
          </Button>
          <Button
            variant="danger"
            onClick={async () => {
              try {
                await command(agentId, "conversations.update", {
                  id: room!.id,
                  archived: true,
                  expected_revision: room!.revision,
                });
                setArchiving(false);
                setSelected(null);
                await refresh();
              } catch (error) {
                notify((error as Error).message, "error");
              }
            }}
          >
            Archive conversation
          </Button>
        </div>
      </Modal>
    </div>
  );
}
const MessageBubble = memo(function MessageBubble({
  message,
}: {
  message: Message;
}) {
  return (
    <article className={"message-bubble " + message.role}>
      <div className="message-avatar">
        {message.role === "assistant" ? <Sparkles size={14} /> : <span>Y</span>}
      </div>
      <div className="message-content">
        <div className="message-author">
          {message.role === "user" ? "You" : "Sturgis"}
          {message.steer && <span>Steering</span>}
          {message.historical && <span>Imported history</span>}
        </div>
        <div className="message-body">
          {message.role === "user" ? (
            message.text
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ children, href }) => (
                  <a href={href} target="_blank" rel="noopener noreferrer">
                    {children}
                  </a>
                ),
                img: ({ alt }) => (
                  <span className="muted">
                    [Image: {alt || "external image"}]
                  </span>
                ),
              }}
            >
              {message.text}
            </ReactMarkdown>
          )}
        </div>
        {message.status && (
          <div className="message-status">
            {message.status === "completed" && <Check size={11} />}
            <span>{message.status}</span>
          </div>
        )}
      </div>
    </article>
  );
});
function Composer({
  agent,
  account,
  conversation,
  onConversation,
  onSent,
  prompt,
  clearPrompt,
  inputRef,
  notify,
  ready,
  available,
  onConfigure,
  connection,
}: {
  agent: string;
  account: string;
  conversation: string | null;
  onConversation: () => Promise<string>;
  onSent: (id: string) => Promise<void>;
  prompt: string;
  clearPrompt: () => void;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
  notify: (message: string, tone?: "success" | "error") => void;
  ready: boolean;
  available: boolean;
  onConfigure: () => void;
  connection: string;
}) {
  const { execute } = useAction();
  const key = `sturgis:draft:${account}:${agent}:${conversation || "new"}`;
  const [draft, setDraft] = useState<Draft>(() => {
    try {
      return JSON.parse(
        localStorage.getItem(key) || '{"text":"","attachments":[]}',
      );
    } catch {
      return { text: "", attachments: [] };
    }
  });
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [mode, setMode] = useState("prompt");
  const fileInput = useRef<HTMLInputElement>(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const persist = useCallback(
    (value: Draft) => {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch {
        notify(
          "This browser could not save the local draft. Keep this tab open.",
          "error",
        );
      }
    },
    [key, notify],
  );
  useEffect(() => {
    const timer = setTimeout(() => persist(draft), 180);
    return () => clearTimeout(timer);
  }, [draft, persist]);
  useEffect(() => () => persist(draftRef.current), [persist]);
  useEffect(() => {
    if (prompt) {
      setDraft((previous) => ({ ...previous, text: prompt }));
      clearPrompt();
    }
  }, [prompt, clearPrompt]);
  useEffect(() => {
    const input = inputRef.current;
    if (input) {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 220) + "px";
    }
  }, [draft.text, inputRef]);
  async function send() {
    if (!draft.text.trim() || sending || uploading || !available) return;
    if (draft.pending && draft.pending.params.text !== draft.text) {
      notify(
        "Retry the saved message before editing it. Its acknowledgement is still unknown.",
        "error",
      );
      return;
    }
    setSending(true);
    try {
      const room =
        (draft.pending?.params.conversation_id as string | undefined) ||
        conversation ||
        (await onConversation());
      const pending = draft.pending || {
        requestId: requestId(),
        params: {
          conversation_id: room,
          message_id: requestId(),
          text: draft.text,
          attachments: draft.attachments.map((a) => a.id),
          kind: mode,
        },
      };
      const retained = { ...draft, pending };
      setDraft(retained);
      persist(retained);
      await command<MessageReceipt>(agent, "messages.send", pending.params, {
        requestId: pending.requestId,
      });
      const empty = { text: "", attachments: [] };
      setDraft(empty);
      persist(empty);
      await onSent(room);
      notify("Message accepted. Your agent is continuing the conversation.");
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setSending(false);
    }
  }
  async function attach(file?: File) {
    if (!file || uploading || !available) return;
    setUploading(true);
    try {
      const source = await execute<Source>("attachments.upload", {
        name: file.name,
        media_type: file.type || "application/octet-stream",
        data_base64: await fileBase64(file),
      });
      if (!source) return;
      setDraft((previous) => ({
        ...previous,
        attachments: previous.attachments.some((item) => item.id === source.id)
          ? previous.attachments
          : [...previous.attachments, source],
      }));
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }
  return (
    <div className="composer-dock">
      <div className="composer-width">
        {!ready && (
          <div className="provider-hint">
            <Sparkles size={13} />
            <span>Choose a model to bring your agent online.</span>
            <button onClick={onConfigure}>
              Set up model
              <ArrowUp size={12} />
            </button>
          </div>
        )}
        <div className={"composer-box " + (draft.pending ? "has-pending" : "")}>
          <Textarea
            ref={inputRef}
            aria-label="Message"
            placeholder="Ask anything, or pick up where you left off…"
            value={draft.text}
            onChange={(event) =>
              setDraft((previous) => ({
                ...previous,
                text: event.target.value,
              }))
            }
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                send();
              }
            }}
          />
          {draft.attachments.length > 0 && (
            <div className="attachment-chips">
              {draft.attachments.map((a) => (
                <span key={a.id}>
                  <FileText size={12} />
                  {a.name}
                  <button
                    aria-label={"Remove " + a.name}
                    onClick={() =>
                      setDraft((previous) => ({
                        ...previous,
                        attachments: previous.attachments.filter(
                          (item) => item.id !== a.id,
                        ),
                      }))
                    }
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="composer-controls">
            <div className="row">
              <Tooltip label="Attach a source">
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Attach file"
                  disabled={!available}
                  loading={uploading}
                  onClick={() => fileInput.current?.click()}
                >
                  <Paperclip size={17} />
                </Button>
              </Tooltip>
              <input
                type="file"
                className="sr-only"
                aria-label="Attachment file"
                ref={fileInput}
                disabled={!available}
                onChange={(event) => attach(event.target.files?.[0])}
              />
              <Select
                label="Message mode"
                value={mode}
                onValueChange={setMode}
                items={[
                  { value: "prompt", label: "New input" },
                  { value: "steer", label: "Steer active work" },
                ]}
              />
            </div>
            <div className="row">
              <Tooltip label="Stop active work">
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Stop"
                  disabled={!conversation || !available}
                  onClick={async () => {
                    try {
                      await command(agent, "messages.cancel", {
                        conversation_id: conversation,
                        message_id: requestId(),
                      });
                      notify(
                        "Cancellation requested. Recorded effects remain visible.",
                      );
                    } catch (error) {
                      notify((error as Error).message, "error");
                    }
                  }}
                >
                  <Square size={13} />
                </Button>
              </Tooltip>
              <Button
                size="icon"
                aria-label={draft.pending ? "Retry message" : "Send message"}
                loading={sending}
                disabled={!draft.text.trim() || uploading || !available}
                onClick={send}
              >
                <ArrowUp size={19} />
              </Button>
            </div>
          </div>
        </div>
        <div className="composer-footnote">
          <span>
            {connection === "reconnecting"
              ? "Reconnecting · your last cursor is saved"
              : draft.pending
                ? "Awaiting acknowledgement · retry preserves this message’s identity"
                : "A continuous conversation. A private workspace."}
          </span>
          <span>
            ↵ Send<span className="keyboard-separator">·</span>⇧↵ New line
          </span>
        </div>
      </div>
    </div>
  );
}
