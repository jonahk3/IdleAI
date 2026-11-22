"""Carbon data: how clean each laptop's electricity is.

Everything else calls the functions here and never reads the data file directly,
so a live source (e.g. Electricity Maps) can replace provinces.json later without
changing any other code.
"""
import json
from functools import lru_cache
from pathlib import Path

from peer.config import LAPTOP_WATTS, UNKNOWN_G_CO2_PER_KWH

DATA_FILE = Path(__file__).with_name("provinces.json")


@lru_cache(maxsize=1)
def provinces() -> dict[str, dict]:
    """{"QC": {"name": "Quebec", "g_co2_per_kwh": 2}, ...}, cleanest first."""
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))["provinces"]
    return dict(sorted(data.items(), key=lambda kv: kv[1]["g_co2_per_kwh"]))


def intensity(location: str | None) -> float:
    """Grams of CO2 per kWh where this laptop is (unknown = treated as the dirtiest)."""
    info = provinces().get((location or "").upper())
    return float(info["g_co2_per_kwh"]) if info else float(UNKNOWN_G_CO2_PER_KWH)


def label(location: str | None) -> str:
    """'QC (2 g/kWh)' or 'unknown location'."""
    code = (location or "").upper()
    return f"{code} ({intensity(code):g} g/kWh)" if code in provinces() else "unknown location"


def estimate_grams(location: str | None, seconds: float) -> float:
    """Rough CO2 for running a model this long here: laptop power x time x grid intensity."""
    kwh = LAPTOP_WATTS * seconds / 3600 / 1000
    return kwh * intensity(location)
