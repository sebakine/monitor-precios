"""Scraping ético: robots.txt y un límite de velocidad por sitio."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)


class Robots:
    """Descarga y guarda en memoria el robots.txt de cada sitio.

    Sigue la convención de RFC 9309: 404 significa que todo está permitido,
    401/403 que nada lo está, y un error del servidor o de red se trata como
    "no permitido" hasta la próxima corrida, para no rastrear a ciegas.
    """

    def __init__(self, client: httpx.AsyncClient, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._cache: dict[str, RobotFileParser | bool] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def permite(self, url: str) -> bool:
        reglas = await self._reglas(url)
        if isinstance(reglas, bool):
            return reglas
        return reglas.can_fetch(self._user_agent, url)

    async def espera_minima(self, url: str) -> float:
        """Crawl-delay declarado por el sitio, o 0."""
        reglas = await self._reglas(url)
        if isinstance(reglas, bool):
            return 0.0
        delay = reglas.crawl_delay(self._user_agent)
        return float(delay) if delay is not None else 0.0

    async def _reglas(self, url: str) -> RobotFileParser | bool:
        """Las reglas del sitio, o True/False si permite o prohíbe todo."""
        partes = urlsplit(url)
        origen = f"{partes.scheme}://{partes.netloc}"
        lock = self._locks.setdefault(origen, asyncio.Lock())
        async with lock:
            if origen not in self._cache:
                self._cache[origen] = await self._descargar(origen)
            return self._cache[origen]

    async def _descargar(self, origen: str) -> RobotFileParser | bool:
        try:
            respuesta = await self._client.get(f"{origen}/robots.txt")
        except httpx.HTTPError as exc:
            log.warning("robots.txt de %s no disponible (%s); no se consultará", origen, exc)
            return False
        if respuesta.status_code in {401, 403} or respuesta.status_code >= 500:
            log.warning("robots.txt de %s respondió %s", origen, respuesta.status_code)
            return False
        if respuesta.status_code >= 400:
            return True
        parser = RobotFileParser(f"{origen}/robots.txt")
        parser.parse(respuesta.text.splitlines())
        return parser


class LimitePorSitio:
    """Garantiza una pausa mínima entre consultas al mismo sitio.

    Las consultas a sitios distintos siguen corriendo en paralelo.
    """

    def __init__(
        self,
        espera: float,
        reloj: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._espera = espera
        self._reloj = reloj
        self._dormir = dormir
        self._ultima: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def esperar_turno(self, host: str, minimo: float = 0.0) -> None:
        espera = max(self._espera, minimo)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            ultima = self._ultima.get(host)
            if ultima is not None:
                faltante = ultima + espera - self._reloj()
                if faltante > 0:
                    await self._dormir(faltante)
            self._ultima[host] = self._reloj()
