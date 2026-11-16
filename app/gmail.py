"""Gmail API service layer.

Wraps googleapiclient.discovery.build("gmail", "v1") with typed helpers for
inbox polling, message parsing, label management, and sending replies.
"""

import base64
import email.policy
from email.message import EmailMessage
from typing import Any, Self

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import settings

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iter_parts(payload: dict) -> list[dict]:
    """Yield every part (including nested) in a MIME payload tree."""
    parts: list[dict] = []
    for part in payload.get("parts", []):
        parts.append(part)
        parts.extend(_iter_parts(part))
    return parts


def _decode_payload(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload dict.

    Gmail returns body content as URL-safe base64 (RFC 4648 base64url), which
    uses '-' and '_' instead of '+' and '/'.  This function normalises the
    string, re-pads it, decodes it, and recurses into multipart containers.
    """
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    if body_data:
        # URL-safe base64 → standard base64, then decode to bytes
        padded = body_data.replace("-", "+").replace("_", "/")
        padded += "=" * (-len(padded) % 4)
        raw = base64.urlsafe_b64decode(padded)
        try:
            decoded = raw.decode("utf-8", errors="replace")
        except Exception:
            decoded = raw.decode("latin-1", errors="replace")
    else:
        decoded = ""

    # Only recurse into multipart containers; ignore text/html parts.
    if mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            decoded += _decode_payload(part)
        if decoded:
            decoded += "\n"

    elif mime_type == "text/plain":
        pass  # already decoded above
    else:
        decoded = ""  # skip text/html, attachments, etc.

    return decoded


def _parse_message(raw: dict) -> dict[str, Any]:
    """Convert a Gmail messages.get() response dict into a clean, typed dict.

    Returns
    -------
    dict with keys: id, thread_id, sender, subject, message_id_header,
    body, label_ids, headers (lowercase keys), has_attachments.
    """
    payload = raw["payload"]
    headers_raw: list[dict[str, str]] = payload.get("headers", [])

    # Case-insensitive header dict
    headers: dict[str, str] = {h["name"].lower(): h["value"] for h in headers_raw}

    # Strip angle brackets from Message-Id if caller included them
    raw_msg_id = headers.get("message-id", "")
    message_id_header = raw_msg_id.strip("<>")

    body = _decode_payload(payload)

    # has_attachments: any MIME part with a non-empty filename
    has_attachments = any(
        part.get("filename", "") != "" for part in _iter_parts(payload)
    )

    return {
        "id": raw["id"],
        "thread_id": raw["threadId"],
        "sender": headers.get("from", "unknown"),
        "subject": headers.get("subject", ""),
        "message_id_header": message_id_header,
        "body": body.strip(),
        "label_ids": raw.get("labelIds", []),
        "headers": headers,
        "has_attachments": has_attachments,
    }


# ---------------------------------------------------------------------------
# GmailService
# ---------------------------------------------------------------------------


class GmailService:
    """Thin typed wrapper around the Gmail API v1."""

    def __init__(self: Self) -> None:
        creds = Credentials(
            token=None,
            refresh_token=settings.google_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
        self._gmail = build("gmail", "v1", credentials=creds)

    # ------------------------------------------------------------------
    # Query / fetch
    # ------------------------------------------------------------------

    def list_unprocessed_messages(self: Self) -> list[str]:
        """Return IDs of inbox messages that have not been labelled AI_PROCESSED."""
        response: dict[str, Any] = (
            self._gmail.users()
            .messages()
            .list(userId="me", q="in:inbox -label:AI_PROCESSED")
            .execute()
        )
        return [m["id"] for m in response.get("messages", [])]

    def get_message(self: Self, message_id: str) -> dict[str, Any]:
        """Fetch a single message (format="full") and return a parsed dict."""
        raw: dict[str, Any] = (
            self._gmail.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return _parse_message(raw)

    def get_thread(self: Self, thread_id: str) -> list[dict[str, Any]]:
        """Fetch all messages in a thread in chronological order (oldest first)."""
        raw_thread: dict[str, Any] = (
            self._gmail.users()
            .threads()
            .get(userId="me", id=thread_id)
            .execute()
        )
        # threads.get() already returns messages oldest-first.
        return [_parse_message(m) for m in raw_thread.get("messages", [])]

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def add_label(self: Self, message_id: str, label_name: str) -> None:
        """Apply *label_name* to the message.

        Creates the label if it does not already exist in the account.
        """
        # 1. Look up existing labels (labels().list does not support
        #    pageToken, so a single call returns the full label set).
        label_id: str | None = None
        result: dict[str, Any] = (
            self._gmail.users().labels().list(userId="me").execute()
        )
        for label in result.get("labels", []):
            if label["name"] == label_name:
                label_id = label["id"]
                break

        # 2. Create if missing
        if not label_id:
            created: dict[str, Any] = (
                self._gmail.users()
                .labels()
                .create(userId="me", body={"name": label_name})
                .execute()
            )
            label_id = created["id"]

        # 3. Apply to message
        self._gmail.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    def send_reply(
        self: Self,
        to: str,
        subject: str,
        body: str,
        thread_id: str,
        in_reply_to: str | None = None,
    ) -> dict[str, Any]:
        """Send a plain-text reply email and return the API response dict.

        Parameters
        ----------
        to : str
            Recipient email address.
        subject : str
            Original subject line; "Re: " is prefixed automatically if absent.
        body : str
            Plain-text email body.
        thread_id : str
            Gmail thread ID to reply within.
        in_reply_to : str | None
            Value for the ``In-Reply-To`` header (typically the parent's
            ``message_id_header``). Angle brackets are stripped automatically.

        Notes
        -----
        The ``References`` header is set to the *in_reply_to* value.  If you
        need the full references chain, pass the concatenated string as
        *in_reply_to*.
        """
        if not subject.startswith("Re:"):
            subject = f"Re: {subject}"

        msg = EmailMessage()
        msg["To"] = to
        msg["From"] = settings.support_email
        msg["Subject"] = subject

        if in_reply_to:
            in_reply_to = in_reply_to.strip("<>")
            msg["In-Reply-To"] = in_reply_to
            # Single-element references chain (extend caller-side if needed)
            msg["References"] = in_reply_to

        msg.set_content(body)

        # Gmail API: raw RFC 2822 message as URL-safe base64 (no padding)
        raw_bytes = msg.as_bytes(policy=email.policy.SMTP)
        encoded = (
            base64.urlsafe_b64encode(raw_bytes)
            .decode("ascii")
            .rstrip("=")
        )

        result: dict[str, Any] = (
            self._gmail.users()
            .messages()
            .send(userId="me", body={"raw": encoded, "threadId": thread_id})
            .execute()
        )
        return result

