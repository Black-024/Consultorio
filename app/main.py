"""Capa HTTP (FastAPI): panel web, API para la Dra Laura y su asistente, y webhook de WhatsApp.

Es deliberadamente fina: toda la lógica vive en service.py y webhook.py.
Nota: este módulo NO usa `from __future__ import annotations` para que FastAPI/Pydantic
resuelvan bien los tipos de los modelos.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from secrets import compare_digest
from typing import Callable, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import webhook
from .calendar_client import CalendarClient, GoogleCalendar
from .config import Settings
from .db import Database
from .service import Service
from .whatsapp import CloudWhatsAppClient, ConsoleWhatsAppClient, WhatsAppClient

log = logging.getLogger("consultorio")
STATIC_DIR = Path(__file__).parent / "static"


class AppointmentIn(BaseModel):
    patient_name: str
    phone: str
    starts_at: datetime  # hora local del consultorio si viene sin zona horaria


class StatusIn(BaseModel):
    status: Literal["confirmed", "cancelled"]


def _build_whatsapp(settings: Settings) -> WhatsAppClient:
    if settings.wa_token and settings.wa_phone_number_id:
        return CloudWhatsAppClient(settings.wa_token, settings.wa_phone_number_id, settings.wa_language)
    log.warning("WA_TOKEN/WA_PHONE_NUMBER_ID no configurados: los WhatsApp se SIMULAN en el log.")
    return ConsoleWhatsAppClient()


def _build_calendar(settings: Settings) -> Optional[CalendarClient]:
    if settings.google_calendar_id and settings.google_credentials_file:
        return GoogleCalendar(settings.google_calendar_id, settings.google_credentials_file)
    log.warning("Google Calendar no configurado: las citas no se sincronizarán con el calendario.")
    return None


async def _scheduler(service: Service, seconds: int) -> None:
    while True:
        try:
            await asyncio.to_thread(service.tick)
        except Exception:  # noqa: BLE001
            log.exception("Falló el ciclo de recordatorios")
        await asyncio.sleep(seconds)


def create_app(
    settings: Optional[Settings] = None,
    whatsapp: Optional[WhatsAppClient] = None,
    calendar: Optional[CalendarClient] = None,
    clock: Optional[Callable[[], datetime]] = None,
    start_scheduler: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if not settings.panel_users:
        raise RuntimeError(
            "Define PANEL_USERS (por ejemplo 'laura:clave1,asistente:clave2'): "
            "el panel contiene datos de pacientes y no puede quedar sin contraseña."
        )
    if start_scheduler:
        logging.basicConfig(level=logging.INFO)

    service = Service(
        settings,
        Database(settings.db_path),
        whatsapp or _build_whatsapp(settings),
        calendar if calendar is not None else _build_calendar(settings),
        clock,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(_scheduler(service, settings.tick_seconds)) if start_scheduler else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Consultorio Dra. Laura", lifespan=lifespan)
    app.state.service = service

    # ---------------------------------------------------------------- autenticación del panel
    security = HTTPBasic()

    def require_user(credentials: HTTPBasicCredentials = Depends(security)) -> str:
        ok = False
        for user, password in settings.panel_users.items():
            same_user = compare_digest(credentials.username.encode(), user.encode())
            same_pass = compare_digest(credentials.password.encode(), password.encode())
            ok = ok or (same_user and same_pass)
        if not ok:
            raise HTTPException(401, "Usuario o clave incorrectos", headers={"WWW-Authenticate": "Basic"})
        return credentials.username

    api = APIRouter(prefix="/api", dependencies=[Depends(require_user)])

    # Los endpoints son `def` (no `async def`): FastAPI los ejecuta en un hilo y las llamadas
    # bloqueantes a WhatsApp/Google/SQLite no frenan al servidor.
    @api.get("/appointments")
    def list_appointments(include_past: bool = False):
        return service.list_appointments(include_past=include_past)

    @api.post("/appointments", status_code=201)
    def create_appointment(body: AppointmentIn):
        try:
            return service.create_appointment(body.patient_name, body.phone, body.starts_at)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @api.post("/appointments/{appt_id}/status")
    def set_status(appt_id: int, body: StatusIn):
        appt = service.set_status(appt_id, body.status)
        if appt is None:
            raise HTTPException(404, "La cita no existe")
        return appt

    @api.get("/notifications")
    def list_notifications():
        return service.list_notifications()

    @api.delete("/notifications/{notif_id}", status_code=204)
    def delete_notification(notif_id: int):
        if not service.delete_notification(notif_id):
            raise HTTPException(404, "La notificación no existe")

    @api.delete("/notifications", status_code=204)
    def clear_notifications():
        service.clear_notifications()

    app.include_router(api)

    # ---------------------------------------------------------------- webhook de WhatsApp
    @app.get("/webhook/whatsapp", include_in_schema=False)
    def verify_webhook(
        mode: Optional[str] = Query(None, alias="hub.mode"),
        challenge: Optional[str] = Query(None, alias="hub.challenge"),
        token: Optional[str] = Query(None, alias="hub.verify_token"),
    ):
        expected = settings.wa_verify_token
        if mode == "subscribe" and expected and compare_digest((token or "").encode(), expected.encode()):
            return PlainTextResponse(challenge or "")
        raise HTTPException(403, "Token de verificación incorrecto")

    @app.post("/webhook/whatsapp", include_in_schema=False)
    async def receive_webhook(request: Request):
        raw = await request.body()
        if settings.wa_app_secret and not webhook.verify_signature(
            settings.wa_app_secret, raw, request.headers.get("X-Hub-Signature-256")
        ):
            raise HTTPException(403, "Firma inválida")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "JSON inválido")
        await asyncio.to_thread(webhook.dispatch, service, payload)
        return {"ok": True}

    # ---------------------------------------------------------------- interfaz web
    @app.get("/health", include_in_schema=False)
    def health():
        return {"ok": True}

    @app.get("/", include_in_schema=False, dependencies=[Depends(require_user)])
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
