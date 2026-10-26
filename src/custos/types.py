"""Custos core data model. FROZEN: changes need sign-off from the integration owner (see docs/SPEC.md §3)."""
from __future__ import annotations
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Literal
from pydantic import BaseModel

class Tier(IntEnum):
    T0 = 0   # external/untrusted: web, email, files, external peer agents, RAG docs from outside
    T1 = 1   # internal tools/APIs, internal wiki/KB, authenticated internal peer agents
    T2 = 2   # authenticated user statements (own data / instructions to own agent)
    T3 = 3   # systems of record (ERP, CRM, order DB, HRIS) or T2 + explicit confirmation
    T4 = 4   # system/operator policy, admin-signed config

class SourceType(str, Enum):
    SYSTEM = "system"; SOR = "system_of_record"; USER = "user"; USER_CONFIRMED = "user_confirmed"
    INTERNAL_TOOL = "internal_tool"; INTERNAL_KB = "internal_kb"; PEER_INTERNAL = "peer_internal"
    WEB = "web"; EMAIL = "email"; FILE = "file"; RAG_DOC = "rag_doc"; PEER_EXTERNAL = "peer_external"
    ASSISTANT = "assistant"; SUMMARY = "summary"; IMPORT = "import"

class Kind(str, Enum):
    FACT = "fact"; PREFERENCE = "preference"; EVENT = "event"; PROCEDURE = "procedure"

class Status(str, Enum):
    PENDING = "pending"; COMMITTED = "committed"; QUARANTINED = "quarantined"; REJECTED = "rejected"
    SUPERSEDED = "superseded"; REVERTED = "reverted"; ERASED = "erased"

class Sensitivity(str, Enum):
    PUBLIC = "public"; INTERNAL = "internal"; CONFIDENTIAL = "confidential"
    PII = "pii"; FINANCIAL = "financial"; HEALTH = "health"

class SourceRecord(BaseModel):
    """Raw evidence. Never retrieved by the agent; used for grounding, lineage and erasure."""
    source_id: str                      # "src_" + ulid
    tenant_id: str
    session_id: str | None
    source_type: SourceType
    origin: str                         # registrable domain / system name / "user:<id>" / "agent:<id>"
    principal: str                      # authenticated author: user id, "erp", "web:acme-supplies.co"
    external_ref: str | None            # email message-id, URL, ERP row id, tool_call id
    base_tier: Tier                     # tier policy (§4.1)
    tier: Tier                          # after LWM over parents (derived sources, e.g. SUMMARY)
    parents: list[str] = []             # source_ids / record_ids it was derived from
    raw_hash: str                       # sha256 hex of raw bytes
    blob_uri: str                       # content-addressed blob path: blobs/<sha256>
    owner: str                          # data principal (DPDP erasure)
    received_at: datetime
    ledger_seq: int
    signature: str

class Claim(BaseModel):
    subject: str                        # canonical entity id, e.g. "vendor:acme_supplies"
    relation: str                       # from relation vocabulary or "other:<snake>"
    object: str                         # normalised value
    qualifiers: dict[str, str] = {}
    kind: Kind
    is_instruction: bool                # directs the agent's future behaviour
    valid_from: datetime | None
    text: str                           # standalone sentence
    key: str                            # f"{tenant}|{subject}|{relation}" (+ sorted qualifiers)
    functional: bool                    # single-valued relation (from vocabulary)
    critical: bool                      # from vocabulary
    extraction_conf: float = 1.0

class MemoryRecord(BaseModel):
    """One atomic, retrievable memory = one claim."""
    record_id: str                      # "mem_" + ulid
    tenant_id: str
    owner: str                          # user id, or "tenant_shared"
    session_id: str | None
    claim: Claim
    content: str                        # = claim.text (what the backend stores and the agent sees)
    sources: list[str]                  # supporting source_ids (cited / extracted-from)
    parents: list[str] = []             # memory record_ids it was derived from (e.g. via a summary)
    base_tier: Tier
    tier: Tier                          # effective tier after LWM (§4.3)
    sensitivity: Sensitivity
    purposes: list[str] = ["*"]
    status: Status = Status.PENDING
    version: int = 1
    supersedes: str | None = None
    superseded_by: str | None = None
    corroborated_by: list[str] = []     # source_ids that independently assert the same key+object
    flags: list[str] = []               # ungrounded_dropped, drift_window, origin_mismatch, sor_mismatch,
                                        # sor_verified, corroborated, rederived
    # bi-temporal
    valid_from: datetime                # world time the fact became true (claim.valid_from or recorded_at)
    valid_to: datetime | None = None    # world time it stopped being true (set on supersede)
    recorded_at: datetime               # transaction time in
    closed_at: datetime | None = None   # transaction time out (supersede / revert / erase)
    backend: str | None = None
    backend_ref: str | None = None
    verdict_id: str | None = None
    ledger_seq: int | None = None
    signature: str | None = None

