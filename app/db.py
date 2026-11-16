"""Database access layer for the AI customer support bot.

All queries are parameterized with %s placeholders. Each function opens its
own connection (matching scripts/init_db.py's pattern) and commits on success.
"""

import psycopg
from psycopg.rows import dict_row

from app.config import settings


def get_conn() -> psycopg.Connection:
    """Open a new connection using the configured database URL.

    dict_row makes each fetched row a plain dict keyed by column name.
    """
    return psycopg.connect(settings.database_url, row_factory=dict_row)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
def start_event(gmail_message_id: str) -> bool:
    """Try to claim a gmail message for processing.

    ON CONFLICT ... DO NOTHING leaves existing rows untouched and makes the
    INSERT return no row, so we can tell whether this message is new.
    """
    sql = """
        INSERT INTO events (gmail_message_id)
        VALUES (%s)
        ON CONFLICT (gmail_message_id) DO NOTHING
        RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (gmail_message_id,))
            claimed = cur.fetchone() is not None
        conn.commit()
    return claimed


def finish_event(
    gmail_message_id: str,
    status: str = "processed",
    skip_reason: str | None = None,
) -> None:
    """Mark an event as finished (processed or skipped) with a reason."""
    sql = """
        UPDATE events
        SET status = %s,
            skip_reason = %s,
            updated_at = NOW()
        WHERE gmail_message_id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (status, skip_reason, gmail_message_id))
        conn.commit()


def fail_event(gmail_message_id: str, error: str) -> None:
    """Mark an event as failed and record the error message."""
    sql = """
        UPDATE events
        SET status = 'failed',
            error = %s,
            updated_at = NOW()
        WHERE gmail_message_id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (error, gmail_message_id))
        conn.commit()


def list_events(limit: int = 50) -> list[dict]:
    """Return the most recent events, newest first."""
    sql = """
        SELECT *
        FROM events
        ORDER BY created_at DESC
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            return cur.fetchall()

# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
def upsert_ticket(
    gmail_thread_id: str, customer_email: str, subject: str
) -> int:
    """Insert a ticket, or touch updated_at if the thread already exists.

    Returns the ticket id in either case.
    """
    sql = """
        INSERT INTO tickets (gmail_thread_id, customer_email, subject)
        VALUES (%s, %s, %s)
        ON CONFLICT (gmail_thread_id) DO UPDATE
            SET updated_at = NOW()
        RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (gmail_thread_id, customer_email, subject))
            row = cur.fetchone()
            ticket_id = row["id"]
        conn.commit()
    return ticket_id


def update_ticket(
    ticket_id: int,
    status: str,
    section: str | None = None,
    escalated: bool = False,
    escalation_reason: str | None = None,
) -> None:
    """Update ticket fields and bump updated_at."""
    sql = """
        UPDATE tickets
        SET status = %s,
            section = %s,
            escalated = %s,
            escalation_reason = %s,
            updated_at = NOW()
        WHERE id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (status, section, escalated, escalation_reason, ticket_id),
            )
        conn.commit()


