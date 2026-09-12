import { useState, type ReactNode } from "react";
import { Dialog, DropdownMenu, ScrollArea } from "radix-ui";
import { m, useReducedMotion } from "motion/react";
import {
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsUpDown,
  Command,
  FolderOpen,
  Layers2,
  LogOut,
  Menu,
  Network,
  MessageCircle,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Plus,
  Settings2,
  X,
  type LucideIcon,
} from "lucide-react";
import { Button, Tooltip, cx } from "./ui";
import "./components.css";

export type ViewName =
  | "Conversation"
  | "Work"
  | "Knowledge"
  | "Files"
  | "Connections"
  | "Settings"
  | "Model Gateway";

export type ShellProps = {
  children: ReactNode;
  view: ViewName;
  onNavigate: (view: ViewName) => void;
  agents: Array<{ id: string; name: string }>;
  agentId: string | null;
  onAgentChange: (id: string) => void;
  onNewAgent: () => void;
  user: { email: string };
  onSignOut: () => void;
  mode: "self_hosted" | "hosted";
  connection: "live" | "reconnecting" | "idle";
  onCommandPalette?: () => void;
  conversations?: Array<{ id: string; title: string }>;
  conversationId?: string | null;
  onConversationSelect?: (id: string) => void;
  onNewConversation?: () => void;
};

const navigation: Array<{ view: ViewName; icon: LucideIcon }> = [
  { view: "Conversation", icon: MessageCircle },
  { view: "Work", icon: Layers2 },
  { view: "Knowledge", icon: BookOpen },
  { view: "Files", icon: FolderOpen },
  { view: "Connections", icon: Plug },
  { view: "Settings", icon: Settings2 },
];

export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      className={cx("st-brand-mark", className)}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M16 4v24M5.61 10l20.78 12M5.61 22l20.78-12"
        stroke="currentColor"
        strokeWidth="4.6"
        strokeLinecap="round"
      />
      <circle cx="16" cy="16" r="3.25" fill="currentColor" />
    </svg>
  );
}

