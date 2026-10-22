"""Where owner notifications go (used by the /internal/notify endpoint)."""

import logging
import os
from typing import Optional

from lib.domain import OWNER_USER_ID, principals

logger = logging.getLogger(__name__)


def owner_messenger_id() -> Optional[str]:
    """The owner's Messenger PSID: their messenger identity, else MESSENGER_ALLOWED_SENDER_ID."""
    try:
        for ident in principals.identities_for(OWNER_USER_ID):
            if ident.get("channel") == "messenger" and ident.get("external_id"):
                return str(ident["external_id"])
    except Exception:  # principals store unavailable — fall back to the env allowlist
        logger.debug("owner identity lookup failed", exc_info=True)
    return (os.getenv("MESSENGER_ALLOWED_SENDER_ID") or "").strip() or None
