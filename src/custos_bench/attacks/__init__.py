"""Attack families (SPEC §13): registry and generation entry points."""
from __future__ import annotations

from ..types import AttackInstance
from .base import AttackFamily, check_instance  # noqa: F401
from .benign import Benign, Legit
from .families import F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, L2, L3

_ALL: list[AttackFamily] = [F01(), F02(), F03(), F04(), F05(), F06(), F07(), F08(), F09(), F10(), F11(), L2(), L3(),
                            Benign(), Legit()]
FAMILIES: dict[str, AttackFamily] = {f.id: f for f in _ALL}
FAMILIES["L1"] = FAMILIES["f03"]          # L1 self-summarisation == f03 (SPEC §13)
FAMILIES["l2"], FAMILIES["l3"] = FAMILIES["L2"], FAMILIES["L3"]
ATTACK_IDS = [f"f{i:02d}" for i in range(1, 12)] + ["L2", "L3"]     # the 13 attack cells of the main grid
PERSIST_IDS = [f"f{i:02d}" for i in range(1, 12)]


def get_family(fid: str) -> AttackFamily:
    try:
        return FAMILIES[fid]
    except KeyError:
        raise KeyError(f"unknown family {fid!r}; known: {sorted(set(FAMILIES))}") from None


def generate(family: str, split: str, seed: int, *, n_trigger: int = 1, tier_variant: str = "T0") -> AttackInstance:
    """C4: deterministic instance for (family, split, seed). dev seeds 0..999, test seeds are offset by 1000."""
    return get_family(family).generate(split, seed, n_trigger=n_trigger, tier_variant=tier_variant)
