"""
FastAPI web app for SeatRadar v2.

New in v2:
  • Feature 9  — WebSocket /ws/status (live check results)
  • Feature 1  — GET /api/search?movie=&city= (BMS movie → seat-map URL)
  • Feature 11 — SQLite via Database (replaces WatchStore JSON)
  • Feature 10 — optional Telegram bot startup
  • Feature 12 — structured JSON logging
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from auth import GoogleAuthService
from config.settings import load_config, validate_config
from db.database import Database
from models import UserAccount, WatchConfig
from monitor import WatchMonitorService, setup_logging
from scraper.bms_search import BMSSearch

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
SESSION_COOKIE = "seatradar_session"

if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

app = FastAPI(title="SeatRadar")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

db: Database = Database()
monitor_service: Optional[WatchMonitorService] = None
auth_service: Optional[PhoneAuthService] = None
app_config: dict = {}
telegram_bot = None


# ── WebSocket connection manager (Feature 9) ──────────────────────────────────

class ConnectionManager:
    def __init__(self) -> None:
        self._connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections = [c for c in self._connections if c is not ws]

    async def broadcast(self, data: dict) -> None:
        if not self._connections:
            return
        payload = json.dumps(data)
        dead: List[WebSocket] = []
        for conn in self._connections:
            try:
                await conn.send_text(payload)
            except Exception:
                dead.append(conn)
        for d in dead:
            self.disconnect(d)


manager = ConnectionManager()


# ── helpers ───────────────────────────────────────────────────────────────────

async def current_user(request: Request) -> Optional[UserAccount]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return await db.get_user_by_session(token)


# ── lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    global monitor_service, app_config, auth_service, telegram_bot
    setup_logging()
    app_config = await load_config("config.json")
    await validate_config(app_config)
    await db.connect()
    auth_service = GoogleAuthService(app_config)

    # Optional Telegram bot (Feature 10)
    bot_token = app_config.get("telegram_bot_token", "")
    if bot_token:
        try:
            from notifiers.telegram_bot import TelegramBot
            telegram_bot = TelegramBot(bot_token, db)
            await telegram_bot.start()
            logger.info("Telegram bot started")
        except Exception as exc:
            logger.warning("Telegram bot failed to start: %s", exc)
            telegram_bot = None

    monitor_service = WatchMonitorService(
        app_config, db,
        broadcast=manager.broadcast,
        telegram_bot=telegram_bot,
    )
    await monitor_service.start()
    logger.info("SeatRadar web app started")


@app.on_event("shutdown")
async def shutdown() -> None:
    if monitor_service:
        await monitor_service.stop()
    if telegram_bot:
        await telegram_bot.stop()
    await db.close()


# ── WebSocket endpoint (Feature 9) ────────────────────────────────────────────

@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()   # keep-alive ping/pong
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── Movie search API (Feature 1) ──────────────────────────────────────────────

@app.get("/api/search")
async def api_search(movie: str = "", city: str = "hyderabad") -> JSONResponse:
    if not movie.strip():
        return JSONResponse({"error": "movie query is required", "theatres": []}, status_code=400)
    if not monitor_service or not monitor_service.scraper:
        return JSONResponse({"error": "scraper not ready yet", "theatres": []}, status_code=503)

    searcher = BMSSearch(monitor_service.scraper.context)
    # BMSSearch uses Playwright context — run in PlaywrightRunner
    result = await monitor_service._playwright_runner.run(
        searcher.search(movie.strip(), city.strip())
    )
    return JSONResponse(result)


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/")
async def home(request: Request, error: str = ""):
    user = await current_user(request)
    if not user:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "auth_enabled": bool(auth_service and auth_service.enabled),
            "error": error,
        })
    watches = await db.list_watches(user.id)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "watches": watches,
        "config": app_config,
        "user": user,
    })


@app.get("/auth/google")
async def auth_google():
    if not auth_service or not auth_service.enabled:
        return RedirectResponse("/?error=oauth-not-configured", status_code=303)
    state = secrets.token_urlsafe(16)
    url = auth_service.authorization_url(state)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie("oauth_state", state, httponly=True, samesite="lax", max_age=300)
    return resp


@app.get("/auth/google/callback")
async def auth_google_callback(
    request: Request, code: str = "", state: str = "", error: str = ""
):
    if error:
        return RedirectResponse(f"/?error={error}", status_code=303)
    saved_state = request.cookies.get("oauth_state", "")
    if not state or state != saved_state:
        return RedirectResponse("/?error=invalid-state", status_code=303)
    if not auth_service:
        return RedirectResponse("/?error=auth-unavailable", status_code=303)
    access_token = await auth_service.exchange_code(code)
    if not access_token:
        return RedirectResponse("/?error=token-exchange-failed", status_code=303)
    user_info = await auth_service.get_user_info(access_token)
    if not user_info or not user_info.get("email"):
        return RedirectResponse("/?error=user-info-failed", status_code=303)
    user = await db.get_or_create_user_by_google(
        google_sub=user_info.get("id", ""),
        email=user_info.get("email", ""),
        avatar_url=user_info.get("picture", ""),
    )
    token = await db.create_session(user.id)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
    resp.delete_cookie("oauth_state")
    return resp


@app.post("/profile/phone")
async def save_phone(request: Request, to_number: str = Form("")):
    user = await current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    await db.update_user_to_number(user.id, to_number)
    return RedirectResponse("/", status_code=303)


@app.post("/auth/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await db.delete_session(token)
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ── Watches ───────────────────────────────────────────────────────────────────

@app.post("/watches")
async def create_watch(request: Request):
    user = await current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    if not form.get("show_url", "").strip():
        return RedirectResponse("/?error=url-required", status_code=303)
    watch = WatchConfig.from_form(form)
    watch.user_id = user.id
    watch.name = watch.movie_name or watch.show_url
    watch.notify_on_initial_status = True
    watch.notify_on_booking_open = True
    await db.save_watch(watch)
    return RedirectResponse("/", status_code=303)


@app.post("/watches/{watch_id}/toggle")
async def toggle_watch(request: Request, watch_id: str):
    user = await current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    watch = await db.get_watch(watch_id, user.id)
    if watch:
        watch.active = not watch.active
        await db.save_watch(watch)
    return RedirectResponse("/", status_code=303)


@app.post("/watches/{watch_id}/check")
async def run_watch_check(request: Request, watch_id: str):
    user = await current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    watch = await db.get_watch(watch_id, user.id)
    if watch and monitor_service:
        await monitor_service.check_watch(watch.id)
    return RedirectResponse("/", status_code=303)


@app.post("/watches/{watch_id}/delete")
async def delete_watch(request: Request, watch_id: str):
    user = await current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    await db.delete_watch(watch_id, user.id)
    return RedirectResponse("/", status_code=303)


@app.get("/watches/{watch_id}/logs")
async def watch_logs(request: Request, watch_id: str):
    user = await current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    logs = await db.get_recent_logs(watch_id, limit=20)
    return JSONResponse(logs)
