"""Pre-reply guards.

Determines whether an incoming parsed message (from
``GmailService.get_message``) should be skipped by the support bot, and if so,
why.  If ``skip_reply`` returns ``None`` the message is safe to process.
"""

from typing import Any

from app.config import settings


def skip_reply(message: dict[str, Any]) -> str | None:
    """Return a short skip-reason string if the bot must *not* reply to this
    message, or ``None`` when it is safe to process.

    ``message`` is a parsed dict from ``GmailService.get_message()``, so its
    ``headers`` dict has lowercase keys, and it carries ``label_ids`` as a list
    of strings and ``sender`` as the raw ``From`` value.
    """
    # 1. Already sent by us — we never reply to our own outbound email.
    if "SENT" in message.get("label_ids", []):
        return "sent_by_us"

    # 2. From our own support address (e.g. a notification loop we generated).
    #    Only enforced when support_email is configured, and matched
    #    case-insensitively on both sides.
    if settings.support_email:
        sender = message.get("sender", "").lower()
        if settings.support_email.lower() in sender:
            return "from_self"

    # ``headers`` always exists from _parse_message (lowercased keys).
    headers: dict[str, str] = message.get("headers", {})

    # 3. Automated/vacation responders put Auto-Submitted on the message;
    #    "no" is the normal human value, anything else is an auto-reply.
    if "auto-submitted" in headers:
        auto = headers["auto-submitted"].strip().lower()
        if auto != "no":
            return "auto_submitted"

    # 4. Bulk/junk/list traffic sets a bulk Precedence header.
    precedence = headers.get("precedence", "").strip().lower()
    if precedence in {"bulk", "junk", "list"}:
        return "bulk_precedence"

    # 5. Any mailing-list header marks this as a list message, not a
    #    one-to-one support conversation.
    if "list-unsubscribe" in headers or "list-id" in headers:
        return "mailing_list"

    # 6. Passed every guard — safe to process.
    return None
