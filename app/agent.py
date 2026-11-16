"""Drafting agent: writes a human-style reply grounded in the knowledge base.

The agent uses pydantic-ai and is given two function tools backed by the DB:

* ``list_sections``    - returns the current knowledge-base section names.
* ``get_section_docs`` - returns the docs (id, title, content) for one section.

The agent is expected to call ``list_sections`` first, then ``get_section_docs``
for the section the classifier suggested, and answer grounded in that content.
The structured output is a ``DraftReply`` (reply text + quality flags) so the
decide step can escalate when the agent says it lacked enough info.
"""

from typing import Optional

from pydantic_ai import Agent

from app import db
from app.classifier import build_model
from app.config import settings
from app.schemas import (
    Classification,
    DraftReply,
    ThreadMessage,
    render_thread,
)

_agent: Optional[Agent] = None

_SYSTEM_PROMPT = (
    "You write replies for a customer-support team. You have two tools:\n"
    "1. list_sections() - lists knowledge-base section names.\n"
    "2. get_section_docs(section) - returns the docs in a section.\n"
    "Always call list_sections() first, then get_section_docs() for the "
    "relevant section, and ground your answer strictly in that content. Never "
    "invent policies or data that are not in the docs.\n"
    "The latest CUSTOMER message must be answered; earlier SUPPORT/CUSTOMER "
    "messages are context so you do not repeat previous answers.\n"
    "Flags:\n"
    "- enough_info: true whenever the retrieved knowledge-base docs contain the "
    "facts needed to answer the customer's question; false ONLY when the docs are "
    "missing the required facts. If the customer asks about a product, size, or "
    "variant not mentioned in the KB docs, set enough_info: false and do not "
    "guess whether it exists.\n"
    "- promises_action: true whenever the customer is requesting a refund, "
    "replacement, exchange, or callback, OR your reply mentions a human "
    "specialist reviewing/issuing one (a human must take over). Do not set it "
    "when merely explaining standard policy fees.\n"
    "Keep the reply concise (2-5 sentences) and in plain text, signed with the "
    "support team name, e.g. 'Kind regards,\nThe Support Team'."
)


def _get_agent() -> Agent:
    """Return the drafting agent, created lazily on first use (cached)."""
    global _agent
    if _agent is not None:
        return _agent

    agent = Agent(
        model=build_model(settings.agent_model, settings.openrouter_api_key),
        system_prompt=_SYSTEM_PROMPT,
        output_type=DraftReply,
        retries=2,
    )

    @agent.tool_plain
    def list_sections() -> str:
        """List the names of the knowledge-base sections, one per line."""
        return "\n".join(db.list_sections()) or "(knowledge base is empty)"

    @agent.tool_plain
    def get_section_docs(section: str) -> str:
        """Return (title, content) for every doc in the given section."""
        docs = db.get_section_docs(section)
        if not docs:
            return "(no docs in this section)"
        blocks = [
            f"## {doc['title']}\n{doc['content']}"
            for doc in docs
        ]
        return "\n\n".join(blocks)

    _agent = agent
    return _agent


def draft_reply(
    thread: list[ThreadMessage], classification: Classification
) -> DraftReply:
    """Draft a reply for the thread given the classifier's chosen section.

    Returns a ``DraftReply``; the pipeline decides whether to actually send it.
    """
    # The classifier's section hint helps the agent pick the right tool fast.
    hint = (
        f"Suggested section (verify with get_section_docs): "
        f"{classification.section}\n"
    )
    prompt = (
        f"{hint}\n"
        f"Conversation:\n{render_thread(thread)}\n\n"
        "Write the reply to the CUSTOMER now."
    )
    result = _get_agent().run_sync(prompt)
    return result.output
