"""Backward-compatibility shim — real implementation in jarvis_engine.phone.scam_hunter."""
from jarvis_engine.phone.scam_hunter import *  # noqa: F401,F403
from jarvis_engine.phone.scam_hunter import (  # noqa: F401 — underscore names not covered by star
    _generate_campaign_id,
    _AREA_CODE_TZ,
    _TZ_UTC_OFFSETS,
    _KNOWN_VOIP_DOMAINS,
    _SCAM_LABELS,
    _VOIP_LATENCY_THRESHOLD_MS,
    _CAMPAIGNS_LOCK,
)
