"""Convierte textos de precio ("$12.990", "£51.77", "1.299,90 €") en Decimal."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal

Separador = Literal[".", ","]

_NUMERO = re.compile(r"\d[\d.,\s  ']*")


class PrecioInvalido(ValueError):
    """El texto no contiene un precio reconocible."""


def parsear_precio(texto: str, decimal: Separador | None = None) -> Decimal:
    """Extrae el primer número del texto y lo interpreta como precio.

    ``decimal`` fija el separador decimal cuando el sitio es conocido. Sin él se
    usa una heurística: si aparecen ambos separadores, el último es el decimal;
    si aparece uno solo seguido de exactamente tres dígitos, se toma como
    separador de miles (el caso típico en Chile, "$12.990").
    """
    coincidencia = _NUMERO.search(texto)
    if coincidencia is None:
        raise PrecioInvalido(f"no hay un número en {texto!r}")
    if "-" in texto[: coincidencia.start()].replace(" ", "")[-2:]:
        raise PrecioInvalido(f"precio negativo: {texto!r}")
    bruto = re.sub(r"[\s  ']", "", coincidencia.group()).rstrip(".,")

    sep = decimal or _adivinar_decimal(bruto)
    if sep is None:
        normalizado = bruto.replace(".", "").replace(",", "")
    else:
        miles = "," if sep == "." else "."
        entero, _, fraccion = bruto.rpartition(sep) if sep in bruto else (bruto, "", "")
        normalizado = entero.replace(miles, "").replace(sep, "")
        if fraccion:
            normalizado += "." + fraccion
    try:
        return Decimal(normalizado)
    except InvalidOperation as exc:  # pragma: no cover - el regex ya filtra
        raise PrecioInvalido(f"no se pudo interpretar {texto!r}") from exc


def _adivinar_decimal(numero: str) -> Separador | None:
    tiene_punto, tiene_coma = "." in numero, "," in numero
    if tiene_punto and tiene_coma:
        return "." if numero.rfind(".") > numero.rfind(",") else ","
    if not (tiene_punto or tiene_coma):
        return None
    sep: Separador = "." if tiene_punto else ","
    partes = numero.split(sep)
    if len(partes) > 2 or len(partes[-1]) == 3:
        return None  # solo separadores de miles
    return sep
