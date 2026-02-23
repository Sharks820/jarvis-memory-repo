from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jarvis_engine.phone_guard import build_phone_action, build_spam_block_actions, detect_spam_candidates


def test_detect_spam_candidates_and_global_silence_rule() -> None:
    now = datetime.now(UTC)
    log = []
    for idx in range(5):
        number = f"+14155550{idx:02d}"
        for n in range(4):
            log.append(
                {
                    "number": number,
                    "type": "missed",
                    "duration_sec": 0,
                    "contact_name": "",
                    "ts_utc": (now - timedelta(minutes=n)).isoformat(),
                }
            )

    candidates = detect_spam_candidates(log, now_utc=now)
    assert len(candidates) >= 5
    actions = build_spam_block_actions(candidates, threshold=0.65, add_global_silence_rule=True)
    assert any(a.action == "block_number" for a in actions)
    assert any(a.action == "silence_unknown_callers" for a in actions)


def test_build_phone_action_send_sms_requires_message() -> None:
    action = build_phone_action("send_sms", "(415) 555-1234", "hello")
    assert action.action == "send_sms"
    assert action.number == "+14155551234"
