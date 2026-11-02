// Wire types for the Meridian gateway (/meridian/*, /v1/*) and meridian-control
// (/admin/*). Mirrors meridian/api/*.py and meridian_control/service.py.

// ── Gateway ────────────────────────────────────────────────────────────────

export type CircuitState = "closed" | "open" | "half_open"

export interface BackendStatus {
  name: string
  url: string
  engine: string
  model: string
  weight: number
  tags: string[]
  healthy: boolean
  idle: boolean
  inflight: number
  inflight_cost: number
  ewma_latency_ms: number
  queue_depth: number | null
  tokens_per_sec: number | null
  gpu_mem_util: number | null
  circuit: { state: CircuitState; consecutive_failures: number } | null
}

export interface CanaryStatus {
  enabled: boolean
  weight: number
  step: number
  rolled_back: boolean
  schedule_done: boolean
  window_samples: number
  window_error_rate: number | null
  canary_tags: string[]
  stable_tags: string[]
}

export interface GatewayStatus {
  strategy: string
  backends: BackendStatus[]
  canary?: CanaryStatus
}

export interface RequestRecord {
  request_id: string
  timestamp: string
  model: string
  stream: boolean
  chosen_backend: string
  status_code: number
  latency_ms: number
  error_type: string | null
  org_id: string | null
  team_id: string | null
  tier: string | null
}

export interface Capabilities {
  view_ops: boolean
  reload: boolean
  manage_keys: boolean
  read_all_cost: boolean
}

export interface Features {
  cost: boolean
  budgets: boolean
  key_lifecycle: boolean
  control_plane: boolean
  canary: boolean
  pii: boolean
}

export type Role = "viewer" | "operator" | "admin"

export interface Identity {
  key_id: string | null
  org_id: string
  team_id: string | null
  user_id: string | null
  role: Role | null
  cost_admin: boolean
  ops_admin: boolean
  allowed_models: string[]
  pii_policy: string | null
}

export interface WhoAmI {
  auth_enabled: boolean
  identity: Identity | null
  capabilities: Capabilities
  features: Features
}

export interface BucketCaps {
  token_capacity: number
  token_refill_rate: number
}

export interface ConfigSummary {
  version: string
  reloadable: boolean
  features: Features
  gateway: {
    strategy: string
    prefill_weight: number
    decode_weight: number
    default_max_tokens: number
    token_estimator: string
    queue_weight: number
    mem_weight: number
    max_body_bytes: number
    cache_affinity: {
      block_chars: number
      max_checkpoints: number
      ttl_s: number
      max_entries: number
      locality_weight: number
      hit_threshold: number
      min_trust: number
      feedback_correction: boolean
    }
  }
  health: { interval_s: number; timeout_s: number; fail_threshold: number; success_threshold: number }
  timeouts: { connect: number; read: number; write: number; pool: number; stream_read: number | null }
  resilience: {
    max_retries: number
    retry_backoff_base: number
    circuit_breaker: { enabled: boolean; failure_threshold: number; open_seconds: number }
  }
  tiering: {
    enabled: boolean
    long_prompt_tokens: number
    long_decode_tokens: number
    tiers: Record<string, string[]>
  }
  session_affinity: {
    enabled: boolean
    header: string
    ttl_s: number
    max_sessions: number
    active_sessions: number | null
  }
  canary: {
    enabled: boolean
    canary_tags: string[]
    stable_tags: string[]
    start_weight: number
    steps: { weight: number; duration_s: number | null }[]
    rollback_error_rate: number
    rollback_min_samples: number
    window_s: number
    tick_s: number
  }
  isolation: { mode: "shared" | "dedicated"; pools: Record<string, string[]> }
  rate_limit: {
    enabled: boolean
    token_capacity: number
    token_refill_rate: number
    max_buckets: number
    idle_ttl_s: number
    global: BucketCaps | null
    models: Record<string, BucketCaps | null>
    org_overrides: Record<string, BucketCaps | null>
  }
  budgets: {
    enabled: boolean
    store: string
    scopes: { orgs: number; teams: number; users: number; keys: number }
  }
  pii: { enabled: boolean; policy: string; entities: string[]; supported_entities: string[] }
  cost: {
    enabled: boolean
    store: string
    currency: string
    max_window_days: number
    default_prompt_per_1m: number
    default_completion_per_1m: number
    models: Record<string, { prompt_per_1m: number; completion_per_1m: number }>
  }
  auth: { enabled: boolean; key_count: number; keys_file_configured: boolean }
  audit_bus: { enabled: boolean; topic: string }
  control_plane: { enabled: boolean; url: string; poll_interval_s: number; tag: string }
  backends: {
    name: string
    health_endpoint: string
    telemetry: { type: string; interval_s: number } | null
    idle_timeout_min: number | null
    timeout_override: Record<string, number> | null
    upstream_auth: boolean
  }[]
}

export interface BudgetRow {
  scope_level: "org" | "team" | "user" | "key"
  scope_id: string
  period: "daily" | "monthly"
  bucket: string
  metric: "tokens" | "requests"
  cap: number
  consumed: number
  remaining: number
  utilization: number | null
  resets_at: string
}

