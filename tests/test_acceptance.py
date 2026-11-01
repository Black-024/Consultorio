"""Criterios de aceptación del spec (uno o más tests por criterio).

Fecha base de los tests: lunes 2026-11-02 12:00 (hora de Buenos Aires).
La cita de ejemplo es el viernes 2026-11-20 a las 10:00, así que:
  recordatorio de 1 semana -> viernes 2026-11-13 10:00
  recordatorio de 1 día    -> jueves  2026-11-19 10:00
"""
import pytest

from app import webhook
from app.phone import normalize_phone, same_phone

from .helpers import (
    PATIENT,
    PATIENT_INPUT,
    STAFF,
    TZ,
    inbound_button,
    inbound_text,
    local,
    status_failed,
)

APPT = "2026-11-20 10:00"


def book(service, when=APPT, name="María Pérez", phone=PATIENT_INPUT):
    return service.create_appointment(name, phone, local(when))


def kinds(service):
    return [n["kind"] for n in service.list_notifications()]


# =============================================================================================
# Criterio 1: al anotar una cita, llegan los recordatorios una semana y un día antes
# =============================================================================================
def test_criterio1_recordatorios_una_semana_y_un_dia_antes(service, clock, wa):
    appt = book(service)
    assert wa.to_patient() == []  # al anotarla no se manda nada: faltan más de 7 días

    clock.set("2026-11-13 09:59")
    service.tick()
    assert wa.to_patient() == []  # todavía falta un minuto para la semana

    clock.set("2026-11-13 10:00")
    service.tick()
    first = wa.to_patient()
    assert len(first) == 1
    assert first[0]["template"] == "recordatorio_cita_7d"
    assert first[0]["params"] == ["María Pérez", "viernes 20/11", "10:00"]
    # el aviso de 1 semana permite confirmar o cancelar
    assert first[0]["buttons"] == [f"confirm:{appt['id']}", f"cancel:{appt['id']}"]

    service.tick()
    service.tick()
    assert len(wa.to_patient()) == 1  # no se duplica

    clock.set("2026-11-19 10:00")
    service.tick()
    sent = wa.to_patient()
    assert len(sent) == 2
    assert sent[1]["template"] == "recordatorio_cita_1d"
    assert sent[1]["params"] == ["María Pérez"]
    assert sent[1]["buttons"] is None  # el de 1 día NO permite confirmar ni cancelar

    clock.set("2026-11-20 08:00")
    service.tick()
    assert len(wa.to_patient()) == 2  # y no hay más


def test_criterio1_cita_cancelada_no_recibe_recordatorio_de_un_dia(service, clock, wa):
    book(service)
    clock.set("2026-11-13 10:00")
    service.tick()
    button = wa.to_patient()[0]["buttons"][1]  # "cancel:<id>"
    webhook.dispatch(service, inbound_button(PATIENT, button, "Cancelar"))

    clock.set("2026-11-19 10:00")
    service.tick()
    assert [m["template"] for m in wa.to_patient()] == ["recordatorio_cita_7d"]


def test_criterio1_cita_dentro_de_la_semana_envia_el_primer_aviso_enseguida(service, wa):
    book(service, "2026-11-05 10:00")  # faltan 3 días
    sent = wa.to_patient()
    assert [m["template"] for m in sent] == ["recordatorio_cita_7d"]


def test_criterio1_cita_con_menos_de_un_dia_solo_recibe_el_recordatorio_final(service, wa):
    book(service, "2026-11-03 10:00")  # faltan 22 h
    assert [m["template"] for m in wa.to_patient()] == ["recordatorio_cita_1d"]


def test_criterio1_no_se_envia_de_madrugada(service, clock, wa):
    book(service, "2026-11-20 23:00")
    clock.set("2026-11-13 23:00")  # fuera de la franja 08:00-21:00
    service.tick()
    assert wa.to_patient() == []
    clock.set("2026-11-14 08:00")  # primer tick dentro de la franja
    service.tick()
    assert len(wa.to_patient()) == 1


