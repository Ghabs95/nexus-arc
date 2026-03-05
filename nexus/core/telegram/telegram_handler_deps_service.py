from functools import partial

from nexus.core.handlers.audio_transcription_handler import AudioTranscriptionDeps
from nexus.core.handlers.callback_command_handlers import CallbackHandlerDeps
from nexus.core.handlers.feature_ideation_handlers import FeatureIdeationHandlerDeps
from nexus.core.handlers.hands_free_routing_handler import HandsFreeRoutingDeps
from nexus.core.handlers.issue_command_handlers import IssueHandlerDeps
from nexus.core.handlers.monitoring_command_handlers import MonitoringHandlersDeps
from nexus.core.handlers.ops_command_handlers import OpsHandlerDeps
from nexus.core.handlers.visualize_command_handlers import VisualizeHandlerDeps
from nexus.core.handlers.watch_command_handlers import WatchHandlerDeps
from nexus.core.handlers.workflow_command_handlers import WorkflowHandlerDeps


def build_workflow_handler_deps(**kwargs) -> WorkflowHandlerDeps:
    return WorkflowHandlerDeps(**kwargs)


def build_monitoring_handler_deps(**kwargs) -> MonitoringHandlersDeps:
    return MonitoringHandlersDeps(**kwargs)


def build_ops_handler_deps(**kwargs) -> OpsHandlerDeps:
    return OpsHandlerDeps(**kwargs)


def build_callback_handler_deps(*, report_bug_action, **kwargs) -> CallbackHandlerDeps:
    return CallbackHandlerDeps(report_bug_action=report_bug_action, **kwargs)


def build_callback_action_handlers(
    *,
    ctx_call_telegram_handler,
    logs_handler,
    logsfull_handler,
    status_handler,
    pause_handler,
    resume_handler,
    stop_handler,
    audit_handler,
    active_handler,
    reprocess_handler,
    plan_handler,
):
    return {
        "logs": partial(ctx_call_telegram_handler, handler=logs_handler),
        "logsfull": partial(ctx_call_telegram_handler, handler=logsfull_handler),
        "status": partial(ctx_call_telegram_handler, handler=status_handler),
        "pause": partial(ctx_call_telegram_handler, handler=pause_handler),
        "resume": partial(ctx_call_telegram_handler, handler=resume_handler),
        "stop": partial(ctx_call_telegram_handler, handler=stop_handler),
        "audit": partial(ctx_call_telegram_handler, handler=audit_handler),
        "active": partial(ctx_call_telegram_handler, handler=active_handler),
        "reprocess": partial(ctx_call_telegram_handler, handler=reprocess_handler),
        "plan": partial(ctx_call_telegram_handler, handler=plan_handler),
    }


def build_visualize_handler_deps(**kwargs) -> VisualizeHandlerDeps:
    return VisualizeHandlerDeps(**kwargs)


def build_watch_handler_deps(**kwargs) -> WatchHandlerDeps:
    return WatchHandlerDeps(**kwargs)


def build_issue_handler_deps(**kwargs) -> IssueHandlerDeps:
    return IssueHandlerDeps(**kwargs)


def build_feature_ideation_handler_deps(
    *,
    logger,
    allowed_user_ids,
    projects,
    get_project_label,
    orchestrator,
    base_dir,
    project_config,
    process_inbox_task,
    requester_context_builder=None,
    authorize_project=None,
    feature_registry_service=None,
    dedup_similarity=0.86,
):
    async def _create_feature_task(
        text: str,
        message_id: str,
        project_key: str,
        user_id: str | None = None,
    ):
        requester_context = None
        if callable(requester_context_builder) and user_id:
            try:
                requester_context = requester_context_builder(int(str(user_id)))
            except Exception:
                requester_context = None
        return await process_inbox_task(
            text,
            orchestrator,
            message_id,
            project_hint=project_key,
            requester_context=requester_context,
            authorize_project=authorize_project,
        )

    return FeatureIdeationHandlerDeps(
        logger=logger,
        allowed_user_ids=allowed_user_ids,
        projects=projects,
        get_project_label=get_project_label,
        orchestrator=orchestrator,
        base_dir=base_dir,
        project_config=project_config,
        create_feature_task=_create_feature_task,
        feature_registry_service=feature_registry_service,
        dedup_similarity=dedup_similarity,
    )


def build_audio_transcription_handler_deps(*, logger, transcribe_audio) -> AudioTranscriptionDeps:
    return AudioTranscriptionDeps(
        logger=logger,
        transcribe_audio=transcribe_audio,
    )


def build_hands_free_routing_handler_deps(**kwargs) -> HandsFreeRoutingDeps:
    return HandsFreeRoutingDeps(**kwargs)
