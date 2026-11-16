"""Forever-running background worker.

Polls Gmail for unprocessed inbox messages on a fixed interval and hands each
one to :func:`app.pipeline.handle_message`.  The loop is deliberately
resilient: a top-level ``except Exception`` logs the failure and keeps the
worker alive, and ``KeyboardInterrupt`` exits cleanly.
"""

import logging
import time

from app.config import settings
from app.gmail import GmailService
from app.pipeline import handle_message

logger = logging.getLogger(__name__)


def poll_once(gmail: GmailService) -> int:
    """Run one polling cycle: check Gmail and process every unread message.

    Parameters
    ----------
    gmail : GmailService
        The Gmail API wrapper to query.

    Returns
    -------
    int
        The number of message IDs that were checked this cycle.
    """
    # 1. Ask Gmail for every inbox message that is not yet labelled
    #    AI_PROCESSED.
    message_ids = gmail.list_unprocessed_messages()

    # 2. Feed each ID through the per-message pipeline.  Any failure on an
    #    individual message is contained inside handle_message.
    for message_id in message_ids:
        handle_message(message_id, gmail)

    # 3. Report how many messages we checked this cycle.
    return len(message_ids)


def main() -> None:
    """Run the poller forever, staying alive across transient failures.

    Logs to stdout with a level message, then loops indefinitely over
    :func:`poll_once`, sleeping :data:`settings.poll_interval_seconds`
    between cycles.  Network/API errors are logged but never fatal;
    Ctrl-C (KeyboardInterrupt) ends the loop cleanly.
    """
    # 1. Configure root logging once, with a readable per-module format.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info("Starting AI customer support poller...")

    # 2. Build the shared Gmail service wrapper.
    gmail = GmailService()

    # 3. Poll forever.
    while True:
        try:
            # 3a. Process whatever is waiting in the inbox this cycle.
            checked = poll_once(gmail)
            logger.info("Polled %d unprocessed message(s).", checked)

            # 3b. Wait for the configured interval before the next cycle.
            time.sleep(settings.poll_interval_seconds)

        except KeyboardInterrupt:
            # 3c. Ctrl-C: stop cleanly instead of failing.
            logger.info("Received interrupt; shutting down cleanly.")
            break

        except Exception:
            # 3d. Top-level safety net: a transient network/API error must
            #     not kill the worker.  Log the full traceback, wait out the
            #     interval (so persistent failures don't busy-loop the API),
            #     and retry.
            logger.exception(
                "Poll cycle failed; retrying after %ss.",
                settings.poll_interval_seconds,
            )
            time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