# =============================================================================================
# Criterio 2: confirmar/cancelar notifica a la Dra Laura y a su asistente
# =============================================================================================
@pytest.mark.parametrize(
    "button_index, status, kind, text",
    [(0, "confirmed", "confirmed", "confirmó"), (1, "cancelled", "cancelled", "canceló")],
)
def test_criterio2_respuesta_del_paciente_avisa_a_laura_y_asistente(
    service, clock, wa, button_index, status, kind, text
):
    appt = book(service)
    clock.set("2026-11-13 10:00")
    service.tick()
    payload = wa.to_patient()[0]["buttons"][button_index]
    before = len(wa.to_staff())

    webhook.dispatch(service, inbound_button(PATIENT, payload))

    assert service.get_appointment(appt["id"])["status"] == status
    # panel de notificaciones
    notes = [n for n in service.list_notifications() if n["kind"] == kind]
    assert len(notes) == 1 and "María Pérez" in notes[0]["body"] and text in notes[0]["body"]
    # WhatsApp a las dos personas del consultorio
    new = wa.to_staff()[before:]
    assert sorted(m["to"] for m in new) == sorted(STAFF)
    assert all("María Pérez" in m["params"][0] and text in m["params"][0] for m in new)


def test_criterio2_respuesta_escrita_tambien_funciona_y_no_se_duplica(service, clock, wa):
    appt = book(service)
    clock.set("2026-11-13 10:00")
    service.tick()

    webhook.dispatch(service, inbound_text(PATIENT, "Sí"))
    webhook.dispatch(service, inbound_text(PATIENT, "si"))  # repetido (o reintento de Meta)

    assert service.get_appointment(appt["id"])["status"] == "confirmed"
    assert kinds(service).count("confirmed") == 1


def test_criterio2_respuesta_de_otro_telefono_se_ignora(service, clock, wa):
    appt = book(service)
    clock.set("2026-11-13 10:00")
    service.tick()
    payload = wa.to_patient()[0]["buttons"][0]

    webhook.dispatch(service, inbound_button("5491177770000", payload))

    assert service.get_appointment(appt["id"])["status"] == "pending"
    assert "confirmed" not in kinds(service)


# =============================================================================================
# Criterio 3: al agendar una cita se crea el evento "Cita de <paciente>" en Google Calendar
# =============================================================================================
def test_criterio3_evento_de_google_calendar_con_fecha_hora_y_nombre(service, cal):
    appt = book(service)

    assert len(cal.events) == 1
    event = cal.events[0]
    assert event["summary"] == "Cita de María Pérez"
    assert event["start"] == local(APPT)
    assert event["start"].astimezone(TZ).hour == 10
    assert event["end"] > event["start"]
    assert service.get_appointment(appt["id"])["calendar_synced"] is True


def test_criterio3_si_google_falla_la_cita_no_se_pierde_y_se_reintenta(service, clock, cal):
    cal.fail = True
    appt = book(service)

    assert service.get_appointment(appt["id"]) is not None
    assert service.get_appointment(appt["id"])["calendar_synced"] is False
    assert kinds(service) == ["calendar_error"]

    cal.fail = False
    clock.set("2026-11-02 12:01")
    service.tick()
    assert [e["summary"] for e in cal.events] == ["Cita de María Pérez"]
    assert service.get_appointment(appt["id"])["calendar_synced"] is True
    assert kinds(service) == ["calendar_error"]  # sin avisos repetidos


# =============================================================================================
# Criterio 4: si el número no existe o no está en WhatsApp, avisa
# =============================================================================================
@pytest.mark.parametrize("bad", ["", "   ", "abc123", "12", "+54 9 11 55"])
def test_criterio4_numero_invalido_se_rechaza_al_anotar_la_cita(service, bad):
    with pytest.raises(ValueError):
        book(service, phone=bad)
    assert service.list_appointments() == []


def test_criterio4_numero_sin_whatsapp_avisa_y_queda_para_gestion_manual(service, clock, wa):
    bad = "5491166660000"
    wa.not_on_whatsapp.add(bad)

    appt = book(service, "2026-11-05 10:00", name="Juan Gómez", phone="+54 9 11 6666-0000")

    assert kinds(service) == ["not_on_whatsapp"]
    note = service.list_notifications()[0]
    assert "Juan Gómez" in note["body"] and bad in note["body"]
    assert sorted(m["to"] for m in wa.to_staff()) == sorted(STAFF)  # se avisó a Laura y asistente
    current = service.get_appointment(appt["id"])
    assert current["needs_attention"] and current["attention_reason"] == "not_on_whatsapp"

    # no se vuelve a intentar ni a avisar: se gestiona de forma manual
    attempts = len(wa.to_patient(bad))
    clock.set("2026-11-04 10:00")
    service.tick()
    assert len(wa.to_patient(bad)) == attempts
    assert kinds(service) == ["not_on_whatsapp"]


