"""Cliente de WhatsApp.

Se usa la API oficial (WhatsApp Cloud API de Meta). Los mensajes que inicia el consultorio
fuera de la ventana de 24 h solo pueden enviarse con *plantillas aprobadas*; abajo están los
textos para crearlas en Meta Business Manager (ver README).
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Optional, Protocol

log = logging.getLogger(__name__)

# 131026 = "Message undeliverable": el número no tiene WhatsApp (o no puede recibir el mensaje).
NOT_ON_WHATSAPP_CODES = {131026}

TEMPLATE_BODIES = {
    "recordatorio_cita_7d": (
        "Hola {{1}}, te recordamos tu cita con la Dra Laura el {{2}} a las {{3}}. "
        "¿Confirmas tu asistencia?   [botones rápidos: Confirmar | Cancelar]"
    ),
    "recordatorio_cita_1d": "Recuerda que mañana tienes tu cita con la Dra Laura, {{1}}.",
    "aviso_consultorio": "Aviso del consultorio: {{1}}",
}


@dataclass
class SendResult:
    status: str  # "sent" | "not_on_whatsapp" | "error"
    message_id: Optional[str] = None
    detail: str = ""


class WhatsAppClient(Protocol):
    def send_template(
        self,
        to: str,
        template: str,
        params: list[str],
        buttons: Optional[list[str]] = None,
    ) -> SendResult: ...


class CloudWhatsAppClient:
    def __init__(
        self,
        token: str,
        phone_number_id: str,
        language: str = "es",
        api_version: str = "v21.0",
        timeout: float = 15.0,
    ):
        self.token = token
        self.url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        self.language = language
        self.timeout = timeout

    def send_template(self, to, template, params, buttons=None) -> SendResult:
        import httpx  # perezoso: el resto del sistema no depende de httpx

        components: list[dict] = []
        if params:
            components.append(
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
            )
        for index, payload in enumerate(buttons or []):
            components.append(
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": str(index),
                    "parameters": [{"type": "payload", "payload": payload}],
                }
            )
        body = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": self.language},
                "components": components,
            },
        }
        try:
            r = httpx.post(
                self.url,
                json=body,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:  # red caída, timeout, etc. -> se reintenta
            return SendResult("error", detail=str(exc))

        if r.status_code == 200:
            try:
                wamid = r.json()["messages"][0]["id"]
            except (KeyError, IndexError, ValueError):
                wamid = None
            return SendResult("sent", message_id=wamid)

        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        if err.get("code") in NOT_ON_WHATSAPP_CODES:
            return SendResult("not_on_whatsapp", detail=err.get("message", ""))
        return SendResult("error", detail=f"HTTP {r.status_code}: {err.get('message', r.text[:200])}")


class ConsoleWhatsAppClient:
    """Modo desarrollo: imprime los mensajes en el log en lugar de enviarlos."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)

    def send_template(self, to, template, params, buttons=None) -> SendResult:
        log.warning("[WhatsApp simulado] a=%s plantilla=%s params=%s botones=%s", to, template, params, buttons)
        return SendResult("sent", message_id=f"console-{next(self._ids)}")
