import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from monitor_precios import historial
from monitor_precios.config import Producto, desde_dict
from monitor_precios.consulta import Consulta

from .conftest import dolar, libro

T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)


@pytest.fixture
def productos() -> tuple[Producto, ...]:
    return desde_dict({"producto": [libro(), dolar()]}).productos


@pytest.fixture
def conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    with historial.abrir(tmp_path / "sub" / "h.db") as c:
        yield c


def _c(p: Producto, horas: int, precio: str | None, error: str | None = None) -> Consulta:
    return Consulta(
        p, T0 + timedelta(hours=horas), None if precio is None else Decimal(precio), error, 10
    )


def test_migrar_es_idempotente(conn: sqlite3.Connection) -> None:
    historial.migrar(conn)
    historial.migrar(conn)
    assert conn.execute("PRAGMA user_version").fetchone() == (1,)


def test_guardar_y_leer(conn: sqlite3.Connection, productos: tuple[Producto, ...]) -> None:
    libro_, dolar_ = productos
    historial.guardar(conn, [_c(libro_, 0, "54.23"), _c(dolar_, 0, "1003.21")])
    historial.guardar(conn, [_c(libro_, 6, "49.99"), _c(dolar_, 6, None, "HTTP 503")])
    historial.guardar(conn, [_c(libro_, 12, "52.10")])

    assert historial.ultimo_precio(conn, "sapiens") == Decimal("52.10")
    assert historial.ultimo_precio(conn, "dolar") == Decimal("1003.21")  # ignora el error
    assert historial.ultimo_precio(conn, "otro") is None
    assert [p.precio for p in historial.serie(conn, "sapiens")] == [
        Decimal("54.23"), Decimal("49.99"), Decimal("52.10"),
    ]  # fmt: skip

    r_dolar, r_libro = historial.resumen(conn)
    assert (r_libro.actual, r_libro.minimo, r_libro.maximo) == (
        Decimal("52.10"), Decimal("49.99"), Decimal("54.23"),
    )  # fmt: skip
    assert (r_libro.consultas, r_libro.errores, r_libro.ultima) == (3, 0, T0 + timedelta(hours=12))
    assert (r_dolar.consultas, r_dolar.errores, r_dolar.moneda) == (2, 1, "CLP")


def test_precios_exactos(conn: sqlite3.Connection, productos: tuple[Producto, ...]) -> None:
    historial.guardar(conn, [_c(productos[0], 0, "0.1"), _c(productos[0], 1, "0.30")])
    assert historial.serie(conn, "sapiens")[1].precio == Decimal("0.30")


def test_producto_sin_precios(conn: sqlite3.Connection, productos: tuple[Producto, ...]) -> None:
    historial.guardar(conn, [_c(productos[1], 0, None, "caído")])
    [r] = historial.resumen(conn)
    assert (r.actual, r.minimo, r.maximo, r.errores) == (None, None, None, 1)


def test_cambio_de_nombre_se_actualiza(conn: sqlite3.Connection) -> None:
    viejo = desde_dict({"producto": [libro(nombre="Viejo")]}).productos[0]
    nuevo = desde_dict({"producto": [libro(nombre="Nuevo")]}).productos[0]
    historial.guardar(conn, [_c(viejo, 0, "1")])
    historial.guardar(conn, [_c(nuevo, 1, "1")])
    assert historial.resumen(conn)[0].nombre == "Nuevo"


def test_la_base_exige_precio_o_error(
    conn: sqlite3.Connection, productos: tuple[Producto, ...]
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        historial.guardar(conn, [_c(productos[0], 0, None)])


def test_alertas_y_exportar(conn: sqlite3.Connection, productos: tuple[Producto, ...]) -> None:
    historial.guardar(conn, [_c(productos[0], 0, "10"), _c(productos[1], 0, None, "x")])
    historial.registrar_alerta(conn, productos[0], T0, "baja", Decimal(10), "bajó")
    assert conn.execute("SELECT tipo, precio FROM alertas").fetchall() == [("baja", "10")]
    filas = list(historial.exportar_filas(conn))
    assert filas == [
        (T0.isoformat(), "dolar", "CLP", "", "x"),
        (T0.isoformat(), "sapiens", "GBP", "10", ""),
    ]
