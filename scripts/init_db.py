import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    gmail_message_id TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing',
    skip_reason TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tickets (
    id SERIAL PRIMARY KEY,
    gmail_thread_id TEXT UNIQUE NOT NULL,
    customer_email TEXT NOT NULL,
    subject TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    section TEXT,
    escalated BOOLEAN NOT NULL DEFAULT FALSE,
    escalation_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    gmail_message_id TEXT UNIQUE NOT NULL,
    sender TEXT NOT NULL,
    direction TEXT NOT NULL, -- 'inbound' or 'outbound'
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS knowledge_base (
    id SERIAL PRIMARY KEY,
    section TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(section, title)
);
"""

KB_ENTRIES = [
    (
        "products",
        "10ft Ridge Inflatable Paddle Board",
        "Maximum rider weight limit is 275 lbs (125 kg). The board has a "
        "6-inch drop-stitch PVC core and inflates to 15 PSI. It includes an "
        "adjustable aluminum paddle, dual-action pump, ankle leash, and "
        "carry backpack.",
    ),
    (
        "products",
        "Cascade 2-Person Backpacking Tent",
        "Trail weight is 3 lbs 14 oz. The tent has a 3000mm waterproof "
        "rainfly, a 3-season rating, and an interior peak height of "
        "42 inches.",
    ),
    (
        "products",
        "Harbor Insulated Daypack (24L)",
        "Holds up to 24 cans plus ice for up to 36 hours and has a leakproof "
        "TPU liner. It is not machine-washable; spot-clean with mild soap "
        "and air dry.",
    ),
    (
        "shipping",
        "Standard & Express Shipping",
        "Standard shipping is free on orders over $75 and takes 4–6 business "
        "days; orders under $75 have an $8.95 flat rate. Express shipping "
        "takes 2 business days and costs $19.95. Orders placed before "
        "2:00 PM EST ship the same business day.",
    ),
    (
        "shipping",
        "International Shipping",
        "We currently ship only to the continental US, Alaska, Hawaii, and "
        "Canada. Canadian orders take 6–10 business days, and customs "
        "duties are paid at checkout.",
    ),
    (
        "returns",
        "30-Day Return Policy",
        "Unused items in their original packaging may be returned within "
        "30 days of delivery for a full refund to the original payment "
        "method. Return shipping is free for defective items; a $7.95 "
        "return label fee is deducted for standard preference returns.",
    ),
    (
        "returns",
        "Damaged or Defective Items",
        "Customers reporting damaged or defective gear must provide their "
        "order number and photos of the issue so a human specialist can "
        "review the case and issue a replacement or full refund.",
    ),
    (
        "warranty",
        "2-Year Limited Craftsmanship Warranty",
        "With proof of purchase, this warranty covers manufacturing defects "
        "in seams, valves, zippers, and poles for 2 years from the purchase "
        "date. It does not cover normal wear and tear, accidental punctures, "
        "or UV fading.",
    ),
]

if __name__ == "__main__":
    db_url = os.environ["DATABASE_URL"]
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            seeded_count = 0
            for entry in KB_ENTRIES:
                cur.execute(
                    """
                    INSERT INTO knowledge_base (section, title, content)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (section, title) DO NOTHING
                    """,
                    entry,
                )
                seeded_count += cur.rowcount
        conn.commit()
    print(f"Database tables initialized; seeded {seeded_count} KB entries.")
