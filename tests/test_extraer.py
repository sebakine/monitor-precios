import json
from decimal import Decimal

import pytest

from monitor_precios.config import desde_dict
from monitor_precios.extraer import PrecioNoEncontrado, precio_desde_html, precio_desde_json

from .conftest import dolar, libro


def _p(datos: dict[str, object]):  # type: ignore[no-untyped-def]
    return desde_dict({"producto": [datos]}).productos[0]


def test_precio_de_una_pagina_real_de_books_toscrape(sapiens_html: str) -> None:
    # La página tiene otros precios (productos relacionados); el selector toma el principal.
    assert precio_desde_html(sapiens_html, _p(libro())) == Decimal("54.23")


def test_usa_el_atributo_content_si_existe() -> None:
    html = '<span itemprop="price" content="12990">$12.990 oferta</span>'
    assert precio_desde_html(html, _p(libro(selector="[itemprop=price]"))) == 12990


def test_selector_que_no_encuentra_nada(sapiens_html: str) -> None:
    with pytest.raises(PrecioNoEncontrado, match="no encontró nada"):
        precio_desde_html(sapiens_html, _p(libro(selector=".no-existe")))


def test_selector_que_apunta_a_texto_sin_precio(sapiens_html: str) -> None:
    with pytest.raises(PrecioNoEncontrado, match="no hay un número"):
        precio_desde_html(sapiens_html, _p(libro(selector=".product_main h1")))


def test_precio_desde_json_real(dolar_json: str) -> None:
    assert precio_desde_json(dolar_json, _p(dolar())) == Decimal("1003.21")


@pytest.mark.parametrize(
    ("cuerpo", "ruta", "esperado"),
    [
        ({"precio": "$12.990"}, "precio", Decimal(12990)),
        ({"a": [{"b": 5}]}, "a.0.b", Decimal(5)),
        ({"a": {"0": 7.5}}, "a.0", Decimal("7.5")),
    ],
)
def test_rutas_json(cuerpo: object, ruta: str, esperado: Decimal) -> None:
    assert precio_desde_json(json.dumps(cuerpo), _p(dolar(ruta=ruta))) == esperado


@pytest.mark.parametrize(
    ("texto", "ruta", "mensaje"),
    [
        ("<html>", "a", "no es JSON"),
        ('{"a": 1}', "b", "no existe"),
        ('{"a": [1]}', "a.3", "no existe"),
        ('{"a": [1]}', "a.x", "no existe"),
        ('{"a": true}', "a", "no apunta a un número"),
        ('{"a": null}', "a", "no apunta a un número"),
        ('{"a": "gratis"}', "a", "no hay un número"),
    ],
)
def test_errores_json(texto: str, ruta: str, mensaje: str) -> None:
    with pytest.raises(PrecioNoEncontrado, match=mensaje):
        precio_desde_json(texto, _p(dolar(ruta=ruta)))
