"""Evaluation runner for Harbor & Pine support pipeline.

Usage:
    uv run python -m evals.run_evals

Requires:
    - Postgres running with KB seeded (run scripts/init_db.py)
    - OPENROUTER_API_KEY set in .env or environment
"""

import argparse
import sys

from app.config import settings
from app.pipeline import evaluate_thread
from app.schemas import Classification, Decision, DraftReply, ThreadMessage
from evals.golden_emails import DATASET, CaseMetadata
from pydantic_evals import Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------


class ActionMatch(Evaluator):
    """Check that decision.action matches expected, and escalation_reason if applicable."""

    def evaluate(
        self, ctx: EvaluatorContext[list[dict], tuple[Classification, DraftReply | None, Decision], CaseMetadata]
    ) -> bool:
        meta = ctx.metadata
        classification, draft, decision = ctx.output

        if decision.action != meta.expected_action:
            return False

        if meta.expected_escalation_reason is not None:
            if decision.escalation_reason != meta.expected_escalation_reason:
                return False

        return True


class SkipLogicMatch(Evaluator):
    """Verify early-escalation skip logic matches expectations.

    For attachments_present, non_english, needs_human -> draft should be None.
    For not_enough_info, promises_an_action -> draft should NOT be None.
    """

    def evaluate(
        self, ctx: EvaluatorContext[list[dict], tuple[Classification, DraftReply | None, Decision], CaseMetadata]
    ) -> bool:
        meta = ctx.metadata
        _, draft, _ = ctx.output

        draft_ran = draft is not None

        # Early-escalation cases should skip drafting
        if not meta.draft_should_run:
            return not draft_ran

        # Cases that need drafting to detect escalation
        return draft_ran


class ReplyGrounded(Evaluator):
    """For reply cases, check that all expected_facts appear in the reply (case-insensitive)."""

    def evaluate(
        self, ctx: EvaluatorContext[list[dict], tuple[Classification, DraftReply | None, Decision], CaseMetadata]
    ) -> bool:
        meta = ctx.metadata
        _, _, decision = ctx.output

        if meta.expected_action != "reply":
            return True  # N/A for escalation cases

        if not meta.expected_facts:
            return True

        reply_text = decision.reply.lower()
        for fact in meta.expected_facts:
            if fact.lower() not in reply_text:
                return False
        return True


class SectionMatch(Evaluator):
    """Check that classification.section matches expected_section (when provided)."""

    def evaluate(
        self, ctx: EvaluatorContext[list[dict], tuple[Classification, DraftReply | None, Decision], CaseMetadata]
    ) -> bool:
        meta = ctx.metadata
        classification, _, _ = ctx.output

        if meta.expected_section is not None:
            return classification.section == meta.expected_section
        return True


# ---------------------------------------------------------------------------
# Task function
# ---------------------------------------------------------------------------


def build_thread(messages: list[dict]) -> list[ThreadMessage]:
    """Convert list of message dicts to ThreadMessage objects."""
    thread = []
    for i, m in enumerate(messages):
        thread.append(
            ThreadMessage(
                gmail_message_id=f"eval-msg-{i}",
                sender=m["sender"],
                direction=m["direction"],
                body=m["body"],
                has_attachments=m.get("has_attachments", False),
            )
        )
    return thread


def run_pipeline(inputs: list[dict]) -> tuple[Classification, DraftReply | None, Decision]:
    """Task function: run the full pipeline on a thread."""
    thread = build_thread(inputs)
    return evaluate_thread(thread)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Harbor & Pine support pipeline evaluation.")
    parser.add_argument(
        "--case",
        help="Only run cases whose name contains this substring (e.g. shipping_standard_express).",
    )
    args = parser.parse_args()

    # Check for API key
    if not settings.openrouter_api_key:
        print("WARNING: OPENROUTER_API_KEY not set. Skipping evaluation.")
        print("   Set OPENROUTER_API_KEY in .env or environment to run evals.")
        return 0

    dataset = DATASET
    if args.case:
        matched = [c for c in DATASET.cases if args.case in (c.name or "")]
        if not matched:
            print(f"No cases match substring {args.case!r}.")
            return 0
        print(f"Filtering to {len(matched)} case(s) matching {args.case!r}")
        dataset = Dataset(name=DATASET.name, cases=matched)

    print(f"Running evaluation: {dataset.name}")
    print(f"Cases: {len(dataset.cases)}")
    print("-" * 60)

    # Add evaluators to the dataset
    dataset.evaluators = [
        ActionMatch(),
        SkipLogicMatch(),
        ReplyGrounded(),
        SectionMatch(),
    ]

    # Run evaluation synchronously
    report = dataset.evaluate_sync(
        run_pipeline,
        name="harbor_pine_pipeline_eval",
        max_concurrency=1,  # Sequential for stability
        progress=True,
    )

    # Print summary table
    report.print()

    # Additional per-case breakdown
    print("\nPer-case detail:")
    print("-" * 60)
    passed = 0
    total = len(report.cases)

    for rc in report.cases:
        case_name = rc.name
        assertions = rc.assertions
        all_passed = all(res.value for _, res in assertions.items())
        status = "PASS" if all_passed else "FAIL"
        if all_passed:
            passed += 1
        print(f"  [{status}]  {case_name}")
        for name, res in assertions.items():
            detail = "OK" if res.value else "FAIL"
            print(f"    {detail} {name}: {res.value}")

    print("-" * 60)
    print(f"Overall: {passed}/{total} cases passed ({100 * passed / total:.1f}%)")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())