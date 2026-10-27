"""custos-bench CLI: run | resume | consolidate | metrics | report | snapshot | paraphrase | grids (SPEC §15.2)."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Custos-Bench: attacks x defences harness and metrics.")
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


@app.command()
def run(cfg: str = typer.Argument(..., help="grid name (configs/bench/<name>.yaml) or a path to a YAML"),
        out: Path = typer.Option(None, "--out", help="output directory (default runs/<grid>)"),
        concurrency: int = typer.Option(0, help="max concurrent episodes (0 = from config)"),
        workers: int = typer.Option(1, help="accepted for CLI compatibility; one asyncio loop is used"),
        limit: int = typer.Option(0, help="run only the first N planned episodes"),
        no_resume: bool = typer.Option(False, "--no-resume", help="ignore existing episodes.jsonl"),
        verbose: bool = typer.Option(False, "-v")) -> None:
    """Run a grid; completed episodes in OUT/episodes.jsonl are skipped (resumable)."""
    from .harness.config import load_config
    from .harness.runner import run_grid
    _setup_logging(verbose)
    g = load_config(cfg)
    out = out or Path("runs") / g.name
    meta = asyncio.run(run_grid(g, out, concurrency=concurrency or None, resume=not no_resume, limit=limit or None))
    console.print(f"[green]done[/green] {meta.get('n_records')} records -> {out} {meta.get('status_counts')}")


@app.command()
def resume(run_dir: Path, concurrency: int = typer.Option(0), verbose: bool = typer.Option(False, "-v")) -> None:
    """Continue an interrupted run (re-reads the config stored in run.json)."""
    from .harness.config import GridConfig
    from .harness.runner import run_grid
    _setup_logging(verbose)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    g = GridConfig.model_validate(meta["config"])
    out = asyncio.run(run_grid(g, run_dir, concurrency=concurrency or None, resume=True))
    console.print(f"[green]resumed[/green] {out.get('n_records')} records {out.get('status_counts')}")


@app.command()
def consolidate(run_dir: Path) -> None:
    """episodes.jsonl -> episodes.parquet (flat table)."""
    from .metrics.compute import build_frame
    from .records_io import load_all_records
    df = build_frame(load_all_records(run_dir), run_dir.name)
    df = df.drop(columns=["lat"], errors="ignore")
    df.to_parquet(run_dir / "episodes.parquet", index=False)
    console.print(f"wrote {run_dir / 'episodes.parquet'} ({len(df)} rows)")


def _metrics(run_dir: Path, out: Path | None) -> "object":
    from .metrics.compute import compute_metrics
    from .metrics.tables import write_all
    _auto_audit(run_dir)
    c = compute_metrics(run_dir)
    od = out or (run_dir if run_dir.is_dir() else run_dir.parent)
    meta_path = run_dir / "run.json" if run_dir.is_dir() else None
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path and meta_path.exists() else None
    write_all(c, od, meta)
    return c


def _auto_audit(run_dir: Path) -> None:
    """Ledger/tamper audit of the sampled ledgers, unless provenance.csv already exists."""
    import pandas as pd
    from .metrics.audit import run_audit
    if run_dir.is_dir() and not (run_dir / "provenance.csv").exists() and any(run_dir.glob("**/ledgers/*.db")):
        rows = run_audit(run_dir)
        if rows:
            pd.DataFrame(rows).to_csv(run_dir / "provenance.csv", index=False)


@app.command()
def audit(run_dir: Path, per_category: int = typer.Option(125, help="tamper ops per category")) -> None:
    """Lineage + tamper audit of the 5% sampled ledgers -> provenance.csv (M5)."""
    import pandas as pd
    from .metrics.audit import run_audit
    rows = run_audit(run_dir, per_category)
    pd.DataFrame(rows).to_csv(run_dir / "provenance.csv", index=False)
    for r in rows:
        console.print(f"  {r['defence']:16s} {r['check']:8s} {r['k']}/{r['n']}")


@app.command()
def access(out: Path = typer.Argument(..., help="run directory that receives isolation.csv / erasure.csv"),
           defences: str = typer.Option("D3,D3-isolation,D0", help="comma list; D3* use the gateway, D0 the shared store"),
           probes: int = typer.Option(10_000), tenants: int = typer.Option(50), per_tenant: int = typer.Option(200),
           principals: int = typer.Option(50, help="erasure requests per defence")) -> None:
    """M10 leakage (10,000 probes) + MEXTRA probes + M12 erasure on seeded stores."""
    from .harness.access import erasure, leakage, write_access_tables
    iso, era = [], []
    for d in [x.strip() for x in defences.split(",") if x.strip()]:
        r = asyncio.run(leakage(d, n_probes=probes, n_tenants=tenants, per_tenant=per_tenant))
        iso += r
        if d in ("D3", "D0"):
            era += asyncio.run(erasure(d, n_principals=principals))
        console.print(f"  {d:14s} leaks {r[0]['n_leaks']}/{r[0]['n_probes']}  extraction {r[1]['n_leaks']}/{r[1]['n_probes']}")
    write_access_tables(out, iso, era)


@app.command()
def metrics(run_dir: Path, out: Path = typer.Option(None, "--out")) -> None:
    """metrics.json + cells/scores/episodes CSVs + final.json + FINAL.md."""
    c = _metrics(run_dir, out)
    m = c.metrics  # type: ignore[attr-defined]
    console.print(f"metrics for {m['n_ok']} ok episodes ({m['n_errors']} errors). flags: {len(m['flags'])}")
    for d, x in m["by_defence"].items():
        asr = x["ASR"]["rate"] if x["ASR"] else None
        console.print(f"  {d:16s} ASR={asr if asr is None else f'{100 * asr:.1f}%'}  MGS={x['MGS']}")


@app.command()
def report(run_dir: Path, out: Path = typer.Option(None, "--out")) -> None:
    """Charts (PNG 300 dpi + SVG) and UI JSON under OUT/figures; recomputes metrics first."""
    from .metrics.charts import make_figures
    c = _metrics(run_dir, out)
    res = make_figures(c, out or (run_dir if run_dir.is_dir() else run_dir.parent))  # type: ignore[arg-type]
    for k, v in res.items():
        console.print(f"  {k}: {v}")


snapshot_app = typer.Typer(help="warm-memory snapshots")
app.add_typer(snapshot_app, name="snapshot")


@snapshot_app.command("build")
def snapshot_build(defence: str = typer.Option("D0", help="D0 (also D1/D2) or D3"), n: int = 1000,
                   split: str = "test", scenario: str = "finance", verbose: bool = typer.Option(False, "-v")) -> None:
    """Build data/snapshots/<scenario>-<d0|d3>-<split>.bin with N benign background records."""
    from .harness.warm import build_warm
    _setup_logging(verbose)
    p = asyncio.run(build_warm(defence, n, split, scenario))
    console.print(f"wrote {p}")


@app.command()
def paraphrase(cfg: str, concurrency: int = typer.Option(8), verbose: bool = typer.Option(False, "-v")) -> None:
    """Pre-generate Nemotron paraphrases (role attacker) for every attack instance of a grid; cached in data/attacks/."""
    from .attacks.paraphrase import load_cache, paraphrase_instance
    from .harness.config import load_config, plan_episodes
    _setup_logging(verbose)
    g = load_config(cfg)
    seen, insts = set(), []
    for p in plan_episodes(g, use_paraphrase_cache=False):
        if p.inst.kind == "attack" and p.inst.variant_id not in seen:
            seen.add(p.inst.variant_id)
            insts.append(p.inst)
    cache = load_cache()

    async def go() -> None:
        sem = asyncio.Semaphore(concurrency)

        async def one(i):
            async with sem:
                await paraphrase_instance(i, cache)
        await asyncio.gather(*(one(i) for i in insts))
    asyncio.run(go())
    console.print(f"paraphrased {len(insts)} instances; cache size {len(cache)}")


@app.command()
def grids() -> None:
    """List the planned size of every grid in configs/bench."""
    from .harness.config import CONFIG_DIR, load_config, plan_episodes
    for p in sorted(CONFIG_DIR.glob("*.yaml")):
        g = load_config(p)
        n = len(plan_episodes(g)) if g.kind == "episodes" else (
            (g.adaptive.seeds * len(g.adaptive.families) * len(g.adaptive.defences) if g.adaptive else 0) if g.kind == "adaptive" else "LME")
        console.print(f"{g.name:10s} {g.kind:9s} {n!s:>6}  {g.description}")


if __name__ == "__main__":
    app()
