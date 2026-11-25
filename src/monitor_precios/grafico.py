"""Gráfico del historial: un panel por producto, porque cada uno tiene su moneda y escala."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import tzinfo
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from monitor_precios.alertas import formatear
from monitor_precios.historial import Punto

_COLOR = "#2a6fdb"
_COLOR_MIN = "#1b9e5a"
_GRIS = "#6b7280"


def dibujar(
    series: Sequence[tuple[str, str, Sequence[Punto]]],
    salida: Path,
    columnas: int = 2,
    zona: tzinfo | None = None,
) -> int:
    """Dibuja ``(nombre, moneda, puntos)`` por producto y devuelve cuántos paneles hizo."""
    con_datos = [(n, m, s) for n, m, s in series if s]
    if not con_datos:
        raise ValueError("no hay precios guardados para graficar")
    columnas = min(columnas, len(con_datos))
    filas = math.ceil(len(con_datos) / columnas)
    fig, ejes = plt.subplots(filas, columnas, figsize=(6.2 * columnas, 2.6 * filas), squeeze=False)
    for eje, (nombre, moneda, puntos) in zip(ejes.flat, con_datos, strict=False):
        fechas = [p.consultado_en.astimezone(zona) for p in puntos]
        valores = [float(p.precio) for p in puntos]
        # Escalonado: el precio vale lo mismo hasta la siguiente consulta.
        eje.step(fechas, valores, where="post", color=_COLOR, linewidth=1.8)
        eje.plot(fechas[-1:], valores[-1:], "o", color=_COLOR, markersize=5)
        i_min = min(range(len(valores)), key=valores.__getitem__)
        if min(valores) < max(valores):
            eje.plot([fechas[i_min]], [valores[i_min]], "o", color=_COLOR_MIN, markersize=5)
        eje.set_title(nombre, loc="left", fontsize=11, fontweight="semibold")
        eje.text(
            1.0, 1.02, f"actual {formatear(puntos[-1].precio, moneda)}",
            transform=eje.transAxes, ha="right", va="bottom", fontsize=9, color=_GRIS,
        )  # fmt: skip
        eje.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos, m=moneda: _eje(v, m)))
        eje.grid(axis="y", color="#e5e7eb", linewidth=0.8)
        for borde in ("top", "right"):
            eje.spines[borde].set_visible(False)
        eje.tick_params(colors=_GRIS, labelsize=8)
        localizador = mdates.AutoDateLocator(tz=zona, maxticks=6)  # type: ignore[no-untyped-call]
        eje.xaxis.set_major_locator(localizador)
        eje.xaxis.set_major_formatter(mdates.ConciseDateFormatter(localizador, tz=zona))  # type: ignore[no-untyped-call]
        if min(valores) == max(valores):
            centro = valores[0]
            eje.set_ylim(centro * 0.95, centro * 1.05)
    for eje in list(ejes.flat)[len(con_datos) :]:
        eje.set_visible(False)
    fig.tight_layout()
    salida.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(salida, dpi=144)
    plt.close(fig)
    return len(con_datos)


def _eje(valor: float, moneda: str) -> str:
    if moneda == "CLP" and valor >= 100:
        return f"{valor:,.0f}".replace(",", ".")
    return f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
