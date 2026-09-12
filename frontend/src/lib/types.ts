/** Consumer/1 domain projections. Authority fields are always assigned by the server. */
export type Json =
  null | boolean | number | string | Json[] | { [key: string]: Json };
export type Params = Record<string, unknown>;
export interface Account {
  id: string;
  email: string;
  admin: boolean;
  session_id: string;
}
export interface Agent {
  id: string;
  name: string;
  revision: number;
  archived: boolean;
}
export interface RuntimeState {
  agent_id: string;
  desired: string;
  observed: string;
  revision?: number;
  next_wake_at?: number | null;
  error?: { code: string; message?: string } | null;
  /** Browser admission guard during a control action; never an observed state. */
  private_queries_paused?: boolean;
}
export interface Health {
  status: string;
  version: string;
  mode: "self_hosted" | "hosted";
}
export interface Conversation {
  id: string;
  title: string;
  revision: number;
  archived: boolean;
  binding_id: string;
}
export interface Page<T> {
  items: T[];
  truncated?: boolean;
}
export interface Source {
  id: string;
  name: string;
  media_type: string;
  bytes: number;
  sha256: string;
  artifact_id: string;
  extraction: { status: string; reason?: string };
  revision: number;
}
export interface Knowledge {
  id: string;
  title: string;
  revision: number;
  attachment_id: string;
  text?: string;
  memory_ids: string[];
}
export interface FileEntry {
  name: string;
  directory: boolean;
  file: boolean;
  bytes: number;
}
export interface FileBody {
  path: string;
  data_base64: string;
  sha256: string;
  bytes: number;
}
export interface Provider {
  id: string;
  revision: number;
  model: string;
  endpoint: string;
  context_limit: number;
  output_limit: number;
  tools: boolean;
  credential_ref: string | null;
  ready: boolean;
  active: boolean;
  probe?: unknown;
}
export interface Credential {
  name: string;
  revision: number;
  reference: string;
  active: boolean;
}
export interface ToolPolicy {
  id: string;
  revision: number;
  mode: "ask" | "allow" | "deny";
  stale: boolean;
  tool: { read_only: boolean; required_capabilities: string[] };
}
export interface Approval {
  id: string;
  event_id?: string;
  state?: string;
  call?: { tool: string; arguments: Params };
}
export interface Connection {
  id: string;
  kind: string;
  endpoint: string;
  revision: number;
  status: string;
  active: boolean;
  grants: string[];
  options: Params;
  credential_ref?: string | null;
}
export interface Goal {
  id: string;
  revision: number;
  objective: string;
  state: string;
}
export interface Commitment {
  id: string;
  revision: number;
  description: string;
  state: string;
  progress: string;
}
export interface Schedule {
  id: string;
  revision: number;
  state: string;
  next_due: number | null;
  payload: { text?: string };
}
export interface Workflow {
  id: string;
  revision: number;
  state: string;
  steps: { id: string; description: string; state: string }[];
  execution?: unknown;
}
export interface Work {
  id: string;
  kind: string;
  state: string;
  objective?: string;
}
export interface Proposal {
  id: string;
  decision: string;
  candidate: { action: { text?: string }; id: string };
}
export interface WorkData {
  goals: Goal[];
  commitments: Commitment[];
  schedules: Schedule[];
  workflows: Workflow[];
  work: Work[];
  children: Work[];
  inbox: Proposal[];
}
export interface Skill {
  id: string;
  revision: number;
  state: string;
  manifest: {
    id: string;
    version: string;
    description: string;
    triggers: string[];
    steps: string[];
    required_tools: string[];
  };
}
export interface Plugin {
  id: string;
  revision: number;
  status: string;
}
export interface MemoryRecord {
  id: string;
  revision: number;
  content: string;
  kind: string;
  concepts: string[];
  sources: unknown[];
}
export interface MemoryResult {
  status: string;
  error?: { message: string };
  data?: { records: { record: MemoryRecord; score: number }[] };
}
export interface Usage {
  actual: Record<string, number>;
  receipt_count: number;
  unresolved_resources: Record<string, number>;
  unresolved_claims: unknown[];
  overruns: string[];
}
export interface Overview {
  agent_id: string;
  mode: string;
  provider_settings: Provider[];
  usage: Usage;
  memory: { available: boolean };
  accepting: boolean;
  tools: unknown[];
}
export interface PublicEvent {
  origin?: string;
  id: string;
  cursor: number;
  kind: string;
  payload: {
    text?: string;
    stream_id?: string;
    terminal?: string;
    content?: {
      type: string;
      text?: string;
      state?: string;
      error?: { code: string; message?: string };
    };
  };
}
export interface EventPage {
  items: PublicEvent[];
  cursor: number;
  has_more: boolean;
}
export interface MessageReceipt {
  id: string;
  event_id: string;
  work_id: string | null;
  status: string;
}

/** Standalone model gateway contracts. These contain no provider credentials. */
export interface GatewayModel {
  id: string;
  name: string;
  context_limit: number;
  output_limit: number;
  tools: boolean;
  connection_id?: string;
  provider_name?: string;
}
export interface GatewayPreset {
  id: string;
  name: string;
  endpoint: string;
  protocol: "openai" | "anthropic" | "gemini" | "openai-responses";
  auth: "api_key" | "none" | "oauth";
  auth_modes?: string[];
  integration_status?: string;
  category: "free_tier" | "free" | "local" | "paid" | "gateway" | "custom";
  description: string;
  key_url?: string;
  defaults: Record<string, unknown>;
}
export interface GatewayConnection {
  id: string;
  revision: number;
  name: string;
  preset_id: string;
  endpoint: string;
  protocol: GatewayPreset["protocol"];
  models: GatewayModel[];
  credential_configured: boolean;
  status: "connected" | "error";
  error: null | { code: string; message: string };
}
export interface GatewayRoute {
  id: string;
  name: string;
  revision: number;
  models: string[];
  strategy: "priority" | "round_robin";
}
export interface GatewayKey {
  id: string;
  name: string;
  prefix: string;
  created_at: number;
  revoked: boolean;
  models: string[];
}
export interface GatewayUsage {
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  failures: number;
  recent: Array<{
    model: string;
    status: string;
    tokens: number;
    created_at: number;
  }>;
}

export interface GatewayAuthProvider {
  id: string;
  name: string;
  flow: "device_code" | "authorization_code_pkce";
  description: string;
  default_redirect_uri?: string;
  options: unknown[];
}
export interface GatewayAuthSession {
  id: string;
  provider_id: string;
  name: string;
  flow: GatewayAuthProvider["flow"];
  status:
    "pending" | "exchanging" | "connected" | "cancelled" | "expired" | "failed";
  expires_at: number;
  poll_after: number;
  authorization_url?: string;
  redirect_uri?: string;
  user_code?: string;
  verification_uri?: string;
  verification_uri_complete?: string;
  account_id?: string;
  error_code?: string;
}
export interface GatewayAuthAccount {
  id: string;
  provider_id: string;
  name: string;
  status: "connected" | "disconnected";
  expires_at: number;
  revision: number;
}
