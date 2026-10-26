# Cross-workstream contracts (FROZEN — the parent integrates against exactly these)

Already in the repo (W1, done; read the code, don't change public signatures):
`custos.types` (all models) · `custos.session` (SessionCtx, GatewayFlags) · `custos.settings` · `custos.compliance` ·
`custos.llm` (get_llm().chat(role, messages, tools=, schema=, temperature=, max_tokens=, thinking=, seed=) → LLMResponse;
FakeLLM; usage_scope) · `custos.embed` (get_embedder().embed(texts, "query"|"passage") → L2-normed np.ndarray; HashEmbedder) ·
`custos.crypto` (jcs, sha256_hex, now_utc, ts_str, Signer) · `custos.db.DB` (tables in SCHEMA) · `custos.ledger.Ledger`
(append(event, tenant_id=, subject_id=, actor=, payload=) → LedgerEntry; entries(); verify(); checkpoint()) ·
`custos.blobs.BlobStore` (put/get/text/verify/delete) · `custos.tiers` (base_tier, lwm, can_supersede, label, merge_labels) ·
`custos.provenance.Provenance` (add/add_many/parents/children/ancestors/closure_by_hop/descendants/edges) ·
`custos.store` (NativeStore: put_source/get_source/sources/put_record/get_record/records/index_record/unindex/search/similar;
NumpyIndex; MemoryBackend, VectorIndex, BackendItem, BackendHit) · `tests/fakes.py` (RegexFakeLLM, FakeEmbed).

## C1. Write pipeline — `custos/write.py` (W2)
```python
class WritePipeline:
    def __init__(self, store: NativeStore, ledger: Ledger, prov: Provenance, blobs: BlobStore,
                 flags: GatewayFlags | None = None, settings: Settings | None = None): ...
    events: list[Callable[[dict], None]]          # subscribers; emit {"type": "stamp"|"claims"|"check"|"decision"|..., "payload": {...}}
    quarantine: "QuarantineQueue"                # custos/quarantine.py
    async def register_source(self, *, tenant_id: str, owner: str, session_id: str | None, source_type: SourceType,
                              origin: str, principal: str, raw: str, external_ref: str | None = None,
                              parents: list[str] | None = None, cross_principal: bool = False) -> SourceRecord
    async def write(self, req: WriteRequest) -> list[Verdict]   # stamp→extract→checks→policy→apply. COMMITTED records are
                                                                # indexed (retrievable) before this returns; others never are.
    async def drain(self, tenant_id: str | None = None) -> None # no-op if writes are synchronous

class QuarantineQueue:
    def list(self, tenant_id: str, status: str = "open") -> list[QuarantineItem]
    async def release(self, qid: str, actor: str, note: str | None = None) -> MemoryRecord     # commit (or version)
    async def confirm_t3(self, qid: str, actor: str) -> MemoryRecord                           # USER_CONFIRMED upgrade, then release
    async def reject(self, qid: str, actor: str, note: str | None = None) -> None
```

## C2. Gateway facade — `custos/gateway.py` (W3)
```python
ReplanFn = Callable[[list[ContextItem]], Awaitable[dict[str, Any] | None]]
# given the planner context with some items removed, returns the args the planner now proposes for the SAME tool
# (None = it no longer calls the tool). Used for leave-one-out; at most settings.t("loo_max_calls") calls.

class CustosGateway:
    def __init__(self, db_path: str = ":memory:", *, flags: GatewayFlags | None = None, embedder=None,
                 signer: Signer | None = None, blobs_root: str | None = None, settings: Settings | None = None): ...
    db, store, ledger, prov, blobs, writer: WritePipeline, flags                       # attributes
    def on_event(self, cb: Callable[[dict], None]) -> None                             # all gateway events (UI/trace)
    async def register_source(self, **kw) -> SourceRecord                               # = writer.register_source
    async def write(self, req: WriteRequest) -> list[Verdict]                           # = writer.write
    async def read(self, req: ReadRequest) -> RetrievedContext                          # SPEC §6 (+ read_log)
    async def gate(self, req: GateRequest, replan: ReplanFn | None = None) -> GateDecision   # SPEC §7
    async def gate_response(self, gate_id: str, approved: bool) -> None
    async def revert(self, roots: list[str], actor: str) -> RollbackReport             # SPEC §8
    async def erase(self, *, tenant_id: str, actor: str, owner: str | None = None,
                    source_ids: list[str] | None = None) -> RollbackReport
    def verify_ledger(self) -> dict
    def graph(self, tenant_id: str) -> dict            # {"nodes": [GraphNode], "edges": [GraphEdge]} (DEMO_DECK_PLAN §3.8 shapes)
    async def snapshot(self) -> bytes                   # sqlite backup + index snapshot (warm-memory snapshots)
    async def restore(self, blob: bytes) -> None
```

## C3. Agent, world and episode runner — `agent/` (W4)
```python
# agent/memory_iface.py — SPEC §11.2 MemoryInterface (Protocol) + D0Memory (shared LangGraph InMemoryStore) + CustosMemory(gw)
# agent/worlds/finance.py
class FinanceWorld:            # deterministic; SPEC §12. Holds ERP (T3), inbox (T0), invoices, payments, sent mail, shares, grants
    def __init__(self, tenant_id: str, seed: int = 0, fixture: str = "default"): ...
    def deliver(self, item: "InboundItem") -> None     # email / file / web page / peer-agent msg lands in the inbox
    def erp_update(self, row: dict) -> None            # SoR change (synced to memory as T3 at session start)
    events: list[dict]                                 # executed tool effects: {"tool","args","status":"executed"|"blocked"|"asked_denied"|"asked_approved"}

class InboundItem(BaseModel): channel: Literal["email","file","web","peer_agent","rag_doc"]; sender: str; subject: str = ""; body: str
class SessionSpec(BaseModel):
    label: str; user_turns: list[str]; inbound: list[InboundItem] = []; erp_updates: list[dict] = []

class SessionRun(BaseModel):
    session_no: int; transcript: list[ContextItem]; tool_calls: list[dict]; gate_decisions: list[GateDecision]
    final_text: str; recalled_ids: list[str]
class EpisodeRun(BaseModel):
    sessions: list[SessionRun]; world_events: list[dict]; usage: dict

# agent/runner.py
async def run_episode(world: FinanceWorld, memory: MemoryInterface, sessions: list[SessionSpec], *,
                      tenant_id: str, principal: str, episode_id: str, user_policy: str = "cautious",
                      temperature: float = 0.0, trace: Callable[[dict], None] | None = None) -> EpisodeRun
```
`trace(event)` receives DEMO_DECK_PLAN §3.8 `TraceEvent`-shaped dicts (`type`, `session`, `payload`), so the demo
golden-path script can write `Trace` JSON for the UI and the bench can write `traces/<episode>.jsonl`.

## C4. Bench — `custos_bench/` (W5)
Attack family = `generate(split, seed) -> AttackInstance` with `sessions: list[SessionSpec]`, `inject_sessions`, `trigger_sessions`,
`poison_markers` (strings/values identifying the poison), and `check(run: EpisodeRun, memory) -> dict` (deterministic:
target_behaviour, stored, retrieved_in_trigger, ...). Defences = factories returning a MemoryInterface:
`D0 → D0Memory`, `D1 → PIFilterMemory(D0Memory)`, `D2 → AMemGuardMemory(D0Memory)`, `D3* → CustosMemory(CustosGateway(flags=GatewayFlags.for_defence(name)))`.