def get_ticket(ticket_id: int) -> dict | None:
    """Fetch a single ticket by id, or None if it does not exist."""
    sql = """
        SELECT *
        FROM tickets
        WHERE id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ticket_id,))
            return cur.fetchone()


def list_tickets(status: str | None = None, limit: int = 50) -> list[dict]:
    """Return the most recently touched tickets, newest first.

    When *status* is given, only tickets with that status are returned.
    """
    if status is None:
        sql = """
            SELECT *
            FROM tickets
            ORDER BY updated_at DESC
            LIMIT %s
        """
        params: tuple = (limit,)
    else:
        sql = """
            SELECT *
            FROM tickets
            WHERE status = %s
            ORDER BY updated_at DESC
            LIMIT %s
        """
        params = (status, limit)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def ticket_counts() -> dict[str, int]:
    """Return ticket counts for every dashboard status (zeros included)."""
    sql = """
        SELECT status, count(*) AS count
        FROM tickets
        GROUP BY status
    """
    counts = {key: 0 for key in ("all", "escalated", "open", "answered", "resolved")}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            for row in cur.fetchall():
                counts[row["status"]] = row["count"]
    counts["all"] = sum(
        counts[key] for key in ("escalated", "open", "answered", "resolved")
    )
    return counts


def get_ticket_with_messages(ticket_id: int) -> dict | None:
    """Fetch a ticket dict with its messages attached in chronological order.

    Returns None if no ticket matches the id.
    """
    ticket_sql = """
        SELECT *
        FROM tickets
        WHERE id = %s
    """
    messages_sql = """
        SELECT *
        FROM messages
        WHERE ticket_id = %s
        ORDER BY created_at ASC
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ticket_sql, (ticket_id,))
            ticket = cur.fetchone()
            if ticket is None:
                return None
            cur.execute(messages_sql, (ticket_id,))
            ticket["messages"] = cur.fetchall()
    return ticket


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
def get_ticket_messages(ticket_id: int) -> list[dict]:
    """Return every message on a ticket, oldest first."""
    sql = """
        SELECT *
        FROM messages
        WHERE ticket_id = %s
        ORDER BY created_at ASC
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ticket_id,))
            return cur.fetchall()


def save_message(
    ticket_id: int,
    gmail_message_id: str,
    sender: str,
    direction: str,
    body: str,
) -> None:
    """Insert a message, skipping it if the gmail message id is already saved."""
    sql = """
        INSERT INTO messages (ticket_id, gmail_message_id, sender, direction, body)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (gmail_message_id) DO NOTHING
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql, (ticket_id, gmail_message_id, sender, direction, body)
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Knowledge Base
# ---------------------------------------------------------------------------
def list_sections() -> list[str]:
    """Return sorted distinct KB section names."""
    sql = """
        SELECT DISTINCT section
        FROM knowledge_base
        ORDER BY section
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            # cur.fetchall() returns dicts; pull the section value out.
            return [row["section"] for row in cur.fetchall()]


def get_section_docs(section: str) -> list[dict]:
    """Return id, title, content for every doc in a section."""
    sql = """
        SELECT id, title, content
        FROM knowledge_base
        WHERE section = %s
        ORDER BY title
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (section,))
            return cur.fetchall()


def list_all_kb() -> list[dict]:
    """Return all KB rows ordered by section, then title."""
    sql = """
        SELECT *
        FROM knowledge_base
        ORDER BY section, title
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


def get_kb_doc(kb_id: int) -> dict | None:
    """Fetch a single KB row by id, or None if it does not exist."""
    sql = """
        SELECT *
        FROM knowledge_base
        WHERE id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (kb_id,))
            return cur.fetchone()


def create_kb_doc(section: str, title: str, content: str) -> int:
    """Insert a KB doc; on (section, title) conflict return the existing id."""
    sql = """
        INSERT INTO knowledge_base (section, title, content)
        VALUES (%s, %s, %s)
        ON CONFLICT (section, title) DO NOTHING
        RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (section, title, content))
            row = cur.fetchone()
            if row is not None:
                kb_id = row["id"]
            else:
                cur.execute(
                    """
                    SELECT id
                    FROM knowledge_base
                    WHERE section = %s AND title = %s
                    """,
                    (section, title),
                )
                kb_id = cur.fetchone()["id"]
        conn.commit()
    return kb_id


def update_kb_doc(
    kb_id: int,
    section: str | None = None,
    title: str | None = None,
    content: str | None = None,
) -> None:
    """Update provided KB fields and bump updated_at."""
    assignments: list[str] = []
    params: list = []
    if section is not None:
        assignments.append("section = %s")
        params.append(section)
    if title is not None:
        assignments.append("title = %s")
        params.append(title)
    if content is not None:
        assignments.append("content = %s")
        params.append(content)
    if not assignments:
        return
    assignments.append("updated_at = NOW()")
    params.append(kb_id)
    sql = f"""
        UPDATE knowledge_base
        SET {', '.join(assignments)}
        WHERE id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()


def delete_kb_doc(kb_id: int) -> None:
    """Delete a KB row by id."""
    sql = """
        DELETE FROM knowledge_base
        WHERE id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (kb_id,))
        conn.commit()


def update_kb_entry(kb_id: int, content: str) -> None:
    """Replace a KB entry's content and bump updated_at."""
    sql = """
        UPDATE knowledge_base
        SET content = %s,
            updated_at = NOW()
        WHERE id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (content, kb_id))
        conn.commit()