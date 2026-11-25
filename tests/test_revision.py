import sqlite3
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from monitor_precios import historial
from monitor_precios.revision import revisar

from .conftest import SAPIENS_URL, Sitio, dolar, hacer_config, libro

pytestmark = pytest.mark.anyio


class Buzon:
    def __init__(self, falla: bool = False) -> None:
        self.mensajes: list[str] = []
        self.falla = falla

    async def enviar(self, texto: str) -> None:
        if self.falla:
            raise RuntimeError("Telegram respondió 500")
        self.mensajes.append(texto)


@pytest.fixture
def conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    with historial.abrir(tmp_path / "h.db") as c:
        yield c


def _precio(sitio: Sitio, html: str, precio: str) -> None:
    sitio.html(SAPIENS_URL, html.replace("£54.23", precio, 1))


async def test_flujo_completo_con_alerta_una_sola_vez(
    sitio: Sitio, sapiens_html: str, conn: sqlite3.Connection
) -> None:
    config = hacer_config(libro(umbral=50), dolar())
    buzon = Buzon()

    r1 = await revisar(config, conn, buzon, transport=sitio.transport)
    assert [f.anterior for f in r1.filas] == [None, None]
    assert buzon.mensajes == []

    _precio(sitio, sapiens_html, "£48.00")
    r2 = await revisar(config, conn, buzon, transport=sitio.transport)
    assert r2.filas[0].anterior == Decimal("54.23")
    assert (
        r2.filas[0].variacion_pct == (Decimal("48.00") - Decimal("54.23")) / Decimal("54.23") * 100
    )
    assert r2.filas[1].variacion_pct == 0
    assert len(r2.alertas_enviadas) == 1
    assert buzon.mensajes[0].startswith("Sapiens bajó a £48,00")

    _precio(sitio, sapiens_html, "£47.00")
    r3 = await revisar(config, conn, buzon, transport=sitio.transport)
    assert r3.alertas_enviadas == []  # sigue bajo el umbral: no repite
    assert conn.execute("SELECT count(*) FROM alertas").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM consultas").fetchone() == (6,)


async def test_sin_notificador_no_avisa(sitio: Sitio, conn: sqlite3.Connection) -> None:
    r = await revisar(hacer_config(libro(umbral=60)), conn, None, transport=sitio.transport)
    assert r.alertas_enviadas == []
    assert conn.execute("SELECT count(*) FROM alertas").fetchone() == (0,)


async def test_si_telegram_falla_el_historial_se_guarda(
    sitio: Sitio, conn: sqlite3.Connection
) -> None:
    r = await revisar(
        hacer_config(libro(umbral=60)), conn, Buzon(falla=True), transport=sitio.transport
    )
    assert r.alertas_enviadas == []
    [(alerta, error)] = r.alertas_fallidas
    assert alerta.tipo == "bajo_umbral"
    assert "500" in error
    assert historial.ultimo_precio(conn, "sapiens") == Decimal("54.23")


async def test_cuenta_fallidas(sitio: Sitio, conn: sqlite3.Connection) -> None:
    sitio.agregar(SAPIENS_URL, httpx.Response(404))
    r = await revisar(hacer_config(libro(), dolar()), conn, None, transport=sitio.transport)
    assert r.fallidas == 1
    assert r.filas[0].variacion_pct is None
