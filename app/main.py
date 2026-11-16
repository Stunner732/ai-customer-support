"""FastAPI dashboard for the AI customer support bot.

Serves a server-rendered dashboard (ticket queue, ticket detail with human
reply/resolve actions, and KB CRUD) and runs the Gmail poller loop in a
background daemon thread for the lifetime of the process.
"""

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import db, poller
from app.config import settings
from app.gmail import GmailService

logger = logging.getLogger(__name__)

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)

STATUSES = ["escalated", "open", "answered", "resolved"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The poller is fully blocking (sync Gmail client + time.sleep), so it
    # runs on a daemon thread: an asyncio task would block the event loop.
    # The thread is resilient (see app.poller.main) and dies with the process.
    thread = threading.Thread(target=poller.main, daemon=True)
    thread.start()
    yield


app = FastAPI(title="AI Customer Support", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def dashboard(request: Request, status: str | None = None):
    """Ticket dashboard with optional status filter and summary counts."""
    tickets = db.list_tickets(status=status) if status else db.list_tickets()
    counts = db.ticket_counts()
    return templates.TemplateResponse(
        request,
        "tickets.html",
        {
            "tickets": tickets,
            "counts": counts,
            "statuses": STATUSES,
            "selected": status,
        },
    )


@app.get("/tickets/{ticket_id}")
def ticket_detail(request: Request, ticket_id: int):
    """Ticket detail page: transcript, reply form, resolve button."""
    ticket = db.get_ticket_with_messages(ticket_id)
    if ticket is None:
        return templates.TemplateResponse(
            request,
            "ticket_detail.html",
            {"ticket": None, "statuses": STATUSES},
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "ticket_detail.html",
        {"ticket": ticket, "statuses": STATUSES},
    )


@app.post("/tickets/{ticket_id}/reply")
def ticket_reply(ticket_id: int, body: str = Form(...)):
    """Send a human reply on the ticket's Gmail thread and record it."""
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        return RedirectResponse("/", status_code=303)
    try:
        gmail = GmailService()
        sent = gmail.send_reply(
            to=ticket["customer_email"],
            subject=ticket["subject"] or "",
            body=body,
            thread_id=ticket["gmail_thread_id"],
            # messages.gmail_message_id holds the Gmail API ID (not the
            # RFC Message-ID header), so there is nothing valid to put in
            # In-Reply-To; Gmail threads the reply via thread_id instead.
            in_reply_to=None,
        )
        db.save_message(
            ticket_id, sent["id"], settings.support_email, "outbound", body
        )
        if ticket["status"] != "resolved":
            db.update_ticket(ticket_id, status="answered")
    except Exception:
        logger.exception("Failed to send human reply on ticket %s", ticket_id)
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@app.post("/tickets/{ticket_id}/resolve")
def ticket_resolve(ticket_id: int):
    """Mark a ticket as resolved."""
    db.update_ticket(ticket_id, status="resolved")
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@app.get("/kb")
def kb_index(request: Request):
    """Knowledge base listing."""
    return templates.TemplateResponse(
        request, "kb.html", {"docs": db.list_all_kb()}
    )


@app.post("/kb")
def kb_create(section: str = Form(...), title: str = Form(...), content: str = Form(...)):
    """Create a KB doc; existing (section, title) pairs keep their row."""
    db.create_kb_doc(section, title, content)
    return RedirectResponse("/kb", status_code=303)


@app.post("/kb/{kb_id}/update")
def kb_update(
    kb_id: int,
    section: str = Form(...),
    title: str = Form(...),
    content: str = Form(...),
):
    """Update a KB doc's section, title, and content."""
    db.update_kb_doc(kb_id, section=section, title=title, content=content)
    return RedirectResponse("/kb", status_code=303)


@app.post("/kb/{kb_id}/delete")
def kb_delete(kb_id: int):
    """Delete a KB doc."""
    db.delete_kb_doc(kb_id)
    return RedirectResponse("/kb", status_code=303)