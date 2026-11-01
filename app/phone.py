"""Validación y normalización de teléfonos (formato internacional, solo dígitos)."""
from __future__ import annotations

import re


def digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def same_phone(a: str, b: str) -> bool:
    """Compara por los últimos 10 dígitos.

    WhatsApp puede informar un mismo móvil argentino como 549... o 54..., así que
    comparar el número completo daría falsos negativos.
    """
    da, db = digits(a), digits(b)
    return bool(da) and bool(db) and da[-10:] == db[-10:]


def normalize_phone(raw: str, default_cc: str = "54") -> str:
    """Devuelve el número como dígitos con código de país (ej. 5491155551234).

    Lanza ValueError con un mensaje apto para mostrar al usuario si no parece un teléfono.
    """
    if not raw or not raw.strip():
        raise ValueError("Falta el teléfono del paciente.")
    text = raw.strip()
    if re.search(r"[A-Za-z]", text):
        raise ValueError("El teléfono solo puede tener números, espacios, guiones y '+'.")
    d = digits(text)
    if text.startswith("+"):
        pass
    elif d.startswith("00"):
        d = d[2:]
    elif d.startswith(default_cc) and len(d) >= 11:
        pass  # ya trae el código de país
    else:
        d = default_cc + d.lstrip("0")
    if not 8 <= len(d) <= 15:
        raise ValueError(
            "El teléfono no es válido. Escríbelo con código de país, "
            "por ejemplo +54 9 11 5555 1234."
        )
    return d