function AgentMenu({
  agents,
  agentId,
  onAgentChange,
  onNewAgent,
  compact,
}: Pick<ShellProps, "agents" | "agentId" | "onAgentChange" | "onNewAgent"> & {
  compact: boolean;
}) {
  const selected = agents.find((agent) => agent.id === agentId);
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          className={cx(
            "st-agent-switch",
            compact && "st-agent-switch--compact",
          )}
          aria-label="Switch agent"
        >
          <span className="st-agent-avatar" aria-hidden="true">
            {selected?.name.slice(0, 1).toUpperCase() || <Plus size={17} />}
          </span>
          {!compact && (
            <>
              <span className="st-agent-switch__text">
                <span>{selected?.name || "Your workspace"}</span>
                <small>Personal agent</small>
              </span>
              <ChevronsUpDown size={14} aria-hidden="true" />
            </>
          )}
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side={compact ? "right" : "bottom"}
          align="start"
          sideOffset={8}
          collisionPadding={12}
          className="st-menu st-agent-menu"
        >
          <DropdownMenu.Label className="st-menu-label">
            Your agents
          </DropdownMenu.Label>
          {agents.map((agent) => (
            <DropdownMenu.Item
              key={agent.id}
              className="st-menu-item"
              onSelect={() => onAgentChange(agent.id)}
            >
              <span className="st-menu-item__text">{agent.name}</span>
              {agent.id === agentId && <Check size={15} aria-hidden="true" />}
            </DropdownMenu.Item>
          ))}
          <DropdownMenu.Separator className="st-menu-separator" />
          <DropdownMenu.Item className="st-menu-item" onSelect={onNewAgent}>
            <Plus size={15} aria-hidden="true" />
            Create an agent
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

function Sidebar({
  compact,
  mobile = false,
  closeMobile,
  ...props
}: ShellProps & {
  compact: boolean;
  mobile?: boolean;
  closeMobile?: () => void;
}) {
  const select = (view: ViewName) => {
    props.onNavigate(view);
    closeMobile?.();
  };
  return (
    <div
      className={cx("st-sidebar-inner", compact && "st-sidebar-inner--compact")}
    >
      <div className="st-sidebar-brand">
        <BrandMark />
        {!compact && (
          <span>
            sturgis<span className="st-brand-period">.</span>
          </span>
        )}
        {mobile && (
          <Dialog.Close asChild>
            <Button size="icon" variant="ghost" aria-label="Close navigation">
              <X size={18} />
            </Button>
          </Dialog.Close>
        )}
      </div>
      <div className="st-agent-slot">
        <AgentMenu
          agents={props.agents}
          agentId={props.agentId}
          compact={compact}
          onAgentChange={(id) => {
            props.onAgentChange(id);
            closeMobile?.();
          }}
          onNewAgent={() => {
            props.onNewAgent();
            closeMobile?.();
          }}
        />
      </div>
      {props.onNewConversation && (
        <div className="st-new-conversation">
          <Button
            variant="secondary"
            aria-label="New conversation"
            size={compact ? "icon" : "md"}
            onClick={() => {
              props.onNewConversation?.();
              closeMobile?.();
            }}
          >
            <Plus size={19} />
            {!compact && "New conversation"}
          </Button>
        </div>
      )}
      <ScrollArea.Root className="st-navigation-scroll">
        <ScrollArea.Viewport className="st-navigation-viewport">
          <nav className="st-navigation" aria-label="Installation navigation">
            {!compact && (
              <div className="st-navigation-label">Installation</div>
            )}
            <button
              className={cx(
                "st-nav-item",
                props.view === "Model Gateway" && "st-nav-item--active",
              )}
              aria-label="Model Gateway"
              aria-current={props.view === "Model Gateway" ? "page" : undefined}
              onClick={() => select("Model Gateway")}
            >
              <Network size={19} aria-hidden="true" />
              {!compact && <span>Model Gateway</span>}
            </button>
          </nav>
          <nav className="st-navigation" aria-label="Main navigation">
            {!compact && <div className="st-navigation-label">Workspace</div>}
            {navigation.map(({ view, icon: Icon }) => {
              const active = props.view === view;
              const control = (
                <button
                  className={cx("st-nav-item", active && "st-nav-item--active")}
                  onClick={() => select(view)}
                  aria-current={active ? "page" : undefined}
                  aria-label={compact ? view : undefined}
                >
                  <Icon
                    size={19}
                    strokeWidth={active ? 1.9 : 1.65}
                    aria-hidden="true"
                  />
                  {!compact && <span>{view}</span>}
                  {!compact && active && (
                    <span className="st-nav-indicator" aria-hidden="true" />
                  )}
                </button>
              );
              return compact ? (
                <Tooltip key={view} label={view}>
                  {control}
                </Tooltip>
              ) : (
                <div key={view}>{control}</div>
              );
            })}
          </nav>
          {!compact && props.onConversationSelect && (
            <nav className="st-recent" aria-label="Recent conversations">
              <div className="st-navigation-label">Recent</div>
              {(props.conversations || []).slice(0, 20).map((conversation) => (
                <button
                  key={conversation.id}
                  className={cx(
                    "st-recent-item",
                    conversation.id === props.conversationId &&
                      "st-recent-item--active",
                  )}
                  aria-current={
                    conversation.id === props.conversationId &&
                    props.view === "Conversation"
                      ? "page"
                      : undefined
                  }
                  onClick={() => {
                    props.onConversationSelect?.(conversation.id);
                    closeMobile?.();
                  }}
                >
                  <MessageCircle size={16} aria-hidden="true" />
                  <span>{conversation.title}</span>
                </button>
              ))}
              {!props.conversations?.length && (
                <p className="st-recent-empty">
                  Your conversations will appear here.
                </p>
              )}
            </nav>
          )}
        </ScrollArea.Viewport>
        <ScrollArea.Scrollbar orientation="vertical" className="st-scrollbar">
          <ScrollArea.Thumb className="st-scroll-thumb" />
        </ScrollArea.Scrollbar>
      </ScrollArea.Root>
      <div className="st-sidebar-footer">
        {!compact && (
          <div className="st-runtime-note">
            <span className="st-runtime-symbol" aria-hidden="true">
              <Layers2 size={15} />
            </span>
            <div>
              <strong>
                {props.mode === "hosted"
                  ? "Private hosted runtime"
                  : "Your own installation"}
              </strong>
              <span>
                {props.mode === "hosted"
                  ? "A space of your own"
                  : "Self-hosted · stays with you"}
              </span>
            </div>
          </div>
        )}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button
              className={cx("st-account", compact && "st-account--compact")}
              aria-label="Account menu"
            >
              <span className="st-account-avatar" aria-hidden="true">
                {props.user.email.slice(0, 1).toUpperCase()}
              </span>
              {!compact && (
                <>
                  <span className="st-account-text">
                    <strong>Your account</strong>
                    <span>{props.user.email}</span>
                  </span>
                  <ChevronDown size={14} aria-hidden="true" />
                </>
              )}
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content
              side="top"
              align="start"
              sideOffset={8}
              collisionPadding={12}
              className="st-menu st-account-menu"
            >
              <DropdownMenu.Label className="st-menu-label st-menu-email">
                {props.user.email}
              </DropdownMenu.Label>
              <DropdownMenu.Item
                className="st-menu-item"
                onSelect={() => select("Settings")}
              >
                <Settings2 size={15} aria-hidden="true" />
                Settings
                <ArrowUpRight size={14} aria-hidden="true" />
              </DropdownMenu.Item>
              <DropdownMenu.Separator className="st-menu-separator" />
              <DropdownMenu.Item
                className="st-menu-item st-menu-item--danger"
                onSelect={props.onSignOut}
              >
                <LogOut size={15} aria-hidden="true" />
                Sign out
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>
    </div>
  );
}

export function Shell(props: ShellProps) {
  const [compact, setCompact] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const reducedMotion = useReducedMotion();
  const selected = props.agents.find((agent) => agent.id === props.agentId);
  return (
    <div className="st-shell">
      <a href="#st-main" className="st-skip-link">
        Skip to content
      </a>
      <m.aside
        className="st-desktop-sidebar"
        initial={false}
        animate={{ width: compact ? 72 : 236 }}
        transition={{
          duration: reducedMotion ? 0 : 0.2,
          ease: [0.22, 1, 0.36, 1],
        }}
      >
        <Sidebar {...props} compact={compact} />
      </m.aside>
      <div className="st-shell-content">
        <header className="st-topbar">
          <div className="st-topbar-leading">
            <Tooltip label={compact ? "Expand sidebar" : "Collapse sidebar"}>
              <Button
                className="st-desktop-toggle"
                variant="ghost"
                size="icon"
                aria-label={compact ? "Expand sidebar" : "Collapse sidebar"}
                onClick={() => setCompact((value) => !value)}
              >
                {compact ? (
                  <PanelLeftOpen size={18} />
                ) : (
                  <PanelLeftClose size={18} />
                )}
              </Button>
            </Tooltip>
            <Dialog.Root open={mobileOpen} onOpenChange={setMobileOpen}>
              <Dialog.Trigger asChild>
                <Button
                  className="st-mobile-toggle"
                  variant="ghost"
                  size="icon"
                  aria-label="Open navigation"
                >
                  <Menu size={20} />
                </Button>
              </Dialog.Trigger>
              <Dialog.Portal>
                <Dialog.Overlay className="st-overlay st-mobile-overlay" />
                <Dialog.Content
                  className="st-mobile-sheet"
                  aria-describedby={undefined}
                >
                  <Dialog.Title className="st-visually-hidden">
                    Workspace navigation
                  </Dialog.Title>
                  <Sidebar
                    {...props}
                    mobile
                    compact={false}
                    closeMobile={() => setMobileOpen(false)}
                  />
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
            <nav className="st-breadcrumbs" aria-label="Breadcrumb">
              <span className="st-breadcrumb-agent">
                {selected?.name || "Your workspace"}
              </span>
              <ChevronRight size={13} aria-hidden="true" />
              <span aria-current="page">{props.view}</span>
            </nav>
          </div>
          <div className="st-topbar-trailing">
            <span
              className={cx(
                "st-connection",
                `st-connection--${props.connection}`,
              )}
              role="status"
              aria-label={`Connection: ${props.connection}`}
            >
              <span className="st-connection-dot" aria-hidden="true" />
              <span>
                {props.connection === "live"
                  ? "Connected"
                  : props.connection === "reconnecting"
                    ? "Reconnecting"
                    : "Ready"}
              </span>
            </span>
            {props.onCommandPalette && (
              <Tooltip label="Open command palette">
                <Button
                  className="st-command-key"
                  variant="ghost"
                  size="sm"
                  onClick={props.onCommandPalette}
                  aria-label="Open command palette"
                >
                  <Command size={13} aria-hidden="true" />
                  <span>K</span>
                </Button>
              </Tooltip>
            )}
          </div>
        </header>
        <main id="st-main" className="st-main" tabIndex={-1}>
          {props.children}
        </main>
      </div>
    </div>
  );
}
