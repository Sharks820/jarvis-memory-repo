"""Scam Campaign Hunter endpoints."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from jarvis_engine._constants import runtime_dir as _runtime_dir

logger = logging.getLogger(__name__)


class ScamRoutesMixin:
    """Scam reporting, lookup, campaign, and stats endpoints."""

    def _handle_post_scam_report_call(self) -> None:
        """Report a screened call with STIR/SHAKEN status for campaign analysis.

        Accepts: {number, stir_status, presentation, duration_sec, answered, contact_name}
        Returns: {ok, campaign_id?, enhanced_score, recommended_action}
        """
        body, _ = self._read_json_body(max_content_length=5_000)
        if body is None:
            return
        root = self._root
        try:
            from jarvis_engine.scam_hunter import (
                create_call_intel_report,
                save_call_intel,
                load_call_intel,
                detect_campaigns,
                save_campaigns,
                compute_enhanced_spam_score,
                lookup_carrier_cached,
            )
            from jarvis_engine.phone_guard import _normalize_number
            from jarvis_engine._shared import safe_float as _safe_float

            number = str(body.get("number", ""))
            stir_status = str(body.get("stir_status", ""))
            presentation = str(body.get("presentation", ""))
            duration_sec = _safe_float(body.get("duration_sec", 0))
            answered = bool(body.get("answered", False))
            contact_name = str(body.get("contact_name", ""))
            caller_display_name = str(body.get("caller_display_name", ""))
            gateway_domain = str(body.get("gateway_domain", ""))
            setup_latency_ms = int(_safe_float(body.get("setup_latency_ms", 0)))

            # Create and save intel report
            report = create_call_intel_report(
                number=number,
                stir_status=stir_status,
                presentation=presentation,
                duration_sec=duration_sec,
                answered=answered,
                contact_name=contact_name,
            )
            intel_path = _runtime_dir(root) / "call_intel.jsonl"
            save_call_intel(intel_path, report)

            # Check carrier cache
            carrier_cache_path = _runtime_dir(root) / "carrier_cache.json"
            carrier = lookup_carrier_cached(carrier_cache_path, report.normalized)
            carrier_risk = 0.0
            line_type = ""
            if carrier:
                line_type = carrier.line_type
                carrier_risk = carrier.risk_score

            # Run campaign detection on recent data
            all_reports = load_call_intel(intel_path, limit=200)
            campaigns = detect_campaigns(all_reports)
            campaign_path = _runtime_dir(root) / "scam_campaigns.json"
            save_campaigns(campaign_path, campaigns)

            # Check if this number belongs to a campaign
            campaign_id = ""
            campaign_confidence = 0.0
            campaign_signals: list[str] = []
            normalized = _normalize_number(number)
            for campaign in campaigns:
                if normalized in campaign.numbers:
                    campaign_id = campaign.campaign_id
                    campaign_confidence = campaign.confidence
                    campaign_signals = campaign.signals
                    break

            # Build base score: phone_guard pattern score + time-of-day
            from jarvis_engine.scam_hunter import score_time_of_day
            from jarvis_engine.phone_guard import detect_spam_candidates
            tod_score = score_time_of_day(normalized)
            base_score = tod_score
            # Check if phone_guard has a pattern-based score for this number
            pg_candidates = detect_spam_candidates(all_reports)
            for c in pg_candidates:
                if c.number == normalized:
                    base_score = max(base_score, c.score)
                    break

            # Compute enhanced score with ALL signals
            enhanced_score = compute_enhanced_spam_score(
                base_score=base_score,
                stir_status=stir_status,
                line_type=line_type,
                carrier_risk=carrier_risk,
                campaign_confidence=campaign_confidence,
                presentation=presentation,
                is_in_contacts=bool(contact_name),
                caller_display_name=caller_display_name,
                gateway_domain=gateway_domain,
                setup_latency_ms=setup_latency_ms,
            )

            # Determine action
            if enhanced_score >= 0.80:
                action = "block"
            elif enhanced_score >= 0.60:
                action = "silence"
            elif enhanced_score >= 0.40:
                action = "voicemail"
            else:
                action = "allow"

            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "enhanced_score": round(enhanced_score, 4),
                "recommended_action": action,
                "campaign_id": campaign_id,
                "campaign_confidence": round(campaign_confidence, 4),
                "line_type": line_type,
                "stir_status": stir_status,
                "signals": campaign_signals,
            })
        except Exception as exc:  # boundary: catch-all justified
            logger.warning("Scam report-call failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "enhanced_score": 0.0, "recommended_action": "voicemail", "error": "Scam report processing failed."})

    def _handle_post_scam_lookup(self) -> None:
        """Lookup carrier and VoIP status for a phone number.

        Accepts: {number}
        Returns: {ok, carrier, line_type, is_voip, campaign_id?, risk_score}
        """
        body, _ = self._read_json_body(max_content_length=5_000)
        if body is None:
            return
        try:
            from jarvis_engine.scam_hunter import (
                lookup_carrier_cached,
                load_campaigns,
            )
            from jarvis_engine.phone_guard import _normalize_number

            number = str(body.get("number", ""))
            normalized = _normalize_number(number)

            # Check carrier cache
            carrier_cache_path = _runtime_dir(self._root) / "carrier_cache.json"
            carrier = lookup_carrier_cached(carrier_cache_path, normalized)

            # Check campaigns
            campaign_path = _runtime_dir(self._root) / "scam_campaigns.json"
            campaigns = load_campaigns(campaign_path)
            campaign_id = ""
            campaign_confidence = 0.0
            campaign_signals: list[str] = []
            for c in campaigns:
                if normalized in c.numbers:
                    campaign_id = c.campaign_id
                    campaign_confidence = c.confidence
                    campaign_signals = c.signals
                    break

            result: dict[str, Any] = {
                "ok": True,
                "number": normalized,
                "carrier": carrier.carrier if carrier else "",
                "line_type": carrier.line_type if carrier else "",
                "is_voip": carrier.is_voip if carrier else False,
                "risk_score": carrier.risk_score if carrier else 0.0,
                "campaign_id": campaign_id,
                "campaign_confidence": round(campaign_confidence, 4),
                "campaign_signals": campaign_signals,
            }
            self._write_json(HTTPStatus.OK, result)
        except Exception as exc:  # boundary: catch-all justified
            logger.warning("Scam lookup failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "ok": False, "number": str(body.get("number", "")),
                "carrier": "", "line_type": "", "is_voip": False, "error": "Scam lookup processing failed.",
            })

    def _handle_get_scam_campaigns(self) -> None:
        """Return detected scam campaigns."""
        if not self._validate_auth(b""):
            return
        try:
            from jarvis_engine.scam_hunter import load_campaigns, build_prefix_block_actions
            from dataclasses import asdict

            campaign_path = _runtime_dir(self._root) / "scam_campaigns.json"
            campaigns = load_campaigns(campaign_path)
            block_actions = build_prefix_block_actions(campaigns)

            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "campaigns": [asdict(c) for c in campaigns],
                "block_actions": block_actions,
                "total_campaigns": len(campaigns),
                "total_scam_numbers": sum(len(c.numbers) for c in campaigns),
            })
        except Exception as exc:  # boundary: catch-all justified
            logger.warning("Scam campaigns fetch failed: %s", exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "campaigns": [], "block_actions": []})

    def _handle_get_scam_stats(self) -> None:
        """Return scam detection statistics."""
        if not self._validate_auth(b""):
            return
        try:
            from jarvis_engine.scam_hunter import load_campaigns, load_call_intel

            campaign_path = _runtime_dir(self._root) / "scam_campaigns.json"
            intel_path = _runtime_dir(self._root) / "call_intel.jsonl"
            campaigns = load_campaigns(campaign_path)
            all_intel = load_call_intel(intel_path, limit=500)

            # Stats
            total_screened = len(all_intel)
            stir_failed = sum(1 for r in all_intel if r.get("stir_status") == "failed")
            stir_passed = sum(1 for r in all_intel if r.get("stir_status") == "passed")
            voip_calls = sum(1 for r in all_intel if r.get("line_type", "").endswith("voip"))
            blocked_numbers = set()
            for c in campaigns:
                if c.confidence >= 0.60:
                    blocked_numbers.update(c.numbers)

            # Top prefixes by campaign activity
            prefix_counts: dict[str, int] = {}
            for c in campaigns:
                prefix_counts[c.prefix] = prefix_counts.get(c.prefix, 0) + len(c.numbers)
            top_prefixes = sorted(prefix_counts.items(), key=lambda x: x[1], reverse=True)[:5]

            # Top carriers
            carrier_counts: dict[str, int] = {}
            for c in campaigns:
                if c.carrier:
                    carrier_counts[c.carrier] = carrier_counts.get(c.carrier, 0) + len(c.numbers)
            top_carriers = sorted(carrier_counts.items(), key=lambda x: x[1], reverse=True)[:5]

            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "total_screened": total_screened,
                "stir_failed": stir_failed,
                "stir_passed": stir_passed,
                "voip_calls": voip_calls,
                "active_campaigns": len(campaigns),
                "total_scam_numbers": sum(len(c.numbers) for c in campaigns),
                "numbers_blocked": len(blocked_numbers),
                "top_scam_prefixes": [{"prefix": p, "numbers": n} for p, n in top_prefixes],
                "top_scam_carriers": [{"carrier": c, "numbers": n} for c, n in top_carriers],
            })
        except Exception as exc:  # boundary: catch-all justified
            logger.warning("Scam stats fetch failed: %s", exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "total_screened": 0, "active_campaigns": 0})
