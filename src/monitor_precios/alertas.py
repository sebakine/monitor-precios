"""Reglas de alerta y envío por Telegram."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from monitor_precios.config import Producto

log = logging.getLogger(__name__)

TipoAlerta = Literal["bajo_umbral", "baja"]


@dataclass(frozen=True, slots=True)
class Alerta:
    producto: Producto
    tipo: TipoAlerta
    anterior: Decimal | None
    actual: Decimal

    @property
    def mensaje(self) -> str:
        p = self.producto
        actual = formatear(self.actual, p.moneda)
        if self.tipo == "bajo_umbral":
            assert p.umbral is not None
            texto = f"{p.nombre} bajó a {actual} (umbral {formatear(p.umbral, p.moneda)})"
        else:
            assert self.anterior is not None
            pct = (self.anterior - self.actual) / self.anterior * 100
            texto = (
                f"{p.nombre} bajó {pct:.1f} %: de {formatear(self.anterior, p.moneda)} a {actual}"
            )
        return f"{texto}\n{p.url}"


def evaluar(producto: Producto, anterior: Decimal | None, actual: Decimal) -> list[Alerta]:
    """Decide qué alertas genera un precio nuevo.

    El aviso por umbral se envía solo al cruzarlo, no en cada consulta mientras el
    precio siga abajo; así una oferta que dura una semana no llena el chat.
    """
    alertas: list[Alerta] = []
    umbral = producto.umbral
    if umbral is not None and actual <= umbral and (anterior is None or anterior > umbral):
        alertas.append(Alerta(producto, "bajo_umbral", anterior, actual))
    baja_pct = producto.baja_pct
    if (
        baja_pct is not None
        and anterior is not None
        and actual < anterior
        and (anterior - actual) / anterior * 100 >= baja_pct
    ):
        alertas.append(Alerta(producto, "baja", anterior, actual))
    return alertas


def formatear(valor: Decimal, moneda: str) -> str:
    """Formato legible: "$12.990" para CLP, "£51,77" para el resto."""
    if moneda == "CLP" and valor == valor.to_integral_value():
        return "$" + f"{int(valor):,}".replace(",", ".")
    simbolo = {"CLP": "$", "USD": "US$", "EUR": "€", "GBP": "£"}.get(moneda, moneda + " ")
    texto = f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{simbolo}{texto}"


class Notificador(Protocol):
    async def enviar(self, texto: str) -> None: ...


class Consola:
    """Se usa cuando no hay credenciales de Telegram: deja el aviso en el log."""

    async def enviar(self, texto: str) -> None:
        log.info("alerta (sin Telegram configurado): %s", texto.replace("\n", " | "))


class Telegram:
    API = "https://api.telegram.org"

    def __init__(
        self,
        token: str,
        chat_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
        espera_reintento: float = 1.0,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._transport = transport
        self._espera = espera_reintento

    async def enviar(self, texto: str) -> None:
        async with httpx.AsyncClient(timeout=15, transport=self._transport) as client:
            async for intento in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=self._espera, max=10),
                retry=retry_if_exception_type(httpx.TransportError),
                reraise=True,
            ):
                with intento:
                    respuesta = await client.post(
                        f"{self.API}/bot{self._token}/sendMessage",
                        json={
                            "chat_id": self._chat_id,
                            "text": texto,
                            "disable_web_page_preview": True,
                        },
                    )
            if respuesta.is_error:
                # No se incluye la URL en el error porque contiene el token.
                descripcion = respuesta.json().get("description", "") if _es_json(respuesta) else ""
                raise RuntimeError(f"Telegram respondió {respuesta.status_code} {descripcion}")


def _es_json(respuesta: httpx.Response) -> bool:
    return str(respuesta.headers.get("content-type", "")).startswith("application/json")


def desde_entorno() -> Notificador:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token and chat_id:
        return Telegram(token, chat_id)
    return Consola()
