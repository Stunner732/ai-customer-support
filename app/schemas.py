"""Shared Pydantic models and conversion helpers.

These models are the typed hand-off objects between the pipeline stages:

* ``ThreadMessage``  - one email in a thread (converted from Gmail's raw dicts).
* ``Classification`` - output of the classifier (KB section, language, human flag).
* ``DraftReply``     - output of the drafting agent (reply text + quality flags).
* ``Decision``       - output of the decide step (reply vs. escalate).

Keeping them in one module avoids circular imports between classifier/agent/decide.
"""

from typing import Any, Literal

from pydantic import BaseModel

from app.config import settings


class ThreadMessage(BaseModel):
    """A single message in a support thread, normalised for the LLM."""

    gmail_message_id: str
    sender: str
    direction: Literal["inbound", "outbound"]
    body: str
    has_attachments: bool = False


class Classification(BaseModel):
    """Classifier output: which KB section applies, message language, human flag."""

    section: str = "general"
    language: str = "en"
    needs_human: bool = False


class DraftReply(BaseModel):
    """Drafting agent output: the proposed reply plus the escalation flags."""

    reply: str = ""
    enough_info: bool = True
    promises_action: bool = False


class Decision(BaseModel):
    """Decide-step output that drives the rest of the pipeline."""

    action: Literal["reply", "escalate"]
    reply: str = ""
    escalation_reason: str | None = None


def _direction(raw: dict[str, Any]) -> Literal["inbound", "outbound"]:
    """Derive a message direction from Gmail labels / sender.

    ``SENT`` label is the authoritative outbound marker.  The sender comparison
    is only used as a fallback and is guarded so an empty ``support_email``
    setting never makes every sender look like us.
    """
    if "SENT" in raw.get("label_ids", []):
        return "outbound"
    if settings.support_email:
        sender = raw.get("sender", "").lower()
        if settings.support_email.lower() in sender:
            return "outbound"
    return "inbound"


def to_thread_message(raw: dict[str, Any]) -> ThreadMessage:
    """Convert one Gmail parsed message dict (from gmail.get_thread) to a model."""
    return ThreadMessage(
        gmail_message_id=raw["id"],
        sender=raw.get("sender", ""),
        direction=_direction(raw),
        body=raw.get("body", ""),
        has_attachments=raw.get("has_attachments", False),
    )


def to_thread_messages(raw_thread: list[dict[str, Any]]) -> list[ThreadMessage]:
    """Convert Gmail's parsed thread list (oldest first) into ThreadMessage list.

    Only messages produced by Gmail's parse (already have an ``id``) are kept;
    malformed entries are skipped so one bad message cannot break a whole thread.
    """
    messages: list[ThreadMessage] = []
    for raw in raw_thread:
        try:
            messages.append(to_thread_message(raw))
        except (KeyError, TypeError):
            # Missing an id / not a dict -> not parseable as a thread message.
            continue
    return messages


def render_thread(thread: list[ThreadMessage]) -> str:
    """Render a thread as a compact, ordered transcript for the LLM.

    Inbound messages are tagged CUSTOMER (the person to answer); outbound ones
    (our own previous emails, from the ``SENT`` label / support address) are
    tagged SUPPORT so the model understands the full conversation context.
    """
    lines: list[str] = []
    for msg in thread:
        tag = "CUSTOMER" if msg.direction == "inbound" else "SUPPORT"
        att = " [attachments present]" if msg.has_attachments else ""
        lines.append(f"--- {tag}{att} ---\n{msg.body.strip()}")
    return "\n\n".join(lines)