class LedgerEntry(BaseModel):
    seq: int
    ts: datetime
    event: Literal["source", "write", "verdict", "commit", "version", "quarantine", "reject", "release",
                   "corroborate", "read", "gate", "gate_response", "revert", "restore", "rederive",
                   "erase", "checkpoint", "key"]
    tenant_id: str | None
    subject_id: str | None              # record_id / source_id / gate_id / op_id
    actor: str                          # "gateway" | reviewer id | user id
    payload: dict[str, Any]             # canonical-JSON-able; PII content stored by digest only
    payload_digest: str                 # sha256(JCS(payload))
    prev_hash: str
    entry_hash: str                     # sha256(prev_hash|seq|ts|event|subject_id|payload_digest)
    signature: str                      # base64 Ed25519(entry_hash) by gateway key
    key_id: str

class ReadLogEntry(BaseModel):
    read_id: str
    ts: datetime
    tenant_id: str
    principal: str
    session_id: str
    purpose: str
    query_hash: str
    returned: list[str]                 # record_ids in rank order
    scores: list[float]
    filtered: dict[str, int]            # reason → count (other tenants never appear: never scored)
    ledger_seq: int

class CheckResult(BaseModel):
    check: Literal["key_conflict", "embed_conflict", "nli", "grounding", "drift", "instruction",
                   "corroboration", "sor_verify", "pi_filter"]
    ok: bool                            # True = no problem found
    label: str | None = None            # e.g. "contradicts", "unsupported", "alarm", "mismatch"
    score: float | None = None
    against: list[str] = []             # record_ids involved
    detail: dict[str, Any] = {}
    error: str | None = None            # set if the checker failed → fail closed (R0)
    latency_ms: float = 0.0

class Verdict(BaseModel):
    verdict_id: str
    record_id: str
    action: Literal["commit", "version", "quarantine", "reject"]
    rule: str                           # e.g. "R5_LOWER_TIER_CONFLICT"
    reasons: list[str]                  # human-readable
    checks: list[CheckResult]
    conflicts_with: list[str] = []
    policy_engine: Literal["python", "opa"] = "python"
    decided_at: datetime
    latency_ms: float

class QuarantineItem(BaseModel):
    qid: str
    tenant_id: str
    record_id: str
    rule: str
    reasons: list[str]
    conflicts_with: list[str] = []
    kind: Literal["review", "confirm", "corroborate", "retry"]   # retry = fail-closed hold
    status: Literal["open", "released", "rejected", "expired"] = "open"
    created_at: datetime
    resolved_at: datetime | None = None
    reviewer: str | None = None
    note: str | None = None

class ArgTaint(BaseModel):
    arg: str
    value: str
    required_tier: Tier
    support: list[str]                  # ctx_ids whose text/claims contain the value
    support_records: list[str]          # memory record_ids among the support
    support_sources: list[str]          # source_ids among the support (tool results, user turns)
    tier: Tier | None                   # MAX tier over support (strongest evidence); None = no support
    origin: Literal["user_turn", "tool_result", "memory", "mixed", "none"]
    conflicts_with_sor: bool = False

class GateDecision(BaseModel):
    gate_id: str
    tenant_id: str
    session_id: str
    tool: str
    args: dict[str, Any]
    risk: Literal["low", "medium", "high", "critical"]
    arg_taint: list[ArgTaint]
    decisive_records: list[str] = []    # from leave-one-out
    decisive_sources: list[str] = []    # root SourceRecords behind decisive inputs (e.g. the email)
    loo_calls: int = 0
    decision: Literal["allow", "ask", "block"]
    rule: str                           # G0_ALLOW | G1_SOR_CONFLICT | G2_LOW_TIER_ARG | G3_DECISIVE_LOW_TIER
                                        # | G4_UNKNOWN_ORIGIN | G9_FAIL_CLOSED
    explanation: str                    # one paragraph, names the source
    latency_ms: float
    decided_at: datetime
    user_response: Literal["approved", "denied"] | None = None

