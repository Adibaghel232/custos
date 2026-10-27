"""W5: ledger tamper/lineage audit and the access (leakage / erasure) probes, on small seeded stores."""
from __future__ import annotations

import asyncio

import pandas as pd

from custos.crypto import Signer
from custos.db import DB
from custos.ledger import Ledger
from custos_bench.harness.access import erasure, leakage, write_access_tables
from custos_bench.metrics.audit import audit_lineage, audit_tamper, run_audit


def _ledger_file(tmp_path, n=14):
    db = DB(":memory:")
    led = Ledger(db, Signer.generate())
    for i in range(n):
        led.append("write", tenant_id="t1", subject_id=f"mem_{i}", actor="gateway", payload={"i": i, "text": f"entry {i}"})
    p = tmp_path / "ledgers" / "ep1.db"
    p.parent.mkdir(parents=True)
    db.backup_to(p)
    return p


def test_tamper_audit_detects_every_ledger_level_category(tmp_path):
    p = _ledger_file(tmp_path)
    res = audit_tamper([p], per_category=8, seed=1)
    assert set(res) == {"edit_payload", "delete_entry", "swap_entries", "forged_entry", "truncate_tail", "foreign_key"}
    for name, v in res.items():
        assert v["n"] == 8 and v["k"] == 8, name
    assert Ledger(DB(p), Signer.generate()).verify()["ok"]                     # the original file is untouched


def test_lineage_on_empty_store_and_run_audit_rows(tmp_path):
    p = _ledger_file(tmp_path)
    assert audit_lineage(p) == (0, 0)
    rows = run_audit(tmp_path, per_category=4)
    assert {r["check"] for r in rows} == {"lineage", "tamper"}
    t = next(r for r in rows if r["check"] == "tamper")
    assert t["n"] == 24 and t["k"] == 24 and "not_run" in t["notes"]


def test_leakage_and_erasure_probes_small():
    iso3 = asyncio.run(leakage("D3", n_probes=200, n_tenants=4, per_tenant=12, n_extraction=30))
    assert iso3[0]["n_leaks"] == 0 and iso3[1]["n_leaks"] == 0
    abl = asyncio.run(leakage("D3-isolation", n_probes=200, n_tenants=4, per_tenant=12, n_extraction=30))
    assert abl[0]["n_leaks"] > 0                                             # post-filter ablation leaks
    d0 = asyncio.run(leakage("D0", n_probes=100, n_tenants=4, per_tenant=12, n_extraction=10))
    assert d0[0]["n_leaks"] > 0                                              # shared namespace leaks
    e3 = asyncio.run(erasure("D3", n_principals=3))
    assert all(r["residual_hits"] == 0 and r["present_before"] > 0 and r["derived_removed"] == 1 for r in e3)
    e0 = asyncio.run(erasure("D0", n_principals=3))
    assert all(r["residual_hits"] > 0 and r["derived_removed"] == 0 for r in e0)       # summary still holds the canary


def test_access_tables_feed_metrics(tmp_path):
    iso = asyncio.run(leakage("D3", n_probes=100, n_tenants=3, per_tenant=8, n_extraction=10))
    era = asyncio.run(erasure("D3", n_principals=2)) + asyncio.run(erasure("D0", n_principals=2))
    write_access_tables(tmp_path, iso, era)
    assert {"probe_type", "defence", "n_probes", "n_leaks"} <= set(pd.read_csv(tmp_path / "isolation.csv").columns)
    (tmp_path / "episodes.jsonl").write_text("")
    from custos_bench.metrics.compute import _side_metrics
    m3 = _side_metrics("D3", None, None, pd.read_csv(tmp_path / "isolation.csv"), pd.read_csv(tmp_path / "erasure.csv"), None)
    assert m3["Leakage"]["k"] == 0 and m3["Leakage"]["n"] == 100 and m3["Leakage"]["r3_upper"] == 0.03
    assert m3["Erasure"]["completeness"] == 1.0
    m0 = _side_metrics("D0", None, None, pd.read_csv(tmp_path / "isolation.csv"), pd.read_csv(tmp_path / "erasure.csv"), None)
    assert m0["Erasure"]["completeness"] < 1.0