def test_criterio4_meta_informa_el_fallo_despues_de_aceptar_el_envio(service, clock, wa):
    """La API de Meta suele aceptar el mensaje y avisar luego por webhook (código 131026)."""
    appt = book(service)
    clock.set("2026-11-13 10:00")
    service.tick()
    assert kinds(service) == []  # el envío fue aceptado

    webhook.dispatch(service, status_failed(wa.ids[0], 131026))

    assert kinds(service) == ["not_on_whatsapp"]
    assert service.get_appointment(appt["id"])["attention_reason"] == "not_on_whatsapp"
    webhook.dispatch(service, status_failed(wa.ids[0], 131026))  # reintento de Meta
    assert kinds(service) == ["not_on_whatsapp"]


def test_criterio4_errores_temporales_se_reintentan_y_luego_avisan(service, clock, wa):
    wa.errors = 3  # los 3 intentos al paciente fallan; los avisos al staff ya salen bien
    appt = book(service, "2026-11-05 10:00")  # intento 1 (al anotarla)
    assert kinds(service) == []
    service.tick()  # intento 2
    assert kinds(service) == []
    service.tick()  # intento 3 -> se rinde y avisa

    assert kinds(service) == ["send_failed"]
    assert service.get_appointment(appt["id"])["reminder_week"] == "failed"


# =============================================================================================
# Criterio 5: si el paciente no contesta, avisa (a los 5 días del primer recordatorio)
# =============================================================================================
def test_criterio5_paciente_sin_respuesta_en_5_dias_avisa_una_sola_vez(service, clock, wa):
    appt = book(service)
    clock.set("2026-11-13 10:00")
    service.tick()  # sale el recordatorio de 1 semana

    clock.set("2026-11-18 09:00")  # faltan 1 h para cumplirse los 5 días
    service.tick()
    assert "no_response" not in kinds(service)

    clock.set("2026-11-18 10:00")
    service.tick()
    assert kinds(service) == ["no_response"]
    assert "María Pérez" in service.list_notifications()[0]["body"]
    assert sorted(m["to"] for m in wa.to_staff()) == sorted(STAFF)
    assert service.get_appointment(appt["id"])["attention_reason"] == "no_response"

    service.tick()
    assert kinds(service) == ["no_response"]  # no se repite


def test_criterio5_si_el_paciente_confirma_no_hay_aviso_de_falta_de_respuesta(service, clock, wa):
    book(service)
    clock.set("2026-11-13 10:00")
    service.tick()
    webhook.dispatch(service, inbound_button(PATIENT, wa.to_patient()[0]["buttons"][0]))

    clock.set("2026-11-18 10:00")
    service.tick()
    assert "no_response" not in kinds(service)


# =============================================================================================
# Extras: panel de notificaciones y teléfonos
# =============================================================================================
def test_notificaciones_se_conservan_hasta_que_el_usuario_las_elimina(service, clock, wa):
    book(service)
    clock.set("2026-11-13 10:00")
    service.tick()
    webhook.dispatch(service, inbound_button(PATIENT, wa.to_patient()[0]["buttons"][0]))
    webhook.dispatch(service, inbound_button(PATIENT, wa.to_patient()[0]["buttons"][1]))

    notes = service.list_notifications()
    assert len(notes) == 2
    assert service.delete_notification(notes[0]["id"]) is True
    assert len(service.list_notifications()) == 1
    service.clear_notifications()
    assert service.list_notifications() == []


def test_cita_en_el_pasado_se_rechaza(service):
    with pytest.raises(ValueError):
        book(service, "2026-11-01 10:00")


def test_normalizacion_de_telefonos():
    assert normalize_phone("+54 9 11 5555-1234") == "5491155551234"
    assert normalize_phone("00 54 9 11 5555 1234") == "5491155551234"
    assert normalize_phone("549 11 5555 1234") == "5491155551234"
    assert normalize_phone("11 5555-1234") == "541155551234"  # sin país: se asume +54
    assert same_phone("5491155551234", "541155551234")  # Meta puede informar el 9 o no
    assert not same_phone("5491155551234", "5491155559999")