export interface BudgetSnapshot {
  enabled: boolean
  generated_at?: string
  rows: BudgetRow[]
}

export interface ApiKeyView {
  key_id: string
  org_id: string
  team_id: string | null
  user_id: string | null
  allowed_models: string[]
  role: Role | null
  cost_admin: boolean
  ops_admin: boolean
  source: string
}

export interface KeyCreateInput {
  org_id: string
  team_id?: string | null
  user_id?: string | null
  key_id?: string | null
  allowed_models?: string[]
  pii_policy?: string | null
  cost_admin?: boolean
  ops_admin?: boolean
  role?: Role | null
}

export interface KeyCreateResult extends ApiKeyView {
  key: string
}

export interface UsageRow {
  org_id: string
  team_id: string
  model: string
  day: string
  key_id: string
  prompt_tokens: number
  completion_tokens: number
  requests: number
  cost: number
}

export interface UsageReport {
  enabled: boolean
  currency: string
  rows: UsageRow[]
}

export interface UsageQuery {
  org?: string
  team?: string
  key?: string
  window_days?: number
}

export interface ReloadReport {
  reloaded: boolean
  scope?: string
  [key: string]: unknown
}

export interface VersionInfo {
  name: string
  version: string
  api: string
}

export interface ModelList {
  object: "list"
  data: { id: string; object: string; owned_by?: string }[]
}

export interface ChatMessage {
  role: "system" | "user" | "assistant"
  content: string
}

export interface ChatRequestBody {
  model: string
  messages: ChatMessage[]
  stream?: boolean
  max_tokens?: number
  temperature?: number
  top_p?: number
  [key: string]: unknown
}

/** Response metadata Meridian stamps on proxied responses. */
export interface MeridianResponseMeta {
  requestId: string | null
  backend: string | null
  tier: string | null
  sessionRoute: string | null
  budgetRemainingTokens: string | null
  budgetRemainingRequests: string | null
  status: number
}

// ── Control plane ─────────────────────────────────────────────────────────

export type NodeStatus = "online" | "offline" | "revoked" | "enrolled"

export type EnginePhase =
  | "Pending"
  | "Pulling"
  | "Starting"
  | "Warming"
  | "Ready"
  | "Draining"
  | "Stopped"
  | "Failed"
  | "Quarantined"
  | "Orphaned"

export interface NodeSummary {
  node_id: string
  display_name: string
  labels: Record<string, string>
  status: NodeStatus
  revoked: boolean
  fencing_epoch: number
  incarnation: number
  highest_sequence: number
  active_session_id: string | null
  lease_expires_at: string | null
  desired_generation: number
  observed_generation: number | null
  observed_at: string | null
  created_at: string | null
  engines: { total: number; ready: number; failed: number; by_phase: Record<string, number> }
  capacity: {
    devices: number
    allocatable_vram_bytes: number
    free_ports: number | null
    running_engines: number | null
    queue_depth: number | null
    held_artifacts: number
  } | null
}

export interface Condition {
  type: string
  status: string
  reason?: string
  message?: string
  observed_at?: string
}

export interface ObservedEngine {
  engine_id: string
  phase: EnginePhase
  restart_count?: number
  loaded_artifact_digest?: string | null
  endpoint?: string
  model?: string
  queue_depth?: number | null
  conditions?: Condition[]
}

export interface Observation {
  node_id: string
  observed_generation: number
  engines?: ObservedEngine[]
  conditions?: Condition[]
  capacity?: {
    allocatable_vram_bytes?: Record<string, number>
    free_ports?: number
    held_artifacts?: string[]
    running_engines?: number
    queue_depth?: number
    nvlink_groups?: Record<string, string>
  }
  [key: string]: unknown
}

export interface AuditEvent {
  id: number
  node_id: string
  kind: string
  detail: string
  created_at: string | null
}

export interface NodeDetail extends NodeSummary {
  certificate: { serial: string; not_valid_before: string; not_valid_after: string } | null
  observation: Observation | null
  desired: { generation: number; created_at: string | null; snapshot: Record<string, unknown> } | null
  stop_authorized_engine_ids: string[]
  audit: AuditEvent[]
}

export interface ControlOverview {
  incarnation: number
  epoch_floor: number
  nodes: { total: number; by_status: Partial<Record<NodeStatus, number>> }
  claims_pending: number
  engines: { total: number; ready: number; failed: number }
  allocatable_vram_bytes: number
  routable_endpoints: number
  settings: {
    lease_ttl_seconds: number
    heartbeat_interval_seconds: number
    cert_lifetime_hours: number
    require_mtls: boolean
  }
}

export interface Claim {
  claim_id: string
  node_id: string
  status: "pending" | "approved" | "denied"
  created_at: string | null
}

export interface EnrollmentTokenView {
  token_ref: string
  auto_approve: boolean
  status: "active" | "used" | "expired"
  expires_at: string | null
}

export interface ProjectionEndpoint {
  engine_id: string
  node_id: string
  endpoint: string
  model: string
  provider: string
  routable: boolean
}

export interface PlacementResult {
  node_id: string
  device_ids: string[]
  device_id: string
  allocatable_vram_bytes: number
}
