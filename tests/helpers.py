"""Dobles de prueba: reloj, WhatsApp y Google Calendar simulados, y payloads al estilo Meta."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.whatsapp import SendResult

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
STAFF = ["5491100000001", "5491100000002"]  # Dra Laura y su asistente
PATIENT_INPUT = "+54 9 11 5555-1234"
PATIENT = "5491155551234"


def local(text: str) -> datetime:
    """'2026-11-20 10:00' (hora de Buenos Aires) -> datetime con zona horaria."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


class FakeClock:
    def __init__(self, text: str):
        self.set(text)

    def set(self, text: str) -> None:
        self.value = local(text).astimezone(timezone.utc)

    def __call__(self) -> datetime:
        return self.value


class FakeWhatsApp:
    """Registra cada intento de envío. Permite simular números sin WhatsApp y errores de red."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.ids: list[str] = []
        self.not_on_whatsapp: set[str] = set()
        self.errors = 0  # cuántos envíos siguientes fallan con error transitorio

    def send_template(self, to, template, params, buttons=None) -> SendResult:
        if self.errors > 0:
            self.errors -= 1
            return SendResult("error", detail="timeout")
        self.sent.append({"to": to, "template": template, "params": params, "buttons": buttons})
        if to in self.not_on_whatsapp:
            return SendResult("not_on_whatsapp")
        wamid = f"wamid.{len(self.ids) + 1}"
        self.ids.append(wamid)
        return SendResult("sent", message_id=wamid)

    def to_patient(self, phone: str = PATIENT) -> list[dict]:
        return [m for m in self.sent if m["to"] == phone]

    def to_staff(self) -> list[dict]:
        return [m for m in self.sent if m["to"] in STAFF]


class FakeCalendar:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.fail = False

    def create_event(self, summary, start, end, description=None) -> str:
        if self.fail:
            raise RuntimeError("Google Calendar no responde")
        self.events.append({"summary": summary, "start": start, "end": end, "description": description})
        return f"evt-{len(self.events)}"


# --- Payloads con la forma del webhook de WhatsApp Cloud API ---------------------------------
def _wrap(value: dict) -> dict:
    return {"entry": [{"changes": [{"value": value}]}]}


def inbound_button(phone: str, payload: str, text: str = "Confirmar") -> dict:
    return _wrap({"messages": [{"from": phone, "type": "button", "button": {"payload": payload, "text": text}}]})


def inbound_text(phone: str, body: str) -> dict:
    return _wrap({"messages": [{"from": phone, "type": "text", "text": {"body": body}}]})


def status_failed(wamid: str, code: int) -> dict:
    return _wrap({"statuses": [{"id": wamid, "status": "failed", "errors": [{"code": code}]}]})
