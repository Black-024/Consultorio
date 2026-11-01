"""Lógica de negocio del consultorio. No depende de FastAPI ni de HTTP: se prueba directo.

Flujo resumido:
  * create_appointment  -> guarda la cita, crea el evento en Google Calendar y evalúa recordatorios.
  * tick (cada minuto)  -> envía el recordatorio de 7 días y el de 1 día, reintenta fallos,
                           detecta pacientes sin respuesta y reintenta eventos de calendario.
  * handle_patient_reply / handle_delivery_status -> entradas desde el webhook de WhatsApp.
"""
from __future__ import annotations

import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .calendar_client import CalendarClient
from .config import Settings
from .db import Database
from .phone import normalize_phone, same_phone
from .whatsapp import NOT_ON_WHATSAPP_CODES, WhatsAppClient

log = logging.getLogger(__name__)

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
CONFIRM_WORDS = {"si", "confirmo", "confirmar", "confirmado", "ok", "dale"}
CANCEL_WORDS = {"no", "cancelo", "cancelar", "cancelado"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return "".join(c for c in text if c.isalnum() or c.isspace()).strip()


class Service:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        whatsapp: WhatsAppClient,
        calendar: Optional[CalendarClient] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.s = settings
        self.db = db
        self.wa = whatsapp
        self.cal = calendar
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.tz = ZoneInfo(settings.timezone)

    # ------------------------------------------------------------------ utilidades
    def now(self) -> datetime:
        return self.clock().astimezone(timezone.utc)

    def _row(self, appt_id: int):
        with self.db.connect() as c:
            return c.execute("SELECT * FROM appointments WHERE id=?", (appt_id,)).fetchone()

    def _set(self, appt_id: int, **cols) -> None:
        sets = ", ".join(f"{k}=?" for k in cols)
        with self.db.connect() as c:
            c.execute(f"UPDATE appointments SET {sets} WHERE id=?", (*cols.values(), appt_id))

    def _when(self, row) -> tuple[str, str]:
        local = _parse(row["starts_at"]).astimezone(self.tz)
        return f"{WEEKDAYS[local.weekday()]} {local:%d/%m}", f"{local:%H:%M}"

    def _to_dict(self, row) -> dict:
        local = _parse(row["starts_at"]).astimezone(self.tz)
        reason = None
        if row["delivery_problem"]:
            reason = row["delivery_problem"]
        elif row["no_response_alerted_at"] and row["status"] == "pending":
            reason = "no_response"
        return {
            "id": row["id"],
            "patient_name": row["patient_name"],
            "phone": "+" + row["phone"],
            "starts_at_local": local.strftime("%Y-%m-%dT%H:%M"),
            "status": row["status"],
            "reminder_week": row["r7_status"],
            "reminder_day": row["r1_status"],
            "delivery_problem": row["delivery_problem"],
            "needs_attention": reason is not None,
            "attention_reason": reason,
            "calendar_synced": (row["calendar_event_id"] is not None) if self.cal else None,
        }

    # ------------------------------------------------------------------ citas
    def create_appointment(self, patient_name: str, phone: str, starts_at: datetime) -> dict:
        """Registra una cita. Lanza ValueError (mensaje apto para el usuario) si los datos no sirven."""
        name = (patient_name or "").strip()
        if not name:
            raise ValueError("Falta el nombre del paciente.")
        phone_n = normalize_phone(phone, self.s.default_country_code)
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=self.tz)
        if starts_at.astimezone(timezone.utc) <= self.now():
            raise ValueError("La fecha y hora de la cita ya pasó.")

        with self.db.connect() as c:
            cur = c.execute(
                "INSERT INTO appointments (patient_name, phone, starts_at, created_at) VALUES (?,?,?,?)",
                (name, phone_n, _iso(starts_at), _iso(self.now())),
            )
            appt_id = cur.lastrowid

        self._create_calendar_event(appt_id, notify_on_error=True)
        self.process_appointment(appt_id)  # si la cita es en < 7 días, el aviso sale ya
        return self._to_dict(self._row(appt_id))

    def get_appointment(self, appt_id: int) -> Optional[dict]:
        row = self._row(appt_id)
        return self._to_dict(row) if row else None

    def list_appointments(self, include_past: bool = False) -> list[dict]:
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM appointments ORDER BY starts_at").fetchall()
        now = self.now()
        out = [r for r in rows if include_past or _parse(r["starts_at"]) >= now]
        return [self._to_dict(r) for r in out]

    def set_status(self, appt_id: int, status: str) -> Optional[dict]:
        """Cambio manual de estado por la Dra Laura o su asistente (p. ej. número sin WhatsApp)."""
        if status not in ("confirmed", "cancelled"):
            raise ValueError("Estado no válido.")
        if self._row(appt_id) is None:
            return None
        self._set(appt_id, status=status)
        return self.get_appointment(appt_id)

    # ------------------------------------------------------------------ Google Calendar
    def _create_calendar_event(self, appt_id: int, notify_on_error: bool) -> None:
        if self.cal is None:
            return
        row = self._row(appt_id)
        start = _parse(row["starts_at"]).astimezone(self.tz)
        try:
            event_id = self.cal.create_event(
                summary=f"Cita de {row['patient_name']}",
                start=start,
                end=start + timedelta(minutes=self.s.appointment_minutes),
                description=f"Teléfono: +{row['phone']}",
            )
            self._set(appt_id, calendar_event_id=event_id)
        except Exception:  # noqa: BLE001 - un fallo de Google no debe perder la cita
            log.exception("No se pudo crear el evento de calendario (cita %s)", appt_id)
            if notify_on_error:
                fecha, hora = self._when(row)
                self.notify(
                    "calendar_error",
                    "No se creó el evento en Google Calendar",
                    f"La cita de {row['patient_name']} ({fecha} {hora}) quedó guardada, "
                    "pero no se pudo agregar al calendario. Se reintentará automáticamente.",
                    appt_id,
                )

    # ------------------------------------------------------------------ notificaciones
    def notify(self, kind: str, title: str, body: str, appointment_id: Optional[int] = None) -> int:
        """Guarda el aviso en el panel y lo envía por WhatsApp a la Dra Laura y su asistente."""
        with self.db.connect() as c:
            cur = c.execute(
                "INSERT INTO notifications (kind, title, body, appointment_id, created_at) VALUES (?,?,?,?,?)",
                (kind, title, body, appointment_id, _iso(self.now())),
            )
            notif_id = cur.lastrowid
        for phone in self.s.staff_phones:
            try:
                self.wa.send_template(phone, self.s.wa_template_staff, [f"{title}: {body}"])
            except Exception:  # noqa: BLE001
                log.exception("No se pudo avisar por WhatsApp a %s", phone)
        return notif_id

    def list_notifications(self) -> list[dict]:
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM notifications ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def delete_notification(self, notif_id: int) -> bool:
        with self.db.connect() as c:
            return c.execute("DELETE FROM notifications WHERE id=?", (notif_id,)).rowcount > 0

    def clear_notifications(self) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM notifications")

    # ------------------------------------------------------------------ recordatorios
    def tick(self) -> None:
        """Se ejecuta periódicamente (cada minuto) desde el scheduler."""
        with self.db.connect() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM appointments WHERE status != 'cancelled'")]
        for appt_id in ids:
            try:
                row = self._row(appt_id)
                if self.cal is not None and row["calendar_event_id"] is None:
                    self._create_calendar_event(appt_id, notify_on_error=False)
                self.process_appointment(appt_id)
            except Exception:  # noqa: BLE001 - una cita con problemas no frena a las demás
                log.exception("Error procesando la cita %s", appt_id)
        try:
            self._check_no_response()
        except Exception:  # noqa: BLE001
            log.exception("Error revisando pacientes sin respuesta")

    def process_appointment(self, appt_id: int) -> None:
        now = self.now()
        row = self._row(appt_id)
        if row is None or row["status"] == "cancelled":
            return
        start = _parse(row["starts_at"])
        if start <= now or row["delivery_problem"] == "not_on_whatsapp":
            return
        hour = now.astimezone(self.tz).hour
        if not (self.s.send_window_start <= hour < self.s.send_window_end):
            return  # no se manda de madrugada; sale en el primer tick dentro de la franja

        first_due = start - timedelta(days=self.s.first_reminder_days)
        last_due = start - timedelta(days=self.s.last_reminder_days)

        if row["r7_status"] == "pending" and now >= first_due:
            if now >= last_due:
                # Cita cargada con menos de 1 día de margen: solo tiene sentido el recordatorio final.
                self._set(appt_id, r7_status="skipped")
            else:
                self._send_reminder(appt_id, "r7")

        row = self._row(appt_id)
        if (
            row["r1_status"] == "pending"
            and row["status"] != "cancelled"
            and row["delivery_problem"] != "not_on_whatsapp"
            and now >= last_due
        ):
            self._send_reminder(appt_id, "r1")

    def _send_reminder(self, appt_id: int, kind: str) -> None:
        row = self._row(appt_id)
        fecha, hora = self._when(row)
        if kind == "r7":
            res = self.wa.send_template(
                row["phone"],
                self.s.wa_template_first,
                [row["patient_name"], fecha, hora],
                buttons=[f"confirm:{appt_id}", f"cancel:{appt_id}"],
            )
        else:
            res = self.wa.send_template(row["phone"], self.s.wa_template_last, [row["patient_name"]])

        if res.status == "sent":
            self._set(appt_id, **{f"{kind}_status": "sent", f"{kind}_sent_at": _iso(self.now())})
            with self.db.connect() as c:
                c.execute(
                    "INSERT INTO messages (appointment_id, kind, wamid) VALUES (?,?,?)",
                    (appt_id, kind, res.message_id),
                )
        elif res.status == "not_on_whatsapp":
            self._mark_not_on_whatsapp(appt_id)
        else:
            attempts = row[f"{kind}_attempts"] + 1
            if attempts >= self.s.max_send_attempts:
                self._set(appt_id, **{f"{kind}_status": "failed", f"{kind}_attempts": attempts},
                          delivery_problem="send_failed")
                self.notify(
                    "send_failed",
                    "No se pudo enviar el recordatorio",
                    f"No se pudo enviar el WhatsApp a {row['patient_name']} para la cita del {fecha} a las "
                    f"{hora} ({res.detail or 'error desconocido'}). Contáctalo de forma manual.",
                    appt_id,
                )
            else:
                self._set(appt_id, **{f"{kind}_attempts": attempts})  # se reintenta en el próximo tick

    def _mark_not_on_whatsapp(self, appt_id: int) -> None:
        row = self._row(appt_id)
        if row["delivery_problem"] == "not_on_whatsapp":
            return
        updates = {"delivery_problem": "not_on_whatsapp"}
        for kind in ("r7", "r1"):
            if row[f"{kind}_status"] == "pending":
                updates[f"{kind}_status"] = "failed"
        self._set(appt_id, **updates)
        fecha, hora = self._when(row)
        self.notify(
            "not_on_whatsapp",
            "Número sin WhatsApp",
            f"El número de {row['patient_name']} (+{row['phone']}) no tiene WhatsApp o no recibe "
            f"mensajes. Contáctalo de forma manual para la cita del {fecha} a las {hora}.",
            appt_id,
        )

    def _check_no_response(self) -> None:
        now = self.now()
        with self.db.connect() as c:
            rows = c.execute(
                "SELECT * FROM appointments WHERE status='pending' AND r7_status='sent' "
                "AND no_response_alerted_at IS NULL"
            ).fetchall()
        for row in rows:
            sent_at = _parse(row["r7_sent_at"])
            if now >= sent_at + timedelta(days=self.s.no_response_days) and _parse(row["starts_at"]) > now:
                self._set(row["id"], no_response_alerted_at=_iso(now))
                fecha, hora = self._when(row)
                self.notify(
                    "no_response",
                    "Paciente sin respuesta",
                    f"{row['patient_name']} no confirmó ni canceló su cita del {fecha} a las {hora} "
                    f"en {self.s.no_response_days} días. Revisa qué hacer.",
                    row["id"],
                )

    # ------------------------------------------------------------------ entradas de WhatsApp
    def handle_patient_reply(
        self, phone: str, payload: Optional[str] = None, text: Optional[str] = None
    ) -> Optional[dict]:
        """Procesa la respuesta de un paciente (botón o texto). Devuelve la cita si hubo cambio."""
        row, action = None, None

        if payload and ":" in payload:
            act, _, raw_id = payload.partition(":")
            if act in ("confirm", "cancel") and raw_id.isdigit():
                candidate = self._row(int(raw_id))
                if candidate is not None and same_phone(candidate["phone"], phone):
                    row, action = candidate, act

        if row is None and text:
            word = _plain(text)
            action = "confirm" if word in CONFIRM_WORDS else "cancel" if word in CANCEL_WORDS else None
            if action:
                row = self._latest_open_for_phone(phone)

        if row is None or action is None:
            return None

        new_status = "confirmed" if action == "confirm" else "cancelled"
        if row["status"] == "cancelled" or row["status"] == new_status or _parse(row["starts_at"]) <= self.now():
            return None  # repetido o fuera de tiempo: no se vuelve a avisar

        self._set(row["id"], status=new_status)
        fecha, hora = self._when(row)
        if new_status == "confirmed":
            self.notify("confirmed", "Cita confirmada",
                        f"{row['patient_name']} confirmó su cita del {fecha} a las {hora}.", row["id"])
        else:
            self.notify("cancelled", "Cita cancelada",
                        f"{row['patient_name']} canceló su cita del {fecha} a las {hora}.", row["id"])
        return self.get_appointment(row["id"])

    def _latest_open_for_phone(self, phone: str):
        now = self.now()
        with self.db.connect() as c:
            rows = c.execute(
                "SELECT * FROM appointments WHERE status IN ('pending','confirmed') "
                "AND r7_status='sent' ORDER BY starts_at"
            ).fetchall()
        for row in rows:
            if _parse(row["starts_at"]) > now and same_phone(row["phone"], phone):
                return row
        return None

    def handle_delivery_status(self, wamid: Optional[str], status: str, error_code: Optional[int]) -> None:
        """Estado de entrega informado por Meta (la API acepta el envío y luego avisa si falló)."""
        if not wamid or status != "failed":
            return
        with self.db.connect() as c:
            msg = c.execute("SELECT * FROM messages WHERE wamid=?", (wamid,)).fetchone()
            if msg is None:
                return  # p. ej. mensaje a la Dra Laura o al asistente
            c.execute("UPDATE messages SET status='failed' WHERE id=?", (msg["id"],))
        appt_id = msg["appointment_id"]
        self._set(appt_id, **{f"{msg['kind']}_status": "failed"})
        if error_code in NOT_ON_WHATSAPP_CODES:
            self._mark_not_on_whatsapp(appt_id)
        else:
            row = self._row(appt_id)
            fecha, hora = self._when(row)
            self._set(appt_id, delivery_problem="send_failed")
            self.notify(
                "send_failed",
                "No se pudo entregar el recordatorio",
                f"WhatsApp no pudo entregar el mensaje a {row['patient_name']} (cita del {fecha} a las "
                f"{hora}). Contáctalo de forma manual.",
                appt_id,
            )
