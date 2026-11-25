"""Línea de comandos: ``monitor revisar``, ``monitor resumen``, ``monitor grafico``..."""

from __future__ import annotations

import asyncio
import csv
import logging
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from monitor_precios import __version__, alertas, grafico, historial
from monitor_precios.config import Config, ConfigInvalida, cargar
from monitor_precios.revision import Resultado, revisar

app = typer.Typer(
    help="Monitor de precios asíncrono: consulta, guarda el historial y avisa cuando bajan.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

OpcionConfig = Annotated[
    Path, typer.Option("--config", "-c", help="Archivo TOML con los productos.")
]
OpcionDb = Annotated[
    Path, typer.Option("--db", help="Base SQLite con el historial.", envvar="MONITOR_DB")
]
OpcionZona = Annotated[str, typer.Option("--zona", help="Zona horaria para mostrar fechas.")]

CONFIG_POR_DEFECTO = Path("productos.toml")
DB_POR_DEFECTO = Path("historial.db")


def _version(valor: bool) -> None:
    if valor:
        console.print(f"monitor-precios {__version__}")
        raise typer.Exit


@app.callback()
def principal(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Muestra más detalle.")] = False,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Versión.")
    ] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
        force=True,
    )
    for ruidoso in ("httpx", "httpcore", "matplotlib"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def _cargar(config: Path) -> Config:
    try:
        return cargar(config)
    except ConfigInvalida as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@app.command("revisar")
def cmd_revisar(
    config: OpcionConfig = CONFIG_POR_DEFECTO,
    db: OpcionDb = DB_POR_DEFECTO,
    sin_alertas: Annotated[
        bool, typer.Option("--sin-alertas", help="Consulta y guarda, pero no envía avisos.")
    ] = False,
) -> None:
    """Consulta todos los productos una vez, guarda los precios y envía las alertas."""
    cfg = _cargar(config)
    notificador = None if sin_alertas else alertas.desde_entorno()
    with historial.abrir(db) as conn:
        resultado = asyncio.run(revisar(cfg, conn, notificador))
    _mostrar_resultado(resultado)
    _resumen_github(resultado)

    if resultado.fallidas == len(resultado.filas):
        console.print("[red]Ninguna consulta funcionó.[/red]")
        raise typer.Exit(1)
    if resultado.fallidas and os.environ.get("GITHUB_ACTIONS") == "true":
        for fila in resultado.filas:
            if not fila.consulta.ok:
                typer.echo(f"::warning title={fila.consulta.producto.id}::{fila.consulta.error}")


def _mostrar_resultado(resultado: Resultado) -> None:
    tabla = Table(title="Revisión de precios", title_justify="left")
    tabla.add_column("Producto")
    tabla.add_column("Precio", justify="right")
    tabla.add_column("Variación", justify="right")
    tabla.add_column("Estado")
    tabla.add_column("ms", justify="right", style="dim")
    for fila in resultado.filas:
        c = fila.consulta
        precio = alertas.formatear(c.precio, c.producto.moneda) if c.precio is not None else "—"
        tabla.add_row(
            c.producto.nombre,
            precio,
            _variacion(fila.variacion_pct, color=True),
            "[green]ok[/green]" if c.ok else f"[red]{c.error}[/red]",
            str(c.duracion_ms),
        )
    console.print(tabla)
    for alerta in resultado.alertas_enviadas:
        console.print(f"[yellow]Alerta enviada:[/yellow] {alerta.mensaje.splitlines()[0]}")
    for alerta, error in resultado.alertas_fallidas:
        console.print(f"[red]Alerta no enviada ({error}):[/red] {alerta.mensaje.splitlines()[0]}")


def _variacion(pct: Decimal | None, color: bool = False) -> str:
    if pct is None:
        return "—"
    if pct == 0:
        return "="
    texto = f"{pct:+.2f} %".replace(".", ",")
    if not color:
        return texto
    return f"[green]{texto}[/green]" if pct < 0 else f"[red]{texto}[/red]"


def _resumen_github(resultado: Resultado) -> None:
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destino:
        return
    lineas = [
        "### Revisión de precios",
        "",
        "| Producto | Precio | Variación | Estado |",
        "|---|---:|---:|---|",
    ]
    for fila in resultado.filas:
        c = fila.consulta
        precio = alertas.formatear(c.precio, c.producto.moneda) if c.precio is not None else "—"
        estado = "ok" if c.ok else f"⚠️ {c.error}"
        lineas.append(
            f"| {c.producto.nombre} | {precio} | {_variacion(fila.variacion_pct)} | {estado} |"
        )
    if resultado.alertas_enviadas:
        lineas += ["", f"Alertas enviadas: {len(resultado.alertas_enviadas)}"]
    with Path(destino).open("a", encoding="utf-8") as f:
        f.write("\n".join(lineas) + "\n")


@app.command("resumen")
def cmd_resumen(db: OpcionDb = DB_POR_DEFECTO, zona: OpcionZona = "America/Santiago") -> None:
    """Precio actual, mínimo y máximo de cada producto."""
    tz = ZoneInfo(zona)
    with historial.abrir(db) as conn:
        filas = historial.resumen(conn)
    if not filas:
        console.print("Todavía no hay consultas. Corre [bold]monitor revisar[/bold] primero.")
        raise typer.Exit(1)
    tabla = Table(title="Resumen del historial", title_justify="left")
    for columna, justificar in [
        ("Producto", "left"), ("Actual", "right"), ("Mínimo", "right"), ("Máximo", "right"),
        ("Consultas", "right"), ("Errores", "right"), ("Última", "left"),
    ]:  # fmt: skip
        tabla.add_column(columna, justify=justificar)  # type: ignore[arg-type]
    for r in filas:
        tabla.add_row(
            r.nombre,
            _fmt(r.actual, r.moneda),
            _fmt(r.minimo, r.moneda),
            _fmt(r.maximo, r.moneda),
            str(r.consultas),
            str(r.errores) if r.errores else "",
            _fecha(r.ultima, tz),
        )
    console.print(tabla)


@app.command("historial")
def cmd_historial(
    producto: Annotated[str, typer.Argument(help="Id del producto, como en el TOML.")],
    db: OpcionDb = DB_POR_DEFECTO,
    ultimos: Annotated[int, typer.Option("--ultimos", "-n", min=1)] = 20,
    zona: OpcionZona = "America/Santiago",
) -> None:
    """Últimos precios guardados de un producto."""
    tz = ZoneInfo(zona)
    with historial.abrir(db) as conn:
        filas = {r.producto_id: r for r in historial.resumen(conn)}
        if producto not in filas:
            console.print(
                f"[red]No hay datos de {producto!r}.[/red] Disponibles: {', '.join(filas)}"
            )
            raise typer.Exit(1)
        puntos = historial.serie(conn, producto)[-ultimos:]
    moneda = filas[producto].moneda
    tabla = Table(title=filas[producto].nombre, title_justify="left")
    tabla.add_column("Fecha")
    tabla.add_column("Precio", justify="right")
    tabla.add_column("Variación", justify="right")
    anterior: Decimal | None = None
    for p in puntos:
        pct = None if anterior is None else (p.precio - anterior) / anterior * 100
        tabla.add_row(_fecha(p.consultado_en, tz), _fmt(p.precio, moneda), _variacion(pct, True))
        anterior = p.precio
    console.print(tabla)


@app.command("grafico")
def cmd_grafico(
    salida: Annotated[Path, typer.Option("--salida", "-o", help="Archivo PNG.")] = Path(
        "historial.png"
    ),
    db: OpcionDb = DB_POR_DEFECTO,
    producto: Annotated[
        list[str] | None, typer.Option("--producto", "-p", help="Solo estos productos.")
    ] = None,
    zona: OpcionZona = "America/Santiago",
) -> None:
    """Dibuja el historial de precios en un PNG."""
    with historial.abrir(db) as conn:
        resumen = historial.resumen(conn)
        elegidos = [r for r in resumen if not producto or r.producto_id in producto]
        series = [(r.nombre, r.moneda, historial.serie(conn, r.producto_id)) for r in elegidos]
    try:
        paneles = grafico.dibujar(series, salida, zona=ZoneInfo(zona))
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"Gráfico con {paneles} productos en [bold]{salida}[/bold]")


