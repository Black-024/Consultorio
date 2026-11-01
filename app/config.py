"""Configuración del sistema. Todo se lee de variables de entorno (ver .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .phone import normalize_phone


@dataclass
class Settings:
    db_path: str = "consultorio.db"
    timezone: str = "America/Argentina/Buenos_Aires"
    default_country_code: str = "54"

    # Personas que reciben los avisos por WhatsApp (Dra Laura y su asistente).
    staff_phones: list[str] = field(default_factory=list)
    # Usuarios del panel web: {"laura": "clave", "asistente": "clave"}
    panel_users: dict[str, str] = field(default_factory=dict)

    # Reglas del negocio (las del spec, configurables).
    first_reminder_days: int = 7
    last_reminder_days: int = 1
    no_response_days: int = 5
    max_send_attempts: int = 3
    appointment_minutes: int = 30
    # Los recordatorios solo salen en esta franja horaria local (no de madrugada).
    send_window_start: int = 8
    send_window_end: int = 21
    tick_seconds: int = 60

    # WhatsApp Cloud API (Meta). Si falta el token se usa un cliente de consola (modo desarrollo).
    wa_token: str = ""
    wa_phone_number_id: str = ""
    wa_verify_token: str = ""
    wa_app_secret: str = ""
    wa_language: str = "es"
    wa_template_first: str = "recordatorio_cita_7d"
    wa_template_last: str = "recordatorio_cita_1d"
    wa_template_staff: str = "aviso_consultorio"

    # Google Calendar (cuenta de servicio). Si falta, las citas no se sincronizan.
    google_calendar_id: str = ""
    google_credentials_file: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        cc = env("DEFAULT_COUNTRY_CODE", "54")
        staff = [
            normalize_phone(p, cc)
            for p in env("STAFF_PHONES", "").split(",")
            if p.strip()
        ]
        users: dict[str, str] = {}
        for pair in env("PANEL_USERS", "").split(","):
            if ":" in pair:
                user, _, pw = pair.strip().partition(":")
                if user and pw:
                    users[user] = pw
        return cls(
            db_path=env("DB_PATH", "consultorio.db"),
            timezone=env("TIMEZONE", "America/Argentina/Buenos_Aires"),
            default_country_code=cc,
            staff_phones=staff,
            panel_users=users,
            appointment_minutes=int(env("APPOINTMENT_MINUTES", "30")),
            send_window_start=int(env("SEND_WINDOW_START", "8")),
            send_window_end=int(env("SEND_WINDOW_END", "21")),
            wa_token=env("WA_TOKEN", ""),
            wa_phone_number_id=env("WA_PHONE_NUMBER_ID", ""),
            wa_verify_token=env("WA_VERIFY_TOKEN", ""),
            wa_app_secret=env("WA_APP_SECRET", ""),
            wa_language=env("WA_LANGUAGE", "es"),
            wa_template_first=env("WA_TEMPLATE_FIRST", "recordatorio_cita_7d"),
            wa_template_last=env("WA_TEMPLATE_LAST", "recordatorio_cita_1d"),
            wa_template_staff=env("WA_TEMPLATE_STAFF", "aviso_consultorio"),
            google_calendar_id=env("GOOGLE_CALENDAR_ID", ""),
            google_credentials_file=env("GOOGLE_CREDENTIALS_FILE", ""),
        )
