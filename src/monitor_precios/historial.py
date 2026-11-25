"""Historial de precios en SQLite.

Los precios se guardan como texto para conservar el valor exacto de ``Decimal``.
Las fechas van en UTC, en formato ISO 8601.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from monitor_precios.config import Producto
from monitor_precios.consulta import Consulta

_MIGRACIONES: tuple[str, ...] = (
    """
    CREATE TABLE productos (
        id      TEXT PRIMARY KEY,
        nombre  TEXT NOT NULL,
        url     TEXT NOT NULL,
        moneda  TEXT NOT NULL
    );
    CREATE TABLE consultas (
        id            INTEGER PRIMARY KEY,
        producto_id   TEXT NOT NULL REFERENCES productos (id),
        consultado_en TEXT NOT NULL,
        precio        TEXT,
        error         TEXT,
        duracion_ms   INTEGER NOT NULL,
        CHECK ((precio IS NULL) <> (error IS NULL))
    );
    CREATE INDEX consultas_producto_fecha ON consultas (producto_id, consultado_en);
    CREATE TABLE alertas (
        id           INTEGER PRIMARY KEY,
        producto_id  TEXT NOT NULL REFERENCES productos (id),
        enviada_en   TEXT NOT NULL,
        tipo         TEXT NOT NULL,
        precio       TEXT NOT NULL,
        mensaje      TEXT NOT NULL
    );
    """,
)


@dataclass(frozen=True, slots=True)
class Punto:
    consultado_en: datetime
    precio: Decimal


@dataclass(frozen=True, slots=True)
class Resumen:
    producto_id: str
    nombre: str
    moneda: str
    actual: Decimal | None
    minimo: Decimal | None
    maximo: Decimal | None
    consultas: int
    errores: int
    ultima: datetime | None


@contextmanager
def abrir(ruta: Path) -> Iterator[sqlite3.Connection]:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ruta)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        migrar(conn)
        yield conn
    finally:
        conn.close()


def migrar(conn: sqlite3.Connection) -> None:
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    for numero, sql in enumerate(_MIGRACIONES[version:], start=version + 1):
        with conn:
            conn.executescript(f"BEGIN; {sql}; PRAGMA user_version = {numero}; COMMIT;")


def guardar(conn: sqlite3.Connection, consultas: Iterable[Consulta]) -> None:
    with conn:
        for c in consultas:
            p = c.producto
            conn.execute(
                """INSERT INTO productos (id, nombre, url, moneda) VALUES (?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET
                       nombre = excluded.nombre, url = excluded.url, moneda = excluded.moneda""",
                (p.id, p.nombre, p.url, p.moneda),
            )
            conn.execute(
                """INSERT INTO consultas (producto_id, consultado_en, precio, error, duracion_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    p.id,
                    c.consultado_en.isoformat(),
                    None if c.precio is None else str(c.precio),
                    c.error,
                    c.duracion_ms,
                ),
            )


def ultimo_precio(conn: sqlite3.Connection, producto_id: str) -> Decimal | None:
    fila = conn.execute(
        """SELECT precio FROM consultas WHERE producto_id = ? AND precio IS NOT NULL
           ORDER BY consultado_en DESC, id DESC LIMIT 1""",
        (producto_id,),
    ).fetchone()
    return None if fila is None else Decimal(fila[0])


def registrar_alerta(
    conn: sqlite3.Connection, producto: Producto, enviada_en: datetime, tipo: str,
    precio: Decimal, mensaje: str,
) -> None:  # fmt: skip
    with conn:
        conn.execute(
            """INSERT INTO alertas (producto_id, enviada_en, tipo, precio, mensaje)
               VALUES (?, ?, ?, ?, ?)""",
            (producto.id, enviada_en.isoformat(), tipo, str(precio), mensaje),
        )


def serie(conn: sqlite3.Connection, producto_id: str) -> list[Punto]:
    filas = conn.execute(
        """SELECT consultado_en, precio FROM consultas
           WHERE producto_id = ? AND precio IS NOT NULL ORDER BY consultado_en, id""",
        (producto_id,),
    )
    return [Punto(datetime.fromisoformat(f), Decimal(p)) for f, p in filas]


def resumen(conn: sqlite3.Connection) -> list[Resumen]:
    filas = conn.execute(
        """
        WITH ultimo AS (
            SELECT producto_id, precio,
                   row_number() OVER (PARTITION BY producto_id
                                      ORDER BY consultado_en DESC, id DESC) AS n
            FROM consultas WHERE precio IS NOT NULL
        )
        SELECT p.id, p.nombre, p.moneda, u.precio,
               count(c.id), count(c.error), max(c.consultado_en)
        FROM productos p
        LEFT JOIN consultas c ON c.producto_id = p.id
        LEFT JOIN ultimo u ON u.producto_id = p.id AND u.n = 1
        GROUP BY p.id ORDER BY p.id
        """
    ).fetchall()
    # Mínimo y máximo en Python: en SQL habría que pasar el texto a REAL y perder precisión.
    extremos: dict[str, tuple[Decimal, Decimal]] = {}
    for pid, texto in conn.execute(
        "SELECT producto_id, precio FROM consultas WHERE precio IS NOT NULL"
    ):
        valor = Decimal(texto)
        bajo, alto = extremos.get(pid, (valor, valor))
        extremos[pid] = (min(bajo, valor), max(alto, valor))
    return [
        Resumen(
            producto_id=pid,
            nombre=nombre,
            moneda=moneda,
            actual=None if actual is None else Decimal(actual),
            minimo=extremos[pid][0] if pid in extremos else None,
            maximo=extremos[pid][1] if pid in extremos else None,
            consultas=total,
            errores=errores,
            ultima=None if ultima is None else datetime.fromisoformat(ultima),
        )
        for pid, nombre, moneda, actual, total, errores, ultima in filas
    ]


def exportar_filas(conn: sqlite3.Connection) -> Iterator[tuple[str, str, str, str, str]]:
    yield from conn.execute(
        """SELECT c.consultado_en, p.id, p.moneda, coalesce(c.precio, ''), coalesce(c.error, '')
           FROM consultas c JOIN productos p ON p.id = c.producto_id
           ORDER BY c.consultado_en, p.id"""
    )
