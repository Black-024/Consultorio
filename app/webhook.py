"""Procesamiento del webhook de WhatsApp Cloud API (independiente del framework web)."""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional

from .service import Service

log = logging.getLogger(__name__)


def verify_signature(app_secret: str, raw_body: bytes, header: Optional[str]) -> bool:
    """Valida X-Hub-Signature-256: HMAC-SHA256 del cuerpo con el app secret de Meta."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def dispatch(service: Service, payload: dict) -> None:
    """Reparte mensajes entrantes (respuestas de pacientes) y estados de entrega."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            for msg in value.get("messages", []):
                try:
                    kind = msg.get("type")
                    button_payload, text = None, None
                    if kind == "button":  # respuesta rápida de una plantilla
                        button_payload = msg.get("button", {}).get("payload")
                        text = msg.get("button", {}).get("text")
                    elif kind == "interactive":
                        button_payload = msg.get("interactive", {}).get("button_reply", {}).get("id")
                    elif kind == "text":
                        text = msg.get("text", {}).get("body")
                    service.handle_patient_reply(msg.get("from", ""), payload=button_payload, text=text)
                except Exception:  # noqa: BLE001
                    log.exception("Error procesando un mensaje entrante")

            for st in value.get("statuses", []):
                try:
                    errors = st.get("errors") or [{}]
                    service.handle_delivery_status(st.get("id"), st.get("status", ""), errors[0].get("code"))
                except Exception:  # noqa: BLE001
                    log.exception("Error procesando un estado de entrega")
