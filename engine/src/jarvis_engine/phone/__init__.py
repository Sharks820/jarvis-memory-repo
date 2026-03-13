"""Phone guard and scam hunter subpackage."""

from jarvis_engine.phone.guard import (  # noqa: F401
    PhoneAction,
    SpamCandidate,
    append_phone_actions,
    area_key,
    build_phone_action,
    build_spam_block_actions,
    detect_spam_candidates,
    load_call_log,
    normalize_number,
    write_spam_report,
)
from jarvis_engine.phone.scam_hunter import (  # noqa: F401
    CallIntelReport,
    CarrierIntel,
    ScamCampaign,
    build_prefix_block_actions,
    compute_enhanced_spam_score,
    create_call_intel_report,
    detect_campaigns,
    load_call_intel,
    load_campaigns,
    lookup_carrier_cached,
    save_call_intel,
    save_campaigns,
    save_carrier_intel,
    score_time_of_day,
)
