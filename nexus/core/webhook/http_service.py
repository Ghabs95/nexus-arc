"""HTTP-level webhook request processing extracted from webhook_server."""

from typing import Any


def _infer_event_type_from_payload(payload: dict[str, Any]) -> str | None:
    """Best-effort event type inference when GitHub event header is missing."""
    if not isinstance(payload, dict) or not payload:
        return None

    if "comment" in payload and "issue" in payload:
        return "issue_comment"
    if "pull_request" in payload and "review" in payload:
        return "pull_request_review"
    if "pull_request" in payload:
        return "pull_request"
    if "issue" in payload:
        return "issues"
    if "zen" in payload or "hook_id" in payload:
        return "ping"
    return None


def process_webhook_request(
    *,
    payload_body: bytes,
    headers: dict[str, Any],
    payload_json: dict[str, Any] | None,
    logger,
    verify_signature,
    get_webhook_policy,
    handle_issue_opened,
    handle_issue_comment,
    handle_pull_request,
    handle_pull_request_review,
    emit_alert,
) -> tuple[dict[str, Any], int]:
    """Process one webhook request and return JSON payload + status code."""
    signature = headers.get("X-Hub-Signature-256")
    gitlab_token = headers.get("X-Gitlab-Token")
    if not verify_signature(payload_body, signature, gitlab_token):
        logger.error("❌ Webhook signature verification failed")
        return {"error": "Invalid signature"}, 403

    event_type = headers.get("X-GitHub-Event")
    if not event_type:
        event_type = headers.get("X-Gitlab-Event")
    if not event_type:
        inferred_event_type = _infer_event_type_from_payload(payload_json or {})
        if inferred_event_type:
            logger.warning(
                "⚠️ Missing X-GitHub-Event/X-Gitlab-Event header; inferred event type: %s",
                inferred_event_type,
            )
            event_type = inferred_event_type
        else:
            logger.error("❌ No Git Event header and could not infer event type")
            return {"error": "No event type"}, 400

    payload = payload_json or {}
    delivery_id = headers.get("X-GitHub-Delivery") or headers.get("X-Gitlab-Event-Uuid")
    logger.info("📨 Webhook received: %s (delivery: %s)", event_type, delivery_id)

    try:
        policy = get_webhook_policy()
        dispatched = policy.dispatch_event(event_type, payload)
        route = dispatched.get("route")
        event = dispatched.get("event", {})

        if route == "issues":
            result = handle_issue_opened(payload, event)
        elif route == "issue_comment":
            result = handle_issue_comment(payload, event)
        elif route == "pull_request":
            result = handle_pull_request(payload, event)
        elif route == "pull_request_review":
            result = handle_pull_request_review(payload, event)
        elif route == "ping":
            logger.info("🏓 Ping received")
            result = {"status": "pong"}
        else:
            logger.info("⏭️ Unhandled event type: %s", event_type)
            result = {"status": "unhandled", "event_type": event_type}
        return result, 200
    except Exception as exc:
        logger.error("❌ Error processing webhook: %s", exc, exc_info=True)
        emit_alert(f"Webhook Error: {str(exc)}", severity="error", source="webhook_server")
        return {"error": str(exc)}, 500
