"""Golden email test cases for Harbor & Pine customer support evaluation.

Each Case defines a test scenario with inputs (thread messages) and expected
outcomes (action, escalation reason, section, key facts in reply).
"""

from dataclasses import dataclass
from typing import Any

from pydantic_evals import Case, Dataset


@dataclass
class CaseMetadata:
    """Expected outcome metadata for a test case."""

    expected_action: str  # "reply" or "escalate"
    expected_escalation_reason: str | None = None
    expected_section: str | None = None
    expected_facts: list[str] | None = None  # substrings that must appear in reply
    draft_should_run: bool = True  # whether draft_reply should be invoked


def _mk_msg(
    sender: str,
    direction: str,
    body: str,
    has_attachments: bool = False,
) -> dict[str, Any]:
    """Helper to create a message dict for test inputs."""
    return {
        "sender": sender,
        "direction": direction,
        "body": body,
        "has_attachments": has_attachments,
    }


# ---------------------------------------------------------------------------
# 12 Test Cases: 7 reply + 5 escalation
# ---------------------------------------------------------------------------

CASES = [
    # 1. Products: Harbor Insulated Daypack (24L) - capacity & cleaning
    Case(
        name="products_daypack_capacity_cleaning",
        inputs=[
            _mk_msg(
                sender="customer@example.com",
                direction="inbound",
                body=(
                    "Hi, I'm considering the Harbor Insulated Daypack for day hikes. "
                    "How many cans does it hold, and can I put it in the washing machine?"
                ),
            )
        ],
        metadata=CaseMetadata(
            expected_action="reply",
            expected_section="products",
            expected_facts=[
                "24 cans",
                "36 hours",
                "not machine-washable",
                "spot-clean",
            ],
        ),
    ),
    # 2. Products: 10ft Ridge Inflatable Paddle Board - weight limit & PSI
    Case(
        name="products_paddleboard_weight_psi",
        inputs=[
            _mk_msg(
                sender="paddler@example.com",
                direction="inbound",
                body=(
                    "What's the maximum rider weight for the 10ft Ridge paddle board? "
                    "Also, what PSI should I inflate it to?"
                ),
            )
        ],
        metadata=CaseMetadata(
            expected_action="reply",
            expected_section="products",
            expected_facts=[
                "275 lbs",
                "125 kg",
                "15 PSI",
            ],
        ),
    ),
    # 3. Products: Cascade 2-Person Backpacking Tent - specs
    Case(
        name="products_tent_specs",
        inputs=[
            _mk_msg(
                sender="camper@example.com",
                direction="inbound",
                body=(
                    "Can you tell me the trail weight and peak height of the Cascade "
                    "2-Person Backpacking Tent? And is the rainfly waterproof?"
                ),
            )
        ],
        metadata=CaseMetadata(
            expected_action="reply",
            expected_section="products",
            expected_facts=[
                "3 lbs 14 oz",
                "3000mm",
                "42 inches",
            ],
        ),
    ),
    # 4. Shipping: Standard & Express Shipping thresholds
    Case(
        name="shipping_standard_express",
        inputs=[
            _mk_msg(
                sender="shopper@example.com",
                direction="inbound",
                body=(
                    "What's the free shipping threshold? How long does standard "
                    "shipping take, and what does express cost?"
                ),
            )
        ],
        metadata=CaseMetadata(
            expected_action="reply",
            expected_section="shipping",
            expected_facts=[
                "business days",
                "$75",
                "$8.95",
                "$19.95",
            ],
        ),
    ),
    # 5. Returns: 30-Day Return Policy
    Case(
        name="returns_30_day_policy",
        inputs=[
            _mk_msg(
                sender="buyer@example.com",
                direction="inbound",
                body=(
                    "I want to return a daypack I bought 2 weeks ago. It's unused "
                    "with original packaging. How long do I have, and will I get a "
                    "full refund to my card?"
                ),
            )
        ],
        metadata=CaseMetadata(
            expected_action="reply",
            expected_section="returns",
            expected_facts=[
                "30 days",
                "original payment method",
                "$7.95",
            ],
        ),
    ),
    # 6. Warranty: 2-Year Limited Craftsmanship Warranty
    Case(
        name="warranty_2_year",
        inputs=[
            _mk_msg(
                sender="owner@example.com",
                direction="inbound",
                body=(
                    "My Harbor daypack's zipper broke after 18 months. Is this "
                    "covered under warranty? What does the warranty cover exactly?"
                ),
            )
        ],
        metadata=CaseMetadata(
            expected_action="reply",
            expected_section="warranty",
            expected_facts=[
                "2 years",
                "zipper",
            ],
        ),
    ),
    # 7. Multi-turn follow-up: paddle board thread continuation
    Case(
        name="multi_turn_paddleboard_followup",
        inputs=[
            _mk_msg(
                sender="customer@example.com",
                direction="inbound",
                body="What's the weight limit on the Ridge paddle board?",
            ),
            _mk_msg(
                sender="support@harborpine.com",
                direction="outbound",
                body=(
                    "The 10ft Ridge Inflatable Paddle Board has a maximum rider "
                    "weight limit of 275 lbs (125 kg) and inflates to 15 PSI. "
                    "Kind regards,\nThe Support Team"
                ),
            ),
            _mk_msg(
                sender="customer@example.com",
                direction="inbound",
                body=(
                    "Thanks! Does the board come with a pump, and is it the dual-action "
                    "kind? Also, does the package include a paddle and leash?"
                ),
            ),
        ],
        metadata=CaseMetadata(
            expected_action="reply",
            expected_section="products",
            expected_facts=[
                "dual-action pump",
                "adjustable aluminum paddle",
                "ankle leash",
            ],
        ),
    ),
    # 8. Escalation: attachments_present
    Case(
        name="escalation_attachments_present",
        inputs=[
            _mk_msg(
                sender="customer@example.com",
                direction="inbound",
                body="Here's a photo of the damaged zipper on my daypack.",
                has_attachments=True,
            )
        ],
        metadata=CaseMetadata(
            expected_action="escalate",
            expected_escalation_reason="attachments_present",
            expected_section="products",
            draft_should_run=False,
        ),
    ),
    # 9. Escalation: non_english (French)
    Case(
        name="escalation_non_english_french",
        inputs=[
            _mk_msg(
                sender="client@example.fr",
                direction="inbound",
                body=(
                    "Bonjour, je voudrais savoir si le sac à dos Harbor Daypack "
                    "est lavable en machine. Merci."
                ),
            )
        ],
        metadata=CaseMetadata(
            expected_action="escalate",
            expected_escalation_reason="non_english",
            expected_section="products",
            draft_should_run=False,
        ),
    ),
    # 10. Escalation: needs_human (angry/legal threat)
    Case(
        name="escalation_needs_human_legal",
        inputs=[
            _mk_msg(
                sender="angry@example.com",
                direction="inbound",
                body=(
                    "This is unacceptable! My paddle board arrived with a cracked "
                    "valve and you're ignoring my emails. I'm going to file a "
                    "lawsuit if I don't get a full refund immediately!"
                ),
            )
        ],
        metadata=CaseMetadata(
            expected_action="escalate",
            expected_escalation_reason="needs_human",
            expected_section=None,
            draft_should_run=False,
        ),
    ),
    # 11. Escalation: not_enough_info (out-of-scope product question)
    Case(
        name="escalation_not_enough_info_oos",
        inputs=[
            _mk_msg(
                sender="curious@example.com",
                direction="inbound",
                body=(
                    "Do you sell the Ridge SUP in a 12-foot touring version? "
                    "I need something longer for ocean paddling."
                ),
            )
        ],
        metadata=CaseMetadata(
            expected_action="escalate",
            expected_escalation_reason="not_enough_info",
            expected_section="products",
            draft_should_run=True,
        ),
    ),
    # 12. Escalation: promises_an_action (immediate replacement demand)
    Case(
        name="escalation_promises_action_replacement",
        inputs=[
            _mk_msg(
                sender="demanding@example.com",
                direction="inbound",
                body=(
                    "My tent pole snapped on the first trip. I need a replacement "
                    "sent TODAY - I have a trip this weekend. This is urgent!"
                ),
            )
        ],
        metadata=CaseMetadata(
            expected_action="escalate",
            expected_escalation_reason="promises_an_action",
            expected_section=None,
            draft_should_run=True,
        ),
    ),
]


# Build the Dataset
DATASET = Dataset(
    name="harbor_pine_support_evals",
    cases=CASES,
)