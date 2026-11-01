"""Cliente de Google Calendar (cuenta de servicio).

Los imports de Google son perezosos: el resto del sistema (y los tests) funcionan sin
tener instaladas esas librerías.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol


class CalendarClient(Protocol):
    def create_event(
        self, summary: str, start: datetime, end: datetime, description: Optional[str] = None
    ) -> str: ...


class GoogleCalendar:
    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, calendar_id: str, credentials_file: str):
        self.calendar_id = calendar_id
        self.credentials_file = credentials_file
        self._service = None

    def _get_service(self):
        if self._service is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_file(
                self.credentials_file, scopes=self.SCOPES
            )
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def create_event(self, summary, start, end, description=None) -> str:
        event = {
            "summary": summary,
            "start": {"dateTime": start.isoformat(), "timeZone": str(start.tzinfo)},
            "end": {"dateTime": end.isoformat(), "timeZone": str(end.tzinfo)},
        }
        if description:
            event["description"] = description
        created = self._get_service().events().insert(calendarId=self.calendar_id, body=event).execute()
        return created["id"]
