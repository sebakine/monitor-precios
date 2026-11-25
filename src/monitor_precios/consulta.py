"""Consulta todos los productos en paralelo, con cortesía por sitio y reintentos."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from monitor_precios.config import Config, Producto
from monitor_precios.cortesia import LimitePorSitio, Robots
from monitor_precios.extraer import precio_desde_html, precio_desde_json
from monitor_precios.navegador import Chromium, Renderizador

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Consulta:
    producto: Producto
    consultado_en: datetime
    precio: Decimal | None
    error: str | None
    duracion_ms: int

    @property
    def ok(self) -> bool:
        return self.error is None


class BloqueadoPorRobots(PermissionError):
    pass


def _reintentable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        codigo = exc.response.status_code
        return codigo == 429 or codigo >= 500
    return False


async def consultar(
    config: Config,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    renderizador: Renderizador | None = None,
    espera_reintento: float = 1.0,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[Consulta]:
    """Consulta cada producto una vez y devuelve los resultados en el orden de la config.

    Un producto que falla nunca detiene a los demás: su error queda en la consulta.
    """
    ajustes = config.ajustes
    async with (
        httpx.AsyncClient(
            headers={"User-Agent": ajustes.user_agent},
            timeout=ajustes.timeout,
            follow_redirects=True,
            transport=transport,
        ) as client,
        _navegador(config, renderizador) as navegador,
    ):
        robots = Robots(client, ajustes.user_agent)
        limite = LimitePorSitio(ajustes.espera_por_sitio, dormir=dormir)
        cupos = asyncio.Semaphore(ajustes.concurrencia)

        async def una(producto: Producto) -> Consulta:
            inicio = time.perf_counter()
            consultado_en = datetime.now(UTC)
            try:
                if ajustes.respetar_robots and not await robots.permite(producto.url):
                    raise BloqueadoPorRobots("robots.txt no permite consultar esta URL")
                minimo = await robots.espera_minima(producto.url) if ajustes.respetar_robots else 0
                await limite.esperar_turno(producto.host, minimo)
                async with cupos:
                    # La hora y la duración se miden desde aquí, sin contar la espera de cortesía.
                    inicio = time.perf_counter()
                    consultado_en = datetime.now(UTC)
                    precio = await _obtener_precio(
                        client, navegador, producto, ajustes.intentos, espera_reintento
                    )
                error = None
            except Exception as exc:
                precio, error = None, _describir(exc)
                log.warning("%s: %s", producto.id, error)
            duracion = round((time.perf_counter() - inicio) * 1000)
            return Consulta(producto, consultado_en, precio, error, duracion)

        async with asyncio.TaskGroup() as grupo:
            tareas = [grupo.create_task(una(p)) for p in config.productos]
    return [t.result() for t in tareas]


@contextlib.asynccontextmanager
async def _navegador(
    config: Config, renderizador: Renderizador | None
) -> AsyncIterator[Renderizador | None]:
    if renderizador is not None or not any(p.js for p in config.productos):
        yield renderizador
        return
    async with Chromium(config.ajustes.user_agent, config.ajustes.timeout) as chromium:
        yield chromium


async def _obtener_precio(
    client: httpx.AsyncClient,
    navegador: Renderizador | None,
    producto: Producto,
    intentos: int,
    espera: float,
) -> Decimal:
    async for intento in AsyncRetrying(
        stop=stop_after_attempt(intentos),
        wait=wait_exponential(multiplier=espera, max=30),
        retry=retry_if_exception(_reintentable),
        reraise=True,
    ):
        with intento:
            if producto.js:
                assert navegador is not None
                html = await navegador.html(producto.url)
            else:
                respuesta = await client.get(producto.url)
                respuesta.raise_for_status()
                html = respuesta.text
    if producto.tipo == "json":
        return precio_desde_json(html, producto)
    return precio_desde_html(html, producto)


def _describir(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "tiempo de espera agotado"
    mensaje = str(exc) or type(exc).__name__
    return f"{type(exc).__name__}: {mensaje}" if isinstance(exc, httpx.HTTPError) else mensaje
