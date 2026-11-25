"""Lectura y validación del archivo de productos (TOML)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from monitor_precios import __version__
from monitor_precios.precios import Separador

Tipo = Literal["html", "json"]

USER_AGENT = f"monitor-precios/{__version__} (+https://github.com/sebakine/monitor-precios)"
_ID_VALIDO = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ConfigInvalida(ValueError):
    """El archivo de productos tiene errores; el mensaje los lista todos."""


@dataclass(frozen=True, slots=True)
class Producto:
    id: str
    nombre: str
    url: str
    tipo: Tipo = "html"
    selector: str | None = None
    """Selector CSS del precio (tipo html)."""
    ruta: str | None = None
    """Ruta al precio dentro del JSON, por ejemplo ``serie.0.valor`` (tipo json)."""
    moneda: str = "CLP"
    decimal: Separador | None = None
    js: bool = False
    """Renderizar la página con Playwright antes de leer el precio."""
    umbral: Decimal | None = None
    """Avisar cuando el precio baje de este valor (o lo iguale)."""
    baja_pct: Decimal | None = None
    """Avisar cuando el precio baje al menos este porcentaje desde la consulta anterior."""

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc


@dataclass(frozen=True, slots=True)
class Ajustes:
    concurrencia: int = 5
    """Consultas simultáneas en total."""
    espera_por_sitio: float = 1.0
    """Segundos mínimos entre dos consultas al mismo sitio."""
    timeout: float = 20.0
    intentos: int = 3
    user_agent: str = USER_AGENT
    respetar_robots: bool = True


@dataclass(frozen=True, slots=True)
class Config:
    productos: tuple[Producto, ...]
    ajustes: Ajustes = field(default_factory=Ajustes)


def cargar(ruta: Path) -> Config:
    try:
        datos = tomllib.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigInvalida(f"no existe el archivo {ruta}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigInvalida(f"{ruta}: TOML inválido: {exc}") from exc
    return desde_dict(datos)


def desde_dict(datos: dict[str, Any]) -> Config:
    errores: list[str] = []
    ajustes = _ajustes(datos.get("ajustes", {}), errores)
    crudos = datos.get("producto", [])
    if not isinstance(crudos, list) or not crudos:
        errores.append("se necesita al menos un [[producto]]")
        crudos = []

    productos: list[Producto] = []
    vistos: set[str] = set()
    for i, crudo in enumerate(crudos, start=1):
        etiqueta = f"producto {i}"
        if not isinstance(crudo, dict):
            errores.append(f"{etiqueta}: debe ser una tabla")
            continue
        producto = _producto(crudo, etiqueta, errores)
        if producto is None:
            continue
        if producto.id in vistos:
            errores.append(f"{etiqueta}: el id {producto.id!r} está repetido")
            continue
        vistos.add(producto.id)
        productos.append(producto)

    if errores:
        raise ConfigInvalida("configuración inválida:\n- " + "\n- ".join(errores))
    return Config(productos=tuple(productos), ajustes=ajustes)


_CAMPOS_PRODUCTO = {
    "id", "nombre", "url", "tipo", "selector", "ruta", "moneda", "decimal", "js", "umbral",
    "baja_pct",
}  # fmt: skip


def _producto(crudo: dict[str, Any], etiqueta: str, errores: list[str]) -> Producto | None:
    antes = len(errores)
    desconocidos = sorted(set(crudo) - _CAMPOS_PRODUCTO)
    if desconocidos:
        errores.append(f"{etiqueta}: campos desconocidos {', '.join(desconocidos)}")

    pid = crudo.get("id")
    if not isinstance(pid, str) or not _ID_VALIDO.match(pid):
        errores.append(f"{etiqueta}: 'id' debe ser minúsculas, números, '-' o '_'")
    else:
        etiqueta = f"producto {pid!r}"
    nombre = crudo.get("nombre", pid)
    if not isinstance(nombre, str) or not nombre.strip():
        errores.append(f"{etiqueta}: 'nombre' debe ser texto")

    url = crudo.get("url")
    partes = urlsplit(url) if isinstance(url, str) else None
    if partes is None or partes.scheme not in {"http", "https"} or not partes.netloc:
        errores.append(f"{etiqueta}: 'url' debe empezar con http:// o https://")

    tipo = crudo.get("tipo", "html")
    if tipo == "html" and not isinstance(crudo.get("selector"), str):
        errores.append(f"{etiqueta}: el tipo html necesita 'selector'")
    elif tipo == "json" and not isinstance(crudo.get("ruta"), str):
        errores.append(f"{etiqueta}: el tipo json necesita 'ruta'")
    elif tipo not in {"html", "json"}:
        errores.append(f"{etiqueta}: 'tipo' debe ser html o json")
    if crudo.get("js") and tipo != "html":
        errores.append(f"{etiqueta}: 'js' solo aplica al tipo html")

    decimal = crudo.get("decimal")
    if decimal not in {None, ".", ","}:
        errores.append(f"{etiqueta}: 'decimal' debe ser '.' o ','")

    umbral = _decimal_positivo(crudo, "umbral", etiqueta, errores)
    baja_pct = _decimal_positivo(crudo, "baja_pct", etiqueta, errores)
    if baja_pct is not None and baja_pct >= 100:
        errores.append(f"{etiqueta}: 'baja_pct' debe ser menor que 100")

    if len(errores) > antes:
        return None
    assert isinstance(pid, str)
    assert isinstance(url, str)
    assert isinstance(nombre, str)
    return Producto(
        id=pid,
        nombre=nombre.strip(),
        url=url,
        tipo=tipo,
        selector=crudo.get("selector"),
        ruta=crudo.get("ruta"),
        moneda=str(crudo.get("moneda", "CLP")).upper(),
        decimal=decimal,
        js=bool(crudo.get("js", False)),
        umbral=umbral,
        baja_pct=baja_pct,
    )


def _decimal_positivo(
    crudo: dict[str, Any], campo: str, etiqueta: str, errores: list[str]
) -> Decimal | None:
    valor = crudo.get(campo)
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, int | float) or valor <= 0:
        errores.append(f"{etiqueta}: '{campo}' debe ser un número mayor que 0")
        return None
    return Decimal(str(valor))


def _ajustes(crudo: Any, errores: list[str]) -> Ajustes:
    if not isinstance(crudo, dict):
        errores.append("[ajustes] debe ser una tabla")
        return Ajustes()
    base = Ajustes()
    desconocidos = sorted(set(crudo) - set(Ajustes.__slots__))
    if desconocidos:
        errores.append(f"[ajustes]: campos desconocidos {', '.join(desconocidos)}")

    def numero(campo: str, minimo: float, entero: bool = False) -> Any:
        valor = crudo.get(campo, getattr(base, campo))
        tipos = (int,) if entero else (int, float)
        if isinstance(valor, bool) or not isinstance(valor, tipos) or valor < minimo:
            errores.append(f"[ajustes]: '{campo}' debe ser un número >= {minimo}")
            return getattr(base, campo)
        return valor

    user_agent = crudo.get("user_agent", base.user_agent)
    if not isinstance(user_agent, str) or not user_agent.strip():
        errores.append("[ajustes]: 'user_agent' debe ser texto")
        user_agent = base.user_agent
    return Ajustes(
        concurrencia=numero("concurrencia", 1, entero=True),
        espera_por_sitio=float(numero("espera_por_sitio", 0)),
        timeout=float(numero("timeout", 1)),
        intentos=numero("intentos", 1, entero=True),
        user_agent=user_agent,
        respetar_robots=bool(crudo.get("respetar_robots", True)),
    )
