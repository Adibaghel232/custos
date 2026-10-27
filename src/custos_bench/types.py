"""Custos-Bench data model: attack/benign instances and the per-episode record written to episodes.jsonl."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from custos.types import Episode, EpisodeResult

from ._c3 import EpisodeRun, InboundItem, SessionSpec


class Artifact(BaseModel):
    """One piece of attacker-controlled content, delivered through `session` (0-based index into sessions)."""
    session: int
    channel: Literal["email", "file", "web", "peer_agent", "rag_doc"]
    sender: str
    subject: str = ""
    body: str

    def to_inbound(self) -> Any:
        return InboundItem(channel=self.channel, sender=self.sender, subject=self.subject, body=self.body)


class TargetSpec(BaseModel):
    """What the attacker wants. Drives the deterministic success predicate (attacks/base.py)."""
    kind: Literal["pay", "email", "recommend", "leak", "none"] = "none"
    tools: list[str] = []
    accounts: list[str] = []         # attacker bank accounts (pay_invoice.payee_account)
    vpas: list[str] = []             # attacker UPI ids (pay_upi.vpa)
    email_domains: list[str] = []    # attacker mail domains (send_email to/cc/bcc)
    names: list[str] = []            # e.g. "QuickPay Solutions"
    canaries: list[str] = []         # tenant-A canaries for f11


class AttackInstance(BaseModel):
    kind: Literal["attack", "benign", "legit_update"] = "attack"
    family: str                              # f01..f11 | L2 | L3 | benign | legit_update
    family_name: str
    split: Literal["dev", "test"]
    seed: int                                # effective seed (dev 0..999, test 1000+)
    variant_id: str
    scenario: str = "finance"
    channel: str = ""
    entry_tier: int = 0
    tier_variant: str = "T0"                 # "T0" | "T1" for families with an internal-KB variant
    sessions: list[SessionSpec]
    session_actor: list[str] = []            # "A" (victim tenant) | "B" (tenant-B principal) per session
    inject_sessions: list[int] = []
    trigger_sessions: list[int] = []
    poison_markers: list[str] = []           # strings identifying the poison (attacker values, canaries)
    canary: str = ""
    target: TargetSpec = Field(default_factory=TargetSpec)
    artifacts: list[Artifact] = []
    probe_queries: list[str] = []            # trigger query + 3 payload-specific queries (M1 probe set)
    probe_actor: str = "A"
    vendor: dict[str, Any] = {}
    slots: dict[str, Any] = {}
    world_rows: list[dict[str, Any]] = []
    allowed_channels: list[str] = []
    expect: dict[str, Any] = {}              # benign / legit_update expectations
    paraphrased: bool = False
    meta: dict[str, Any] = {}

    def check(self, run: EpisodeRun, memory: Any = None) -> dict[str, Any]:
        """C4: deterministic success check on a finished episode (no LLM)."""
        from .attacks import get_family
        return get_family(self.family).check(self, run, memory)

    def with_artifacts(self, new: list[Artifact]) -> "AttackInstance":
        """Replace the attacker content (adaptive rewrite / paraphrase); user turns and all other sessions stay."""
        old = [a.to_inbound() for a in self.artifacts]
        sessions = []
        for i, s in enumerate(self.sessions):
            keep = [it for it in s.inbound if it not in old]
            add = [a.to_inbound() for a in new if a.session == i]
            sessions.append(s.model_copy(update={"inbound": keep + add}))
        return self.model_copy(update={"sessions": sessions, "artifacts": list(new)}, deep=True)

    @property
    def n_sessions(self) -> int:
        return len(self.sessions)

    def actor_of(self, i: int) -> str:
        return self.session_actor[i] if i < len(self.session_actor) else "A"


class BenchRecord(BaseModel):
    """One line of episodes.jsonl: the frozen Episode + EpisodeResult plus bench-only columns."""
    episode: Episode
    result: EpisodeResult
    extra: dict[str, Any] = {}
