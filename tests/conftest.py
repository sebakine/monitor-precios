from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from monitor_precios.config import Config, desde_dict

FIXTURES = Path(__file__).parent / "fixtures"
SAPIENS_URL = "https://books.toscrape.com/catalogue/sapiens_996/index.html"
DOLAR_URL = "https://mindicador.cl/api/dolar"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def sapiens_html() -> str:
    return (FIXTURES / "sapiens.html").read_text(encoding="utf-8")


@pytest.fixture
def dolar_json() -> str:
    return (FIXTURES / "mindicador_dolar.json").read_text(encoding="utf-8")


def hacer_config(*productos: dict[str, Any], **ajustes: Any) -> Config:
    base = {"espera_por_sitio": 0, "intentos": 3, **ajustes}
    return desde_dict({"ajustes": base, "producto": list(productos)})


def libro(**extra: Any) -> dict[str, Any]:
    return {
        "id": "sapiens",
        "nombre": "Sapiens",
        "url": SAPIENS_URL,
        "selector": ".product_main .price_color",
        "moneda": "GBP",
        **extra,
    }


def dolar(**extra: Any) -> dict[str, Any]:
    return {
        "id": "dolar",
        "nombre": "Dólar",
        "url": DOLAR_URL,
        "tipo": "json",
        "ruta": "serie.0.valor",
        **extra,
    }


Respuesta = httpx.Response | Callable[[httpx.Request], httpx.Response] | Exception


@dataclass
class Sitio:
    """Servidor falso: rutas por URL completa, con registro de peticiones.

    Una ruta puede tener una lista de respuestas, que se entregan en orden
    (la última se repite). Sin robots.txt definido, responde 404 (todo permitido).
    """

    rutas: dict[str, list[Respuesta]] = field(default_factory=dict)
    pedidas: list[str] = field(default_factory=list)

    def agregar(self, url: str, *respuestas: Respuesta) -> Sitio:
        self.rutas[url] = list(respuestas)
        return self

    def html(self, url: str, cuerpo: str, status: int = 200) -> Sitio:
        return self.agregar(url, httpx.Response(status, text=cuerpo))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.pedidas.append(url)
        cola = self.rutas.get(url)
        if cola is None:
            return httpx.Response(404, text="no encontrado")
        respuesta = cola.pop(0) if len(cola) > 1 else cola[0]
        if isinstance(respuesta, Exception):
            raise respuesta
        if callable(respuesta) and not isinstance(respuesta, httpx.Response):
            return respuesta(request)
        return respuesta

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def veces(self, url: str) -> int:
        return self.pedidas.count(url)


@pytest.fixture
def sitio(sapiens_html: str, dolar_json: str) -> Sitio:
    return (
        Sitio()
        .html(SAPIENS_URL, sapiens_html)
        .agregar(DOLAR_URL, httpx.Response(200, text=dolar_json))
    )
