"""Per-message processing pipeline.

handle_message takes a single unprocessed Gmail message ID through the
full one-message pipeline: claim it in the events table, fetch the parsed
message, run the pre-reply guards, classify the thread, draft a reply against
the knowledge base, decide whether to send or escalate, and record every step
in the database.  Every failure is caught and recorded in the events table so a
single malformed email can never crash the poller loop.

The LLM stages (classifier, agent, decide) are invoked here synchronously;
the poller already runs each message inside a thread pool executor, so these
blocking calls do not block the rest of the poller.
"""

import logging

from app import agent, classifier, db, decide
from app.config import settings
from app.gmail import GmailService
from app.guards import skip_reply
from app.schemas import Classification, Decision, DraftReply, ThreadMessage, to_thread_messages

logger = logging.getLogger(__name__)


def _save_thread_history(ticket_id: int, thread: list[ThreadMessage]) -> None:
    """Persist every thread message under the ticket, skipping duplicates.

    save_message is idempotent (UNIQUE gmail_message_id + ON CONFLICT
    DO NOTHING), so re-saving history across runs is safe and cheap.  This
    keeps the DB transcript complete even for messages processed before the
    LLM stages were wired in.
    """
    for m in thread:
        db.save_message(ticket_id, m.gmail_message_id, m.sender, m.direction, m.body)


def _should_basic_escalate(
    thread: list[ThreadMessage], classification
) -> bool:
    """True when we should skip drafting because escalation is already certain.

    Covers the escalate-first checks that don't need the drafting agent:
    attachments, non-English, and the classifier's needs_human flag.  Skipping
    the LLM call here avoids wasting tokens on guaranteed hand-offs.
    """
    latest = thread[-1] if thread else None
    return bool(
        (latest is not None and latest.has_attachments)
        or not classifier.is_english(classification.language)
        or classification.needs_human
    )


def evaluate_thread(
    thread: list[ThreadMessage],
) -> tuple[Classification, DraftReply | None, Decision]:
    """Run the core pipeline on a thread: classify -> (conditional) draft -> decide.

    This is the shared logic used by both the production poller (handle_message)
    and the evaluation harness (evals/run_evals.py).  It mirrors the LLM stages
    exactly as they run in production, including the early-escalation skip.

    Returns a tuple of (classification, draft, decision).
    """
    from app import agent, classifier, decide

    classification = classifier.classify(thread)

    if _should_basic_escalate(thread, classification):
        draft: DraftReply | None = None
    else:
        draft = agent.draft_reply(thread, classification)

    decision = decide.decide(thread, classification, draft)
    return classification, draft, decision


def handle_message(message_id: str, gmail: GmailService) -> None:
    """Process one inbound Gmail message end to end.

    Parameters
    ----------
    message_id : str
        Gmail message id to process (one entry from
        GmailService.list_unprocessed_messages).
    gmail : GmailService
        The shared Gmail API wrapper used for fetching and labeling.
    """
    # 1. Claim this message in the events table (idempotent: ON CONFLICT
    #    DO NOTHING).  A False return means another poller already claimed or
    #    processed it, so there is nothing left to do here.
    if not db.start_event(message_id):
        logger.debug("Message %s already claimed/processed; skipping.", message_id)
        return

    # 2. All real work happens inside a try so that any exception is recorded
    #    as a failure instead of propagating up and killing the poller loop.
    try:
        # 2a. Fetch the full parsed message dict from Gmail.
        msg = gmail.get_message(message_id)

        # 2b. Run the skip guards; a non-None reason means "do not process".
        reason = skip_reply(msg)
        if reason:
            # Guard tripped: record the event as skipped, apply the label so
            # this message is never re-polled, log why, and bail out without
            # storing the message or replying.
            db.finish_event(message_id, status="skipped", skip_reason=reason)
            gmail.add_label(message_id, "AI_PROCESSED")
            logger.info("Message %s skipped: %s", message_id, reason)
            return

        # 2c. Upsert the ticket for this thread, then save this inbound
        #     message under it.  Both are idempotent (UNIQUE gmail ids).
        ticket_id = db.upsert_ticket(
            msg["thread_id"], msg["sender"], msg["subject"]
        )
        db.save_message(
            ticket_id, msg["id"], msg["sender"], "inbound", msg["body"]
        )

        # 2d. Build the full multi-turn thread history (oldest first) for the
        #     LLM context, and persist it so the DB transcript stays complete.
        raw_thread = gmail.get_thread(msg["thread_id"])
        thread = to_thread_messages(raw_thread)
        _save_thread_history(ticket_id, thread)

        # 2e-f. Run the shared pipeline logic.
        classification, draft, decision = evaluate_thread(thread)

        # Record the section on the ticket.
        db.update_ticket(ticket_id, status="open", section=classification.section)
        logger.info(
            "Message %s classified: section=%s language=%s needs_human=%s",
            message_id,
            classification.section,
            classification.language,
            classification.needs_human,
        )

        # 2g. Act on the decision: reply, or escalate with the holding ack.
        if decision.action == "reply":
            db.update_ticket(
                ticket_id, status="answered", section=classification.section
            )
        else:
            db.update_ticket(
                ticket_id,
                status="escalated",
                section=classification.section,
                escalated=True,
                escalation_reason=decision.escalation_reason,
            )
        _send_reply(gmail, message_id, ticket_id, msg, decision.reply)

    except Exception as exc:
        # Log the full traceback and record the failure in the events table,
        # then swallow the exception so the poller keeps running.
        logger.exception("Failed to process message %s", message_id)
        db.fail_event(message_id, str(exc))


def _send_reply(
    gmail: GmailService,
    message_id: str,
    ticket_id: int,
    msg: dict,
    reply_text: str,
) -> None:
    """Send an outbound reply on a thread and record it in the database.

    gmail.send_reply mirrors the message back onto the same Gmail thread,
    the caller's from address is the configured support email.  The Gmail
    API response dict carries the new outbound message under "id", which is
    what we persist so future thread fetches classify our own replies as SUPPORT
    context.  Finally the AI_PROCESSED label is applied so this inbound
    message is never re-polled, and the event is finalized.
    """
    # send_reply(to, subject, body, thread_id, in_reply_to) -> API response dict
    sent = gmail.send_reply(
        to=msg["sender"],
        subject=msg["subject"],
        body=reply_text,
        thread_id=msg["thread_id"],
        in_reply_to=msg["message_id_header"],
    )

    # Persist the outbound message under the ticket for the full transcript.
    db.save_message(
        ticket_id, sent["id"], settings.support_email, "outbound", reply_text
    )

    # Mark the event processed and apply the label so the inbound message is
    # never polled again.  These run last: a crash before them leaves the event
    # unfinished so the message is retried (idempotent, thanks to start_event).
    gmail.add_label(message_id, "AI_PROCESSED")
    db.finish_event(message_id, status="processed")