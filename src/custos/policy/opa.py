"""OPA mirror of the write policy: `opa eval` subprocess or a running `opa run --server` (REST)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..settings import ROOT
from .engine import PolicyInput

REGO_PATH = ROOT / "configs" / "policy" / "custos.rego"
QUERY = "data.custos.write.decision"


def find_opa() -> str | None:
    """Locate an `opa` binary: $OPA_BIN, PATH, the venv Scripts dir, ~/.local/bin."""
    cands = [os.getenv("OPA_BIN"), shutil.which("opa")]
    scripts = Path(sys.executable).parent
    cands += [str(scripts / n) for n in ("opa", "opa.exe")]
    cands += [str(Path.home() / ".local" / "bin" / "opa")]
    for c in cands:
        if c and Path(c).exists():
            return c
    return None


class OPAPolicy:
    """decide(PolicyInput) -> (action, rule), evaluated by OPA. `url` (e.g. http://localhost:8181) uses REST."""

    def __init__(self, binary: str | None = None, rego: Path | str = REGO_PATH, url: str | None = None):
        self.binary = binary or find_opa()
        self.rego = str(rego)
        self.url = url or os.getenv("CUSTOS_OPA_URL")
        if not self.url and not self.binary:
            raise RuntimeError("no opa binary found (set OPA_BIN or CUSTOS_OPA_URL)")

    def decide(self, x: PolicyInput) -> tuple[str, str]:
        inp = x.model_dump()
        if self.url:
            import httpx

            r = httpx.post(f"{self.url.rstrip('/')}/v1/data/custos/write/decision", json={"input": inp}, timeout=5)
            r.raise_for_status()
            out = r.json()["result"]
        else:
            p = subprocess.run([self.binary, "eval", "-d", self.rego, "--stdin-input", QUERY, "-f", "raw"],
                               input=json.dumps(inp), capture_output=True, text=True, timeout=30)
            if p.returncode != 0:
                raise RuntimeError(f"opa eval failed: {p.stderr[:300]}")
            out = json.loads(p.stdout)
        return out["action"], out["rule"]

    def decide_many(self, xs: list[PolicyInput]) -> list[tuple[str, str]]:
        """Batch evaluation in ONE opa process (used by the parity test): bulk rule over an array input."""
        if self.url:
            return [self.decide(x) for x in xs]
        q = ("[d | some i; inp := input.cases[i]; d := data.custos.write.decision with input as inp]")
        p = subprocess.run([self.binary, "eval", "-d", self.rego, "--stdin-input", q, "-f", "raw"],
                           input=json.dumps({"cases": [x.model_dump() for x in xs]}),
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise RuntimeError(f"opa eval failed: {p.stderr[:300]}")
        return [(d["action"], d["rule"]) for d in json.loads(p.stdout)]
