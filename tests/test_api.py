"""Capa HTTP (FastAPI). Requiere `pip install -r requirements-dev.txt` (fastapi + httpx).

Si esas librerías no están instaladas, el archivo se omite en lugar de fallar.
"""
import base64
import hashlib
import hmac
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

from .helpers import PATIENT, PATIENT_INPUT, STAFF, FakeCalendar, FakeClock, FakeWhatsApp, inbound_button  # noqa: E402


def _basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


@pytest.fixture
def api(settings, clock, wa, cal):
    app = create_app(settings, whatsapp=wa, calendar=cal, clock=clock, start_scheduler=False)
    client = TestClient(app, headers=_basic("laura", "clave-laura"))
    client.service = app.state.service
    return client


def test_el_panel_exige_usuario_y_clave(api):
    anon = TestClient(api.app)
    assert anon.get("/api/appointments").status_code == 401
    assert anon.get("/").status_code == 401
    assert anon.get("/api/appointments", headers=_basic("laura", "mal")).status_code == 401
    assert api.get("/api/appointments").status_code == 200
    assert TestClient(api.app, headers=_basic("asistente", "clave-asistente")).get("/").status_code == 200


def test_sin_usuarios_configurados_la_app_no_arranca(settings, wa):
    settings.panel_users = {}
    with pytest.raises(RuntimeError):
        create_app(settings, whatsapp=wa, start_scheduler=False)


def test_crear_cita_por_la_api_crea_el_evento_de_calendar(api, cal):
    r = api.post("/api/appointments",
                 json={"patient_name": "María Pérez", "phone": PATIENT_INPUT, "starts_at": "2026-11-20T10:00"})
    assert r.status_code == 201
    assert r.json()["starts_at_local"] == "2026-11-20T10:00"
    assert cal.events[0]["summary"] == "Cita de María Pérez"


def test_numero_invalido_devuelve_error_claro(api):
    r = api.post("/api/appointments",
                 json={"patient_name": "Ana", "phone": "abc", "starts_at": "2026-11-20T10:00"})
    assert r.status_code == 422
    assert "teléfono" in r.json()["detail"].lower()
    assert api.get("/api/appointments").json() == []


def test_notificaciones_se_listan_y_se_eliminan(api, clock, wa):
    api.post("/api/appointments",
             json={"patient_name": "María Pérez", "phone": PATIENT_INPUT, "starts_at": "2026-11-20T10:00"})
    clock.set("2026-11-13 10:00")
    api.service.tick()
    payload = wa.to_patient()[0]["buttons"][0]
    assert api.post("/webhook/whatsapp", json=inbound_button(PATIENT, payload)).status_code == 200

    notes = api.get("/api/notifications").json()
    assert [n["kind"] for n in notes] == ["confirmed"]
    assert api.delete(f"/api/notifications/{notes[0]['id']}").status_code == 204
    assert api.get("/api/notifications").json() == []
    assert api.delete("/api/notifications/999").status_code == 404


def test_cambio_manual_de_estado(api):
    created = api.post("/api/appointments",
                       json={"patient_name": "Ana", "phone": PATIENT_INPUT, "starts_at": "2026-11-20T10:00"}).json()
    r = api.post(f"/api/appointments/{created['id']}/status", json={"status": "confirmed"})
    assert r.json()["status"] == "confirmed"
    assert api.post("/api/appointments/999/status", json={"status": "cancelled"}).status_code == 404
    assert api.post(f"/api/appointments/{created['id']}/status", json={"status": "otro"}).status_code == 422


def test_verificacion_del_webhook_de_meta(settings, wa):
    settings.wa_verify_token = "secreto-meta"
    client = TestClient(create_app(settings, whatsapp=wa, start_scheduler=False))
    ok = client.get("/webhook/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "secreto-meta", "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/webhook/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "12345"})
    assert bad.status_code == 403


def test_webhook_rechaza_firma_invalida_cuando_hay_app_secret(settings, wa):
    settings.wa_app_secret = "app-secret"
    client = TestClient(create_app(settings, whatsapp=wa, start_scheduler=False))
    body = json.dumps({"entry": []}).encode()
    good = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

    assert client.post("/webhook/whatsapp", content=body).status_code == 403
    assert client.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": "sha256=00"}).status_code == 403
    assert client.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": good}).status_code == 200
