"""Facebook Messenger webhook — HTTP entry point into Jarvis.

Model flow (see docs/MESSENGER.md):

    Meta ──POST──► /webhook ──► verify signature ──► 200 OK (<5 s)
                                     │
                                     └─(background)─► engine.handle_message
                                                          └─► Send API reply

Run it with uvicorn (env is loaded here so uvicorn picks up `.env`):

    poetry run uvicorn app.webhook:app --host 0.0.0.0 --port 8002

The endpoint is transport-only: it hands each inbound text to the same
`engine.handle_message` the REPL uses, keyed per Messenger sender so every
conversation keeps its own continuity.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request, Response

from lib.cache import Cache, Ctx
from lib.engine import handle_message
from lib.hooks import LoggingRunHooks
from lib.logger import Logger
from lib.messenger import (
    iter_message_events,
    send_message,
    verify_challenge,
    verify_signature,
)
from lib.scheduler_runner import run_scheduler_loop, scheduler_enabled
from lib.tracing import setup_tracing

load_dotenv()

logger = logging.getLogger(__name__)

VERIFY_TOKEN = os.getenv("MESSENGER_VERIFY_TOKEN", "")
APP_SECRET = os.getenv("MESSENGER_APP_SECRET", "")
PAGE_ACCESS_TOKEN = os.getenv("MESSENGER_PAGE_ACCESS_TOKEN", "")
# Optional allowlist: restrict the bot to one sender (the owner's PSID). Empty =
# accept anyone the app is allowed to talk to (dev mode already limits this).
ALLOWED_SENDER_ID = os.getenv("MESSENGER_ALLOWED_SENDER_ID", "").strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Logger.config_root_logger()
    setup_tracing()
    app.state.ctx = Ctx(cache=Cache())
    app.state.hooks = LoggingRunHooks()
    if not (VERIFY_TOKEN and APP_SECRET and PAGE_ACCESS_TOKEN):
        logger.warning(
            "Messenger env incomplete — set MESSENGER_VERIFY_TOKEN, "
            "MESSENGER_APP_SECRET, MESSENGER_PAGE_ACCESS_TOKEN."
        )

    # Start the proactive scheduler loop (the alternate, system-driven entry path).
    # In-process asyncio task, cancelled on shutdown; disabled via SCHEDULER_ENABLED.
    scheduler_task = None
    if scheduler_enabled():
        scheduler_task = asyncio.create_task(run_scheduler_loop())
        logger.info("Scheduler loop task started")
    else:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED")

    logger.info("Messenger webhook ready")
    yield

    if scheduler_task:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/webhook")
async def verify(request: Request) -> Response:
    """Meta's GET verification handshake."""
    params = request.query_params
    challenge = verify_challenge(
        VERIFY_TOKEN,
        params.get("hub.mode"),
        params.get("hub.verify_token"),
        params.get("hub.challenge"),
    )
    if challenge is None:
        logger.warning("Webhook verification failed (bad verify token or mode)")
        return Response(status_code=403)
    return Response(content=challenge, media_type="text/plain")


async def _process(ctx: Ctx, hooks: LoggingRunHooks, sender_id: str, text: str) -> None:
    """Run the coordinator for one inbound message and send the reply back."""
    logger.conversation(f"[MESSENGER {sender_id}] {text}")
    try:
        reply = await handle_message(f"messenger:{sender_id}", text, ctx, hooks)
    except Exception:
        logger.exception("handle_message failed for %s", sender_id)
        reply = "Przepraszam, coś poszło nie tak po mojej stronie."
    logger.conversation(f"[ASSISTANT -> {sender_id}] {reply}")
    await send_message(PAGE_ACCESS_TOKEN, sender_id, reply)


@app.post("/webhook")
async def receive(request: Request, background: BackgroundTasks) -> Response:
    """Receive inbound events; ack fast, process in the background."""
    raw = await request.body()
    if not verify_signature(APP_SECRET, raw, request.headers.get("X-Hub-Signature-256")):
        logger.warning("Rejected webhook POST: bad or missing signature")
        return Response(status_code=403)

    body = await request.json()
    ctx: Ctx = request.app.state.ctx
    hooks: LoggingRunHooks = request.app.state.hooks

    for sender_id, text in iter_message_events(body):
        if ALLOWED_SENDER_ID and sender_id != ALLOWED_SENDER_ID:
            logger.info("Ignoring message from non-allowed sender %s", sender_id)
            continue
        background.add_task(_process, ctx, hooks, sender_id, text)

    # Always 200 within the 5 s window; work happens after the response is sent.
    return Response(status_code=200)
