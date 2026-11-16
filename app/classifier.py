"""Message classifier: picks the KB section, language, and human-intervention flag.

Uses a pydantic-ai agent with the configured OpenRouter model and a structured
``Classification`` output.  Because the KB sections live in the database, the
agent is told the current section names in its system prompt, and the output is
validated against that whitelist so a hallucinated section never reaches the DB.
"""

from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app import db
from app.config import settings
from app.schemas import Classification, ThreadMessage, render_thread

# Sentinel used when the KB contains no sections yet.
FALLBACK_SECTION = "general"


def build_model(model_id: str, api_key: str) -> Any:
    """Construct a pydantic-ai model from a config string.

    The config values look like ``openrouter:openrouter/free``; we strip the
    provider prefix and pass the api key explicitly (the .env is loaded into
    settings but not necessarily exported to os.environ, so relying on the
    default env-var lookup is risky).
    """
    if model_id.startswith("openrouter:"):
        return OpenRouterModel(model_id[len("openrouter:"):], provider=OpenRouterProvider(api_key=api_key))
    return model_id  # non-OpenRouter models can use the plain string form


def _valid_sections() -> frozenset[str]:
    """Return the current KB section names (cached - refreshed per call).

    ``list_sections`` is cheap and the DB may change between runs, so we do a
    plain (uncached) call here; the classifier prompt is built per message.
    """
    try:
        return frozenset(db.list_sections())
    except Exception:
        return frozenset()


_SYSTEM_PROMPT = (
    "You are the triage classifier for a customer-support inbox.\n"
    "Given the full conversation thread, decide:\n"
    "1. section  - the single knowledge-base section that best answers the "
    "customer's latest question ({sections}). Choose exactly one of these names; "
    "if none fits, use general.\n"
    "2. language - the ISO 639-1 code of the CUSTOMER's message (e.g. en, de, fr).\n"
    "3. needs_human - true ONLY if the latest customer message requires a human "
    "(legal threats, abusive language, security breach, refund disputes over a "
    "threshold). Leave false for ordinary product questions.\n"
    "The customer is the inbound message; the human is the support engineer, not "
    "an automated bot.\n"
)

_classifier: Agent | None = None


def _get_classifier() -> Agent:
    """Return a classifier agent bound to the configured model (cached)."""
    global _classifier
    if _classifier is None:
        _classifier = Agent(
            model=build_model(settings.classifier_model, settings.openrouter_api_key),
            system_prompt=_SYSTEM_PROMPT.format(
                sections=", ".join(sorted(_valid_sections())) or FALLBACK_SECTION
            ),
            output_type=Classification,
            retries=2,
        )
    return _classifier


def classify(thread: list[ThreadMessage]) -> Classification:
    """Classify a thread and return a validated Classification.

    The returned ``section`` is guaranteed to be in the KB whitelist (falling
    back to ``general`` when the model or the DB is missing/empty).
    """
    prompt = render_thread(thread)
    result = _get_classifier().run_sync(prompt)
    classification = result.output

    sections = _valid_sections()
    if sections and classification.section not in sections:
        classification.section = FALLBACK_SECTION
    if not sections:
        classification.section = FALLBACK_SECTION

    # Normalise the language: keep only the primary subtag, lowercased.
    primary = classification.language.split("-")[0].strip().lower()
    classification.language = primary or "en"

    return classification


def is_english(language: str) -> bool:
    """True when a primary language code is English."""
    return language.split("-")[0].strip().lower() == "en"
