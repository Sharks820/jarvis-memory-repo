"""CLI command handlers subpackage.

Re-exports all public command functions for backward compatibility.
Modules were moved from jarvis_engine/cli_*.py into this subpackage.
"""

from jarvis_engine.cli.knowledge import (  # noqa: F401
    cmd_brain_status,
    cmd_brain_context,
    cmd_brain_compact,
    cmd_brain_regression,
    cmd_knowledge_status,
    cmd_contradiction_list,
    cmd_contradiction_resolve,
    cmd_fact_lock,
    cmd_knowledge_regression,
    cmd_consolidate,
    cmd_harvest,
    cmd_ingest_session,
    cmd_harvest_budget,
    cmd_learn,
    cmd_cross_branch_query,
    cmd_flag_expired,
)

from jarvis_engine.cli.ops import (  # noqa: F401
    cmd_ops_brief,
    cmd_ops_export_actions,
    cmd_ops_sync,
    cmd_ops_autopilot,
    cmd_automation_run,
    cmd_mission_create,
    cmd_mission_status,
    cmd_mission_cancel,
    cmd_mission_run,
    cmd_growth_eval,
    cmd_growth_report,
    cmd_growth_audit,
    cmd_intelligence_dashboard,
)

from jarvis_engine.cli.proactive import (  # noqa: F401
    cmd_proactive_check,
    cmd_cost_reduction,
    cmd_self_test,
)

from jarvis_engine.cli.security import (  # noqa: F401
    cmd_owner_guard,
    cmd_connect_status,
    cmd_connect_grant,
    cmd_connect_bootstrap,
    cmd_phone_action,
    cmd_phone_spam_guard,
)

from jarvis_engine.cli.system import (  # noqa: F401
    cmd_gaming_mode,
    cmd_status,
    cmd_log,
    cmd_ingest,
    cmd_serve_mobile,
    cmd_desktop_widget,
    cmd_runtime_control,
    cmd_persona_config,
    cmd_memory_snapshot,
    cmd_memory_maintenance,
    cmd_migrate_memory,
    cmd_mobile_desktop_sync,
    cmd_self_heal,
    cmd_memory_eval,
    cmd_weather,
    cmd_open_web,
    cmd_daemon_run,
)

from jarvis_engine.cli.tasks import (  # noqa: F401
    cmd_route,
    cmd_run_task,
    cmd_web_research,
)

from jarvis_engine.cli.voice import (  # noqa: F401
    cmd_voice_list,
    cmd_voice_say,
    cmd_voice_enroll,
    cmd_voice_verify,
    cmd_voice_listen,
    cmd_voice_run,
    cmd_wake_word,
)
