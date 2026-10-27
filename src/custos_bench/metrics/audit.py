"""Provenance audit of the sampled episode ledgers (M5a lineage, M5b tamper detection) -> provenance.csv.

Tamper categories (SPEC §9.1) that can be exercised on a ledger-only sample are run on fresh copies: 1 edit a payload,
2 delete an entry, 3 swap two entries, 4 insert a forged entry (bad signature), 7 truncate the tail (detected only against a
published checkpoint - we pass the final head hash), 8 append under a foreign key. Categories 5 (records-table edit) and
6 (blob replacement) need the live record/blob stores and are NOT run here; they are listed in `notes` so nothing is
overstated. Lineage (§9.2) is checked structurally: every committed record is signed, every source resolves with a
content-addressed blob uri, and every parent chain ends in SourceRecords."""
from __future__ import annotations

import json
import random
import shutil
import tempfile
from pathlib import Path

import orjson

from custos.crypto import Signer, sha256_hex
from custos.db import DB
from custos.ledger import Ledger, entry_hash
from custos.types import MemoryRecord, Status

from ..records_io import load_all_records

CATEGORIES = {1: "edit_payload", 2: "delete_entry", 3: "swap_entries", 4: "forged_entry", 7: "truncate_tail", 8: "foreign_key"}
NOT_RUN = "5 records-table edit and 6 blob replacement need live stores (not run on ledger samples)"


def _open(path: Path) -> DB:
    return DB(path)


def _pub(db: DB) -> str | None:
    r = db.one("SELECT payload FROM ledger WHERE event='key' ORDER BY seq LIMIT 1")
    return orjson.loads(r[0]).get("public_key") if r else None


def _tamper(db: DB, cat: int, rng: random.Random) -> None:
    n = db.one("SELECT COUNT(*) FROM ledger")[0]
    seqs = [r[0] for r in db.query("SELECT seq FROM ledger ORDER BY seq")]
    if cat == 1:
        s = rng.choice(seqs)
        db.execute("UPDATE ledger SET payload=? WHERE seq=?", (b'{"tampered":1}', s))
    elif cat == 2:
        db.execute("DELETE FROM ledger WHERE seq=?", (rng.choice(seqs[1:-1] or seqs[1:] or seqs),))
    elif cat == 3:
        a, b = sorted(rng.sample(seqs[1:], 2)) if len(seqs) > 3 else (seqs[0], seqs[-1])
        ra = db.one("SELECT payload, subject_id, event FROM ledger WHERE seq=?", (a,))
        rb = db.one("SELECT payload, subject_id, event FROM ledger WHERE seq=?", (b,))
        db.execute("UPDATE ledger SET payload=?, subject_id=?, event=? WHERE seq=?", (*tuple(rb), a))
        db.execute("UPDATE ledger SET payload=?, subject_id=?, event=? WHERE seq=?", (*tuple(ra), b))
    elif cat == 4:
        head = db.one("SELECT seq, entry_hash, key_id FROM ledger ORDER BY seq DESC LIMIT 1")
        body = b'{"forged":1}'
        dig = sha256_hex(body)
        ts = "2026-10-26T00:00:00.000Z"
        h = entry_hash(head["entry_hash"], head["seq"] + 1, ts, "write", "x", dig)
        db.execute("INSERT INTO ledger (seq, ts, event, tenant_id, subject_id, actor, payload, payload_digest, prev_hash,"
                   " entry_hash, signature, key_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (head["seq"] + 1, ts, "write", None, "x", "evil", body, dig, head["entry_hash"], h, "AA" * 64, head["key_id"]))
    elif cat == 7:
        k = max(1, min(n - 1, rng.randrange(1, 4)))
        db.execute("DELETE FROM ledger WHERE seq > (SELECT MAX(seq) FROM ledger) - ?", (k,))
    elif cat == 8:
        Ledger(db, Signer.generate()).append("write", tenant_id=None, subject_id="x", actor="evil", payload={"foreign": 1})


def audit_tamper(ledger_paths: list[Path], per_category: int = 125, seed: int = 0) -> dict[str, dict[str, int]]:
    out = {name: {"n": 0, "k": 0} for name in CATEGORIES.values()}
    if not ledger_paths:
        return out
    with tempfile.TemporaryDirectory() as td:
        for cat, name in CATEGORIES.items():
            for i in range(per_category):
                rng = random.Random(f"{seed}|{cat}|{i}")
                src = ledger_paths[i % len(ledger_paths)]
                cp = Path(td) / f"{cat}_{i}.db"
                shutil.copy(src, cp)
                db = _open(cp)
                head = db.one("SELECT entry_hash FROM ledger ORDER BY seq DESC LIMIT 1")[0]
                pub = _pub(db)
                _tamper(db, cat, rng)
                rep = Ledger(db, Signer.generate()).verify(pinned_public_key=pub, checkpoint_hash=head)
                out[name]["n"] += 1
                out[name]["k"] += int(not rep["ok"])
                db.conn.close()
                cp.unlink(missing_ok=True)
    return out


def audit_lineage(path: Path) -> tuple[int, int]:
    """(ok, total) committed records of one ledger/episode database."""
    db = _open(path)
    try:
        srcs = {r["source_id"]: r for r in db.query("SELECT source_id, doc FROM sources")}
        recs = {r["record_id"]: MemoryRecord.model_validate_json(r["doc"]) for r in db.query("SELECT record_id, doc FROM records")}
    finally:
        db.conn.close()

    def resolves(i: str, seen: set[str]) -> bool:
        if i in srcs:
            d = json.loads(srcs[i]["doc"])
            return bool(d.get("raw_hash")) and str(d.get("blob_uri", "")).endswith(d["raw_hash"]) and \
                all(resolves(p, seen) for p in d.get("parents", []) if p not in seen)
        if i in recs and i not in seen:
            seen.add(i)
            r = recs[i]
            return bool(r.sources or r.parents) and all(resolves(x, seen) for x in [*r.sources, *r.parents])
        return False

    committed = [r for r in recs.values() if r.status == Status.COMMITTED]
    ok = sum(1 for r in committed if r.signature and all(resolves(x, {r.record_id}) for x in [*r.sources, *r.parents])
             and (r.sources or r.parents))
    return ok, len(committed)


def run_audit(run_dir: Path, per_category: int = 125) -> list[dict]:
    """Audit every ledgers/*.db under run_dir; returns provenance.csv rows per defence."""
    recs = {r.episode.episode_id: r.episode.defence for r in load_all_records(run_dir)}
    by_def: dict[str, list[Path]] = {}
    for sub in [run_dir, *sorted(p for p in run_dir.glob("*") if p.is_dir())]:
        for p in sorted((sub / "ledgers").glob("*.db")) if (sub / "ledgers").exists() else []:
            by_def.setdefault(recs.get(p.stem, "D3"), []).append(p)
    rows: list[dict] = []
    for d, paths in sorted(by_def.items()):
        ok = tot = 0
        for p in paths:
            a, b = audit_lineage(p)
            ok, tot = ok + a, tot + b
        rows.append({"run_id": run_dir.name, "check": "lineage", "defence": d, "n": tot, "k": ok,
                     "notes": f"{len(paths)} sampled ledgers; structural: signature + resolvable sources/parents + content-addressed blob uri"})
        res = audit_tamper(paths, per_category)
        rows.append({"run_id": run_dir.name, "check": "tamper", "defence": d, "n": sum(v["n"] for v in res.values()),
                     "k": sum(v["k"] for v in res.values()),
                     "notes": json.dumps({"by_category": res, "not_run": NOT_RUN})})
    return rows