class Episode(BaseModel):
    episode_id: str                     # sha256(JCS(all fields below))[:16]
    grid: str
    split: Literal["dev", "test"]
    scenario: Literal["finance", "support", "email", "multiagent", "longmemeval", "locomo"]
    family: str                         # f01..f11 | benign | legit_update | utility_q
    variant_id: str
    backend: Literal["langgraph", "mem0", "graphiti", "letta", "native"]
    defence: str                        # D0 | D1 | D2 | D3 | D3-tiers | D3-consistency | D3-gate
                                        # | D3-quoting | D3-isolation | D3-claimlwm
    memory_state: Literal["cold", "warm"]
    warm_records: int = 0
    trigger_sessions: int = 1
    user_policy: Literal["cautious", "fatigued"] = "cautious"
    adaptive_round: int = 0
    seed: int
    models: dict[str, str]              # role → pinned model id
    config_hash: str                    # hash of settings.yaml thresholds

class EpisodeResult(BaseModel):
    episode_id: str
    status: Literal["ok", "error", "timeout"]
    error: str | None = None
    # attack outcome
    injected: bool = False
    stored: bool = False                # poison retrievable after injection phase (ISR numerator)
    retrieved_in_trigger: bool = False
    target_behaviour: bool = False      # ASR numerator (under user_policy)
    target_behaviour_fatigued: bool = False
    target_event: dict[str, Any] | None = None
    blocked_by: Literal["pi_filter", "consensus", "quarantine", "reject", "read_filter", "gate",
                        "agent_refused", "none"] = "none"
    sessions_acted: int = 0             # persistence numerator
    attribution: dict[str, Any] | None = None   # {true_ids, named_ids, hit, precision}
    # benign outcome
    benign_success: bool | None = None
    benign_asks: int = 0
    false_quarantines: int = 0
    legit_writes: int = 0
    leaked_records: int = 0
    # cost
    llm_calls: dict[str, int] = {}      # role → count
    tokens: dict[str, int] = {}         # "<role>.prompt" / "<role>.completion" / "<role>.cached"
    latency_ms: dict[str, list[float]] = {}   # read_overhead / gate / write_verdict samples
    verdicts: list[str] = []
    gate_ids: list[str] = []
    trace_path: str | None = None
    started_at: datetime
    finished_at: datetime


# ---- request / response models (also frozen) ----

class ContextItem(BaseModel):
    ctx_id: str                         # "c<session>_<n>", shown to the planner as [c3_7]
    session_id: str
    kind: Literal["system", "user_turn", "assistant_turn", "tool_result", "memory"]
    tier: Tier
    text: str
    source_id: str | None = None        # user turns & tool results are registered as SourceRecords
    record_id: str | None = None        # memories
    tool: str | None = None

class WriteRequest(BaseModel):
    tenant_id: str
    owner: str
    session_id: str | None
    content: str
    via: Literal["remember", "summary", "sor_sync", "profile", "import"]
    cites: list[str] = []               # ctx_ids / source_ids / record_ids
    source: SourceRecord | None = None  # direct ingestion (sor_sync / import)
    purposes: list[str] = ["*"]
    sensitivity_hint: Sensitivity | None = None
    wait: bool = False                  # block until verdicts (bench uses drain() instead)

class SummaryClaim(BaseModel):
    text: str
    cites: list[str]                    # ctx_ids

class SummaryOut(BaseModel):
    text: str                           # what D0 stores
    claims: list[SummaryClaim]          # what D3 sends through the gateway

class ReadRequest(BaseModel):
    tenant_id: str
    principal: str
    session_id: str
    purpose: str
    query: str
    k: int = 8

class RetrievedContext(BaseModel):
    read_id: str
    records: list[MemoryRecord]
    rendered: str                       # memory block for the prompt (§6.4)
    standing_instructions: str          # T2+ confirmed procedures only
    filtered: dict[str, int]

class GateRequest(BaseModel):
    tenant_id: str
    session_id: str
    tool: str
    args: dict[str, Any]
    context: list[ContextItem]          # exactly what the planner saw
    replan_handle: str | None = None    # key into the in-process replan registry (LOO)

class RollbackReport(BaseModel):
    op_id: str
    kind: Literal["revert", "erase"]
    roots: list[str]
    closure: list[str]
    reverted: list[str]
    restored: list[str]
    rederived: list[str]
    erased_sources: list[str]
    exposed_sessions: list[str]
    exposed_principals: list[str]
    affected_gate_ids: list[str]
    ledger_from: int
    ledger_to: int
