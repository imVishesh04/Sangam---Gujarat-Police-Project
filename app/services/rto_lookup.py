"""
RTO / vehicle-registration lookup for the alert agent.

Three layers, from always-true to needs-authorisation:

1. Plate decode (always on, public facts)
   The plate itself says which state and which RTO office registered it:
   GJ 01 AB 1234 -> Gujarat, GJ-01 = Ahmedabad RTO. No database needed.

2. Registry provider (configurable with RTO_PROVIDER)
   * "sample"     - demo records in services/data/sample_vehicle_registry.json.
                    Every result is labelled SAMPLE so it can never be mistaken
                    for a real government record.
   * "vahan_http" - adapter for an AUTHORISED VAHAN / Parivahan endpoint
                    (RTO_API_URL + RTO_API_KEY). Owner data is not public;
                    police access goes through NIC authorisation.
   * "none"       - plate decode only.

3. Privacy (PII minimisation)
   Results leave the system over WhatsApp, a third-party channel, so owner
   names are masked and phone / address / chassis / engine numbers are
   never forwarded.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

PLATE_PARTS = re.compile(r"^([A-Z]{2})(\d{1,2})([A-Z]{1,3})(\d{4})$")

STATE_NAMES = {
    "AN": "Andaman & Nicobar", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh",
    "AS": "Assam", "BR": "Bihar", "CG": "Chhattisgarh", "CH": "Chandigarh",
    "DD": "Dadra & Nagar Haveli and Daman & Diu", "DN": "Dadra & Nagar Haveli",
    "DL": "Delhi", "GA": "Goa", "GJ": "Gujarat", "HP": "Himachal Pradesh",
    "HR": "Haryana", "JH": "Jharkhand", "JK": "Jammu & Kashmir", "KA": "Karnataka",
    "KL": "Kerala", "LA": "Ladakh", "LD": "Lakshadweep", "MH": "Maharashtra",
    "ML": "Meghalaya", "MN": "Manipur", "MP": "Madhya Pradesh", "MZ": "Mizoram",
    "NL": "Nagaland", "OD": "Odisha", "OR": "Odisha", "PB": "Punjab",
    "PY": "Puducherry", "RJ": "Rajasthan", "SK": "Sikkim", "TN": "Tamil Nadu",
    "TR": "Tripura", "TS": "Telangana", "TG": "Telangana", "UK": "Uttarakhand",
    "UP": "Uttar Pradesh", "WB": "West Bengal",
}

# Gujarat RTO / ARTO offices (checked against public RTO code lists, Oct 2026).
GUJARAT_RTO = {
    1: "Ahmedabad", 2: "Mehsana", 3: "Rajkot", 4: "Bhavnagar", 5: "Surat",
    6: "Vadodara", 7: "Nadiad", 8: "Palanpur", 9: "Himatnagar", 10: "Jamnagar",
    11: "Junagadh", 12: "Bhuj (Kutch)", 13: "Surendranagar", 14: "Amreli",
    15: "Valsad", 16: "Bharuch", 17: "Godhra", 18: "Gandhinagar", 19: "Bardoli",
    20: "Dahod", 21: "Navsari", 22: "Rajpipla", 23: "Anand", 24: "Patan",
    25: "Porbandar", 26: "Vyara", 27: "Ahmedabad East", 28: "Surat (Pal)",
    29: "Vadodara (Darjipura)", 30: "Ahwa", 31: "Modasa (Aravalli)", 32: "Veraval",
    33: "Botad", 34: "Chhota Udepur", 35: "Lunawada", 36: "Morbi",
    37: "Khambhaliya", 38: "Bavla",
}

_SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "data", "sample_vehicle_registry.json")
_FORWARDABLE_FIELDS = ("make", "model", "colour", "fuel", "vehicle_class",
                       "registration_year", "insurance_valid_till", "puc_valid_till",
                       "rc_status")


def decode_plate(plate: str) -> Optional[dict]:
    """Public facts derived from the plate text alone."""
    m = PLATE_PARTS.match((plate or "").upper())
    if not m:
        return None
    state, rto, series, number = m.groups()
    rto_num = int(rto)
    office = GUJARAT_RTO.get(rto_num) if state == "GJ" else None
    return {
        "state_code": state,
        "state": STATE_NAMES.get(state, state),
        "rto_code": f"{state}-{rto_num:02d}",
        "rto_office": office,   # None when not in the local table
        "series": series,
        "number": number,
    }


def mask_name(name: str) -> str:
    """'Ramesh Patel' -> 'R***** P****'. Keeps initials only."""
    parts = [p for p in (name or "").split() if p]
    return " ".join(p[0] + "*" * max(2, len(p) - 1) for p in parts) or "-"


def _minimise(record: dict) -> dict:
    out = {k: record[k] for k in _FORWARDABLE_FIELDS if record.get(k)}
    if record.get("owner_name"):
        out["owner_masked"] = mask_name(record["owner_name"])
    return out


def _sample_lookup(plate: str) -> tuple[Optional[dict], str]:
    try:
        with open(_SAMPLE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return None, f"sample registry unavailable ({exc})"
    rec = data.get("records", {}).get(plate.upper())
    if not rec:
        return None, "no record in SAMPLE registry (VAHAN not connected)"
    return _minimise(rec), "SAMPLE registry (demo data, not VAHAN)"


def _vahan_http_lookup(plate: str) -> tuple[Optional[dict], str]:
    url = os.environ.get("RTO_API_URL", "").strip()
    key = os.environ.get("RTO_API_KEY", "").strip()
    if not url or not key:
        return None, "VAHAN adapter not configured (needs authorised RTO_API_URL + RTO_API_KEY)"
    try:
        import requests
        resp = requests.get(url, params={"reg_no": plate.upper()},
                            headers={"Authorization": f"Bearer {key}"}, timeout=6)
        if resp.status_code != 200:
            return None, f"VAHAN lookup failed (HTTP {resp.status_code})"
        body = resp.json()
        rec = body.get("data", body) if isinstance(body, dict) else {}
        return _minimise(rec), "VAHAN (authorised API)"
    except Exception as exc:  # noqa: BLE001
        logger.error("VAHAN lookup error for %s: %s", plate, type(exc).__name__)
        return None, f"VAHAN lookup error ({type(exc).__name__})"


def lookup(plate: str) -> dict:
    """
    Returns {"decode": {...}|None, "record": {...}|None, "source": str}.
    Never raises - the alert must go out even if the registry is down.
    """
    provider = os.environ.get("RTO_PROVIDER", "sample").strip().lower()
    decode = decode_plate(plate)
    if provider == "vahan_http":
        record, source = _vahan_http_lookup(plate)
    elif provider == "none":
        record, source = None, "registry lookup disabled"
    else:
        record, source = _sample_lookup(plate)
    return {"decode": decode, "record": record, "source": source}
