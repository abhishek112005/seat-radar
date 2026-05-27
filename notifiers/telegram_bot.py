"""
Telegram bot notifier for SeatRadar (Feature 10).

Uses python-telegram-bot v20+ (fully async, integrates with uvicorn's event loop).

Commands:
  /start               — welcome + help
  /status              — list all watches for this chat
  /pause  <watch_id>   — pause a watch
  /resume <watch_id>   — resume a watch
  /delete <watch_id>   — delete a watch

Replies:
  STOP                 — pauses the most recently alerted watch for this chat

Sending alerts:
  bot.send_alert(chat_id, message)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

if TYPE_CHECKING:
    from db.database import Database

logger = logging.getLogger(__name__)


class TelegramBot:
    """Wraps python-telegram-bot Application for use inside uvicorn's event loop."""

    def __init__(self, token: str, db: "Database") -> None:
        self._db = db
        self._app = (
            Application.builder()
            .token(token)
            .updater(None)           # disable built-in polling; we start it manually
            .build()
        )
        # Use proper updater for polling
        self._app = Application.builder().token(token).build()
        self._register_handlers()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise and start polling.  Call from FastAPI startup."""
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        logger.info("Telegram bot polling started")

    async def stop(self) -> None:
        """Stop polling gracefully.  Call from FastAPI shutdown."""
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bot stopped")
        except Exception as exc:
            logger.warning("Telegram bot stop error: %s", exc)

    # ── public send helper ────────────────────────────────────────────────────

    async def send_alert(self, chat_id: str, message: str) -> bool:
        """Send a plain-text alert to *chat_id*."""
        try:
            await self._app.bot.send_message(chat_id=int(chat_id), text=message)
            logger.info("Telegram alert sent to %s", chat_id)
            return True
        except Exception as exc:
            logger.error("Telegram send_alert failed for %s: %s", chat_id, exc)
            return False

    # ── handler registration ──────────────────────────────────────────────────

    def _register_handlers(self) -> None:
        add = self._app.add_handler
        add(CommandHandler("start",  self._cmd_start))
        add(CommandHandler("status", self._cmd_status))
        add(CommandHandler("pause",  self._cmd_pause))
        add(CommandHandler("resume", self._cmd_resume))
        add(CommandHandler("delete", self._cmd_delete))
        add(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))

    # ── command handlers ──────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)
        await self._db.set_user_telegram(
            await self._resolve_user_id(chat_id), chat_id
        ) if await self._resolve_user_id(chat_id) else None
        await update.message.reply_text(
            "SeatRadar Bot\n\n"
            "/status — view your watches\n"
            "/pause <id> — pause a watch\n"
            "/resume <id> — resume a watch\n"
            "/delete <id> — delete a watch\n\n"
            "Reply STOP to pause the latest watch."
        )

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)
        watches = await self._watches_for_chat(chat_id)
        if not watches:
            await update.message.reply_text(
                "No watches linked to this chat.\n"
                "Create one at the SeatRadar dashboard and add this chat ID: "
                + chat_id
            )
            return
        lines = []
        for w in watches:
            icon = "▶" if w.active else "⏸"
            lines.append(f"{icon} [{w.id[:6]}] {w.display_name()} — {w.last_status}")
        await update.message.reply_text("\n".join(lines))

    async def _cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_active(update, ctx, active=False)

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_active(update, ctx, active=True)

    async def _cmd_delete(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)
        watch_id = ctx.args[0] if ctx.args else ""
        if not watch_id:
            await update.message.reply_text("Usage: /delete <watch_id>")
            return
        watch = await self._find_watch(chat_id, watch_id)
        if not watch:
            await update.message.reply_text(f"Watch '{watch_id}' not found.")
            return
        await self._db.delete_watch(watch.id)
        await update.message.reply_text(f"Deleted watch: {watch.display_name()}")

    async def _on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message and update.message.text.strip().upper() == "STOP":
            chat_id = str(update.effective_chat.id)
            watches = await self._watches_for_chat(chat_id)
            active = [w for w in watches if w.active]
            if active:
                w = active[0]
                w.active = False
                await self._db.save_watch(w)
                await update.message.reply_text(
                    f"Watch paused: {w.display_name()}\nReply /resume {w.id[:6]} to restart."
                )
            else:
                await update.message.reply_text("No active watches to pause.")

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _set_active(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE, active: bool
    ) -> None:
        chat_id = str(update.effective_chat.id)
        verb = "resume" if active else "pause"
        watch_id = ctx.args[0] if ctx.args else ""
        if not watch_id:
            await update.message.reply_text(f"Usage: /{verb} <watch_id>")
            return
        watch = await self._find_watch(chat_id, watch_id)
        if not watch:
            await update.message.reply_text(f"Watch '{watch_id}' not found.")
            return
        watch.active = active
        await self._db.save_watch(watch)
        state = "resumed" if active else "paused"
        await update.message.reply_text(f"Watch {state}: {watch.display_name()}")

    async def _watches_for_chat(self, chat_id: str):
        all_watches = await self._db.list_watches()
        return [w for w in all_watches if w.telegram_chat_id == chat_id]

    async def _find_watch(self, chat_id: str, partial_id: str):
        watches = await self._watches_for_chat(chat_id)
        for w in watches:
            if w.id.startswith(partial_id) or w.id == partial_id:
                return w
        return None

    async def _resolve_user_id(self, chat_id: str) -> Optional[str]:
        user = await self._db.get_user_by_telegram(chat_id)
        return user.id if user else None
