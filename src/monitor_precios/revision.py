"""Una corrida completa: consultar, comparar con el historial, guardar y avisar."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from monitor_precios import historial
from monitor_precios.alertas import Alerta, Notificador, evaluar
from monitor_precios.config import Config
from monitor_precios.consulta import Consulta, consultar
from monitor_precios.navegador import Renderizador

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Fila:
    consulta: Consulta
    anterior: Decimal | None

    @property
    def variacion_pct(self) -> Decimal | None:
        actual = self.consulta.precio
        if actual is None or self.anterior is None or self.anterior == 0:
            return None
        return (actual - self.anterior) / self.anterior * 100


@dataclass(slots=True)
class Resultado:
    filas: list[Fila] = field(default_factory=list)
    alertas_enviadas: list[Alerta] = field(default_factory=list)
    alertas_fallidas: list[tuple[Alerta, str]] = field(default_factory=list)

    @property
    def fallidas(self) -> int:
        return sum(1 for f in self.filas if not f.consulta.ok)


async def revisar(
    config: Config,
    conn: sqlite3.Connection,
    notificador: Notificador | None,
    *,
    renderizador: Renderizador | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    espera_reintento: float = 1.0,
) -> Resultado:
    """Corre una revisión. Con ``notificador=None`` no se envían alertas (modo prueba)."""
    consultas = await consultar(
        config, transport=transport, renderizador=renderizador, espera_reintento=espera_reintento
    )
    resultado = Resultado()
    pendientes: list[Alerta] = []
    for c in consultas:
        anterior = historial.ultimo_precio(conn, c.producto.id)
        resultado.filas.append(Fila(c, anterior))
        if c.precio is not None:
            pendientes.extend(evaluar(c.producto, anterior, c.precio))
    # Se guarda antes de avisar: si Telegram falla, el historial no se pierde.
    historial.guardar(conn, consultas)

    if notificador is None:
        return resultado
    for alerta in pendientes:
        try:
            await notificador.enviar(alerta.mensaje)
        except Exception as exc:
            log.error("no se pudo enviar la alerta de %s: %s", alerta.producto.id, exc)
            resultado.alertas_fallidas.append((alerta, str(exc)))
            continue
        historial.registrar_alerta(
            conn, alerta.producto, datetime.now(UTC), alerta.tipo, alerta.actual, alerta.mensaje
        )
        resultado.alertas_enviadas.append(alerta)
    return resultado