@app.command("exportar")
def cmd_exportar(
    salida: Annotated[Path, typer.Option("--salida", "-o", help="Archivo CSV.")] = Path(
        "precios.csv"
    ),
    db: OpcionDb = DB_POR_DEFECTO,
) -> None:
    """Exporta todas las consultas a CSV (fecha UTC, producto, moneda, precio, error)."""
    salida.parent.mkdir(parents=True, exist_ok=True)
    with historial.abrir(db) as conn, salida.open("w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(["consultado_en", "producto", "moneda", "precio", "error"])
        total = 0
        for fila in historial.exportar_filas(conn):
            escritor.writerow(fila)
            total += 1
    console.print(f"{total} consultas exportadas a [bold]{salida}[/bold]")


@app.command("validar")
def cmd_validar(config: OpcionConfig = CONFIG_POR_DEFECTO) -> None:
    """Revisa el archivo de productos sin consultar nada."""
    cfg = _cargar(config)
    tabla = Table(title=f"{len(cfg.productos)} productos en {config}", title_justify="left")
    for columna in ("Id", "Tipo", "Sitio", "Moneda", "Alertas"):
        tabla.add_column(columna)
    for p in cfg.productos:
        reglas = []
        if p.umbral is not None:
            reglas.append(f"bajo {alertas.formatear(p.umbral, p.moneda)}")
        if p.baja_pct is not None:
            reglas.append(f"baja ≥ {p.baja_pct} %".replace(".", ","))
        tipo = p.tipo + (" + js" if p.js else "")
        tabla.add_row(p.id, tipo, p.host, p.moneda, ", ".join(reglas) or "—")
    console.print(tabla)
    telegram = isinstance(alertas.desde_entorno(), alertas.Telegram)
    console.print(
        "Telegram: [green]configurado[/green]"
        if telegram
        else "Telegram: [yellow]sin configurar[/yellow] (las alertas solo quedan en el log)"
    )


def _fmt(valor: Decimal | None, moneda: str) -> str:
    return "—" if valor is None else alertas.formatear(valor, moneda)


def _fecha(valor: datetime | None, tz: ZoneInfo) -> str:
    return "—" if valor is None else valor.astimezone(tz).strftime("%Y-%m-%d %H:%M")
