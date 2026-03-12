"""Scam Campaign Hunter endpoints."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any, Protocol

from jarvis_engine.mobile_routes._helpers import MobileRouteHandlerProtocol
from jarvis_engine._shared import runtime_dir as _runtime_dir

logger = logging.getLogger(__name__)


class _ScamRoutesHandlerProtocol(MobileRouteHandlerProtocol, Protocol):
    def _parse_scam_report_body(self, body: dict[str, Any]) -> dict[str, Any]:
        ...

    def _save_intel_and_detect_campaigns(
        self,
        root: Any,
        fields: dict[str, Any],
    ) -> tuple[Any, list[Any], list[Any], str, float]:
        ...

    def _compute_scam_score_and_action(
        self,
        fields: dict[str, Any],
        all_reports: list[Any],
        campaigns: list[Any],
        line_type: str,
        carrier_risk: float,
    ) -> dict[str, Any]:
        ...


class ScamRoutesMixin:
    """Scam reporting, lookup, campaign, and stats endpoints."""

    @staticmethod
    def _parse_scam_report_body(body: dict[str, Any]) -> dict[str, Any]:
        """Parse and normalize fields from the scam report request body."""
        from jarvis_engine._shared import safe_float as _safe_float

        return {
            "number": str(body.get("number", "")),
            "stir_status": str(body.get("stir_status", "")),
            "presentation": str(body.get("presentation", "")),
            "duration_sec": _safe_float(body.get("duration_sec", 0)),
            "answered": bool(body.get("answered", False)),
            "contact_name": str(body.get("contact_name", "")),
            "caller_display_name": str(body.get("caller_display_name", "")),
            "gateway_domain": str(body.get("gateway_domain", "")),
            "setup_latency_ms": int(_safe_float(body.get("setup_latency_ms", 0))),
        }

    @staticmethod
    def _save_intel_and_detect_campaigns(
        root: Any, fields: dict[str, Any],
    ) -> tuple[Any, list[Any], list[Any], str, float]:
        """Create intel report, save it, run campaign detection.

        Returns (report, all_reports, campaigns, line_type, carrier_risk).
        """
        from jarvis_engine.scam_hunter import (
            create_call_intel_report,
            save_call_intel,
            load_call_intel,
            detect_campaigns,
            save_campaigns,
            lookup_carrier_cached,
        )

        report = create_call_intel_report(
            number=fields["number"],
            stir_status=fields["stir_status"],
            presentation=fields["presentation"],
            duration_sec=fields["duration_sec"],
            answered=fields["answered"],
            contact_name=fields["contact_name"],
        )
        intel_path = _runtime_dir(root) / "call_intel.jsonl"
        save_call_intel(intel_path, report)

        # Check carrier cache
        carrier_cache_path = _runtime_dir(root) / "carrier_cache.json"
        carrier = lookup_carrier_cached(carrier_cache_path, report.normalized)
        line_type = carrier.line_type if carrier else ""
        carrier_risk = carrier.risk_score if carrier else 0.0

        # Run campaign detection on recent data
        all_reports = load_call_intel(intel_path, limit=200)
        campaigns = detect_campaigns(all_reports)
        campaign_path = _runtime_dir(root) / "scam_campaigns.json"
        save_campaigns(campaign_path, campaigns)

        return report, all_reports, campaigns, line_type, carrier_risk

    @staticmethod
    def _compute_scam_score_and_action(
        fields: dict[str, Any],
        all_reports: list[Any],
        campaigns: list[Any],
        line_type: str,
        carrier_risk: float,
    ) -> dict[str, Any]:
        """Compute enhanced spam score and recommended action.

        Returns dict with enhanced_score, action, campaign_id,
        campaign_confidence, and campaign_signals.
        """
        from jarvis_engine.scam_hunter import (
            compute_enhanced_spam_score,
            score_time_of_day,
        )
        from jarvis_engine.phone_guard import _normalize_number, detect_spam_candidates

        normalized = _normalize_number(fields["number"])

        # Find campaign membership
        campaign_id = ""
        campaign_confidence = 0.0
        campaign_signals: list[str] = []
        for campaign in campaigns:
            if normalized in campaign.numbers:
                campaign_id = campaign.campaign_id
                campaign_confidence = campaign.confidence
                campaign_signals = campaign.signals
                break

        # Build base score: phone_guard pattern score + time-of-day
        base_score = score_time_of_day(normalized)
        pg_candidates = detect_spam_candidates(all_reports)
        for c in pg_candidates:
            if c.number == normalized:
                base_score = max(base_score, c.score)
                break

        # Compute enhanced score with ALL signals
        enhanced_score = compute_enhanced_spam_score(
            base_score=base_score,
            stir_status=fields["stir_status"],
            line_type=line_type,
            carrier_risk=carrier_risk,
            campaign_confidence=campaign_confidence,
            presentation=fields["presentation"],
            is_in_contacts=bool(fields["contact_name"]),
            caller_display_name=fields["caller_display_name"],
            gateway_domain=fields["gateway_domain"],
            setup_latency_ms=fields["setup_latency_ms"],
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

        return {
            "enhanced_score": enhanced_score,
            "action": action,
            "campaign_id": campaign_id,
            "campaign_confidence": campaign_confidence,
            "campaign_signals": campaign_signals,
        }

    def _handle_post_scam_report_call(self: _ScamRoutesHandlerProtocol) -> None:
        """Report a screened call with STIR/SHAKEN status for campaign analysis.

        Accepts: {number, stir_status, presentation, duration_sec, answered, contact_name}
        Returns: {ok, campaign_id?, enhanced_score, recommended_action}
        """
        body, _ = self._read_json_body(max_content_length=5_000)
        if body is None:
            return
        try:
            fields = self._parse_scam_report_body(body)
            _, all_reports, campaigns, line_type, carrier_risk = (
                self._save_intel_and_detect_campaigns(self._root, fields)
            )
            result = self._compute_scam_score_and_action(
                fields, all_reports, campaigns, line_type, carrier_risk,
            )

            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "enhanced_score": round(result["enhanced_score"], 4),
                "recommended_action": result["action"],
                "campaign_id": result["campaign_id"],
                "campaign_confidence": round(result["campaign_confidence"], 4),
                "line_type": line_type,
                "stir_status": fields["stir_status"],
                "signals": result["campaign_signals"],
            })
        except (ValueError, KeyError, TypeError, OSError, ImportError, AttributeError) as exc:  # narrowed from except Exception
            logger.warning("Scam report-call failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "enhanced_score": 0.0, "recommended_action": "voicemail", "error": "Scam report processing failed."})

    def _handle_post_scam_lookup(self: _ScamRoutesHandlerProtocol) -> None:
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
        except (ValueError, KeyError, TypeError, OSError, ImportError, AttributeError) as exc:  # narrowed from except Exception
            logger.warning("Scam lookup failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "ok": False, "number": str(body.get("number", "")),
                "carrier": "", "line_type": "", "is_voip": False, "error": "Scam lookup processing failed.",
            })

    def _handle_get_scam_campaigns(self: _ScamRoutesHandlerProtocol) -> None:
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
        except (ValueError, KeyError, TypeError, OSError, ImportError, AttributeError) as exc:  # narrowed from except Exception
            logger.warning("Scam campaigns fetch failed: %s", exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "campaigns": [], "block_actions": []})

    def _handle_get_scam_stats(self: _ScamRoutesHandlerProtocol) -> None:
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
        except (ValueError, KeyError, TypeError, OSError, ImportError, AttributeError) as exc:  # narrowed from except Exception
            logger.warning("Scam stats fetch failed: %s", exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "total_screened": 0, "active_campaigns": 0})
