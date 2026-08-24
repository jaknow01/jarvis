"""Facebook Messenger adapter: signature verification + Send API.

Thin, transport-only layer. It does **not** know about agents — the webhook
(`app/webhook.py`) wires this together with the transport-agnostic core
(`lib/engine.handle_message`). See `docs/MESSENGER.md` for the Meta-side setup
and the list of `MESSENGER_*` env vars.

Secrets stay server-side: the page access token / app secret live in the
environment and never reach the model.
"""

import hashlib
import hmac
import logging
import os

import httpx

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v22.0"
SEND_API_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"

# Messenger caps a single text message at 2000 chars; longer replies are chunked.
_MAX_MESSAGE_CHARS = 2000


def verify_signature(app_secret: str, payload: bytes, header: str | None) -> bool:
    """Validate the `X-Hub-Signature-256` header against the raw request body.

    Meta signs every webhook POST with HMAC-SHA256 over the raw body using the
    app secret. Rejecting mismatches is what stops a stranger from POSTing fake
    messages to the public endpoint.

    Args:
        app_secret: `MESSENGER_APP_SECRET`.
        payload: the raw request body bytes (must be the exact bytes received).
        header: the incoming `X-Hub-Signature-256` header (e.g. "sha256=abc...").

    Returns:
        True iff the signature is present, well-formed, and matches.
    """
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), payload, hashlib.sha256).hexdigest()
    received = header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


def verify_challenge(
    verify_token: str, mode: str | None, token: str | None, challenge: str | None
) -> str | None:
    """Handle the GET webhook-verification handshake.

    Meta calls the callback URL with `hub.mode=subscribe`, `hub.verify_token`,
    and `hub.challenge`. If the token matches ours, echo the challenge back.

    Returns:
        The challenge string to echo on success, else None (caller returns 403).
    """
    if mode == "subscribe" and token and hmac.compare_digest(token, verify_token):
        return challenge
    return None


def iter_message_events(body: dict):
    """Yield `(sender_id, text)` for each inbound text message in a webhook body.

    Skips non-message events (deliveries, reads, echoes of the page's own
    outgoing messages) and messages without a text part (stickers, attachments).
    """
    if body.get("object") != "page":
        return
    for entry in body.get("entry", []):
        for event in entry.get("messaging", []):
            message = event.get("message")
            if not message or message.get("is_echo"):
                continue
            text = message.get("text")
            sender_id = event.get("sender", {}).get("id")
            if text and sender_id:
                yield sender_id, text


def _chunks(text: str, size: int = _MAX_MESSAGE_CHARS):
    for i in range(0, len(text), size):
        yield text[i : i + size]


async def send_message(
    page_access_token: str,
    recipient_id: str,
    text: str,
    messaging_type: str = "RESPONSE",
    tag: str | None = None,
) -> None:
    """Send a text reply to `recipient_id` via the Messenger Send API.

    Long replies are split into <=2000-char chunks sent in order. Network/API
    errors are logged and swallowed so a delivery failure never crashes the caller
    (for the webhook the inbound event was already acknowledged with 200).

    Args:
        messaging_type: "RESPONSE" for a normal reply inside the 24h window, or
            "MESSAGE_TAG" for a proactive push outside it (requires `tag`).
        tag: message tag when `messaging_type="MESSAGE_TAG"`, e.g. "HUMAN_AGENT"
            (7-day window — used by the scheduler for proactive briefs/reminders).

    Proactive fallback: if a tagged send is rejected (e.g. the tag/window is not
    permitted), we retry the same chunk once as a plain RESPONSE — which succeeds
    only if the 24h window happens to be open — and otherwise log a hint. This keeps
    a scheduler push best-effort without ever raising.
    """
    params = {"access_token": page_access_token}
    async with httpx.AsyncClient(timeout=15) as client:
        for chunk in _chunks(text):
            payload = {
                "recipient": {"id": recipient_id},
                "messaging_type": messaging_type,
                "message": {"text": chunk},
            }
            if messaging_type == "MESSAGE_TAG" and tag:
                payload["tag"] = tag
            try:
                resp = await client.post(SEND_API_URL, params=params, json=payload)
                if resp.status_code >= 400:
                    logger.error(
                        "Send API error %s: %s", resp.status_code, resp.text[:500]
                    )
                    if messaging_type == "MESSAGE_TAG":
                        logger.warning(
                            "Tagged send rejected; retrying as RESPONSE (works only if "
                            "the 24h window is open). If proactive pushes keep failing, "
                            "message the bot once to reopen the window, or enable the "
                            "HUMAN_AGENT tag in the Meta panel."
                        )
                        retry = {
                            "recipient": {"id": recipient_id},
                            "messaging_type": "RESPONSE",
                            "message": {"text": chunk},
                        }
                        try:
                            r2 = await client.post(SEND_API_URL, params=params, json=retry)
                            if r2.status_code >= 400:
                                logger.error(
                                    "Send API RESPONSE fallback also failed %s: %s",
                                    r2.status_code, r2.text[:500],
                                )
                        except httpx.HTTPError as exc:
                            logger.error("Send API fallback request failed: %s", exc)
            except httpx.HTTPError as exc:
                logger.error("Send API request failed: %s", exc)
