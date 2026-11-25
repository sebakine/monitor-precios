"""Saca el precio de una página HTML o de una respuesta JSON."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from bs4 import BeautifulSoup

from monitor_precios.config import Producto
from monitor_precios.precios import PrecioInvalido, parsear_precio


class PrecioNoEncontrado(LookupError):
    """La página respondió, pero el precio no está donde se esperaba."""


def precio_desde_html(html: str, producto: Producto) -> Decimal:
    assert producto.selector is not None
    sopa = BeautifulSoup(html, "html.parser")
    nodo = sopa.select_one(producto.selector)
    if nodo is None:
        raise PrecioNoEncontrado(f"el selector {producto.selector!r} no encontró nada")
    texto = nodo.get("content") or nodo.get_text(" ", strip=True)
    try:
        return parsear_precio(str(texto), producto.decimal)
    except PrecioInvalido as exc:
        raise PrecioNoEncontrado(str(exc)) from exc


def precio_desde_json(texto: str, producto: Producto) -> Decimal:
    assert producto.ruta is not None
    try:
        valor: Any = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise PrecioNoEncontrado(f"la respuesta no es JSON: {exc}") from exc
    for paso in producto.ruta.split("."):
        try:
            valor = valor[int(paso)] if isinstance(valor, list) else valor[paso]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise PrecioNoEncontrado(f"la ruta {producto.ruta!r} no existe ({paso!r})") from exc
    if isinstance(valor, bool):
        raise PrecioNoEncontrado(f"la ruta {producto.ruta!r} no apunta a un número")
    if isinstance(valor, int | float):
        return Decimal(str(valor))
    if isinstance(valor, str):
        try:
            return parsear_precio(valor, producto.decimal)
        except PrecioInvalido as exc:
            raise PrecioNoEncontrado(str(exc)) from exc
    raise PrecioNoEncontrado(f"la ruta {producto.ruta!r} no apunta a un número")
