"""Renderizado de páginas con JavaScript usando Playwright (dependencia opcional)."""

from __future__ import annotations

import os
from types import TracebackType
from typing import Any, Protocol


class Renderizador(Protocol):
    async def html(self, url: str) -> str: ...


class PlaywrightNoInstalado(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "este producto usa js = true: instala el extra con "
            "`pip install 'monitor-precios[js]'` y luego `playwright install chromium`"
        )


class Chromium:
    """Un navegador Chromium sin interfaz, compartido por todas las consultas de una corrida."""

    def __init__(self, user_agent: str, timeout_s: float) -> None:
        self._user_agent = user_agent
        self._timeout_ms = timeout_s * 1000
        self._playwright: Any = None
        self._navegador: Any = None
        self._contexto: Any = None

    async def __aenter__(self) -> Chromium:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise PlaywrightNoInstalado from exc
        self._playwright = await async_playwright().start()
        # MONITOR_CHROMIUM permite usar un Chrome o Chromium ya instalado en el sistema.
        ejecutable = os.environ.get("MONITOR_CHROMIUM") or None
        self._navegador = await self._playwright.chromium.launch(executable_path=ejecutable)
        self._contexto = await self._navegador.new_context(user_agent=self._user_agent)
        return self

    async def __aexit__(
        self,
        tipo: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._navegador is not None:
            await self._navegador.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def html(self, url: str) -> str:
        pagina = await self._contexto.new_page()
        try:
            await pagina.goto(url, wait_until="networkidle", timeout=self._timeout_ms)
            return str(await pagina.content())
        finally:
            await pagina.close()
