"""Decide step: turn classifier + draft output into a send-or-escalate decision.

Escalation precedence (first matching rule wins).  ``draft`` is optional so the
pipeline can skip the (expensive) drafting call whenever an escalation is
already guaranteed by the earlier checks.
"""

from app.schemas import Classification, Decision, DraftReply, ThreadMessage

# Sent to the customer when a human must take over, so they know the ticket
# is not silently dropped.  Kept deliberately human and non-committal.
HOLDING_ACK_TEMPLATE = (
    "Thanks for reaching out. Your message has been passed to a member of our "
    "support team, who will get back to you shortly."
)


def decide(
    thread: list[ThreadMessage],
    classification: Classification,
    draft: DraftReply | None = None,
) -> Decision:
    """Choose ``reply`` or ``escalate`` for a thread.

    Parameters
    ----------
    thread : list[ThreadMessage]
        Full conversation, oldest first (the latest message is ``thread[-1]``).
    classification : Classification
        Classifier output (language, needs_human).
    draft : DraftReply | None
        Drafting agent output, or ``None`` when the pipeline skipped drafting
        because an escalation was already guaranteed.
    """
    latest = thread[-1] if thread else None

    # 1. The customer attached files (instructions/orders/etc.) a bot cannot
    #    inspect - always route to a human.
    if latest is not None and latest.has_attachments:
        return Decision(
            action="escalate",
            reply=HOLDING_ACK_TEMPLATE,
            escalation_reason="attachments_present",
        )

    # 2. Non-English mail: we only send curated English replies.
    if classification.language.split("-")[0].strip().lower() != "en":
        return Decision(
            action="escalate",
            reply=HOLDING_ACK_TEMPLATE,
            escalation_reason="non_english",
        )

    # 3. The classifier flagged this as needing a human (abuse, security, etc.).
    if classification.needs_human:
        return Decision(
            action="escalate",
            reply=HOLDING_ACK_TEMPLATE,
            escalation_reason="needs_human",
        )

    # Draft is None only when one of the rules above already escalated, but we
    # guard defensively so a None draft can never send an empty reply.
    if draft is None:
        return Decision(
            action="escalate",
            reply=HOLDING_ACK_TEMPLATE,
            escalation_reason="draft_skipped",
        )

    # 4. The agent could not answer from the knowledge base - hand off.
    if not draft.enough_info:
        return Decision(
            action="escalate",
            reply=HOLDING_ACK_TEMPLATE,
            escalation_reason="not_enough_info",
        )

    # 5. The reply promises a follow-up action a human must confirm.
    if draft.promises_action:
        return Decision(
            action="escalate",
            reply=HOLDING_ACK_TEMPLATE,
            escalation_reason="promises_an_action",
        )

    # 6. Never send an empty or whitespace-only reply.
    if not draft.reply.strip():
        return Decision(
            action="escalate",
            reply=HOLDING_ACK_TEMPLATE,
            escalation_reason="empty_draft",
        )

    # Everything checks out - send the drafted reply.
    return Decision(action="reply", reply=draft.reply)
