import functools
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from monitor_precios import __version__, cli
from monitor_precios.revision import revisar

from .conftest import SAPIENS_URL, Sitio

runner = CliRunner()

CONFIG = f"""
[ajustes]
espera_por_sitio = 0
intentos = 1

[[producto]]
id = "sapiens"
nombre = "Sapiens"
url = "{SAPIENS_URL}"
selector = ".product_main .price_color"
moneda = "GBP"
umbral = 60

[[producto]]
id = "dolar"
nombre = "Dólar"
url = "https://mindicador.cl/api/dolar"
tipo = "json"
ruta = "serie.0.valor"
baja_pct = 1.5
"""


@pytest.fixture
def entorno(tmp_path: Path, sitio: Sitio, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "productos.toml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "200")
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli, "revisar", functools.partial(revisar, transport=sitio.transport))
    return tmp_path


def _ok(*args: str) -> str:
    resultado = runner.invoke(cli.app, list(args))
    assert resultado.exit_code == 0, resultado.output
    return resultado.output


def test_version() -> None:
    assert __version__ in _ok("--version")


def test_revisar_guarda_y_muestra(entorno: Path) -> None:
    salida = _ok("revisar")
    assert "£54,23" in salida
    assert "$1.003,21" in salida
    assert "sin Telegram configurado" in salida  # el aviso queda en el log
    with closing(sqlite3.connect(entorno / "historial.db")) as conn:
        assert conn.execute("SELECT count(*) FROM consultas").fetchone() == (2,)


def test_revisar_sin_alertas_y_con_alerta_enviada(
    entorno: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ok("revisar", "--sin-alertas")
    with closing(sqlite3.connect(entorno / "historial.db")) as conn:
        assert conn.execute("SELECT count(*) FROM alertas").fetchone() == (0,)

    enviados: list[str] = []

    class Buzon:
        async def enviar(self, texto: str) -> None:
            enviados.append(texto)

    monkeypatch.setattr(cli.alertas, "desde_entorno", Buzon)
    (entorno / "historial.db").unlink()
    salida = _ok("revisar")
    assert "Alerta enviada: Sapiens bajó a £54,23" in salida
    assert len(enviados) == 1


def test_alerta_fallida_se_informa(entorno: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Roto:
        async def enviar(self, texto: str) -> None:
            raise RuntimeError("Telegram respondió 401 Unauthorized")

    monkeypatch.setattr(cli.alertas, "desde_entorno", Roto)
    assert "Alerta no enviada (Telegram respondió 401" in _ok("revisar")


def test_revisar_en_github_actions(
    entorno: Path, sitio: Sitio, monkeypatch: pytest.MonkeyPatch
) -> None:
    resumen = entorno / "resumen.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumen))
    sitio.agregar(SAPIENS_URL, httpx.Response(503))
    salida = _ok("revisar", "--sin-alertas")
    assert "::warning title=sapiens::HTTP 503" in salida
    texto = resumen.read_text(encoding="utf-8")
    assert "| Dólar | $1.003,21 | — | ok |" in texto
    assert "| Sapiens | — | — | ⚠️ HTTP 503 |" in texto


def test_revisar_falla_si_ninguna_consulta_funciona(entorno: Path, sitio: Sitio) -> None:
    sitio.agregar(SAPIENS_URL, httpx.Response(500))
    sitio.agregar("https://mindicador.cl/api/dolar", httpx.Response(500))
    resultado = runner.invoke(cli.app, ["revisar"])
    assert resultado.exit_code == 1
    assert "Ninguna consulta funcionó" in resultado.output


def test_config_invalida_sale_con_codigo_2(entorno: Path) -> None:
    (entorno / "malo.toml").write_text("[[producto]]\nid = 'x'\n", encoding="utf-8")
    resultado = runner.invoke(cli.app, ["revisar", "-c", "malo.toml"])
    assert resultado.exit_code == 2
    assert "'url' debe empezar" in resultado.output


def test_resumen_historial_grafico_y_exportar(
    entorno: Path, sitio: Sitio, sapiens_html: str
) -> None:
    _ok("revisar", "--sin-alertas")
    sitio.html(SAPIENS_URL, sapiens_html.replace("£54.23", "£50.00", 1))
    _ok("revisar", "--sin-alertas")

    salida = _ok("resumen")
    assert "£50,00" in salida
    assert "£54,23" in salida

    salida = _ok("historial", "sapiens", "-n", "5")
    assert "-7,80 %" in salida

    salida = _ok("grafico", "-o", "docs/h.png")
    assert "2 productos" in salida
    assert (entorno / "docs" / "h.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "1 productos" in _ok("grafico", "-o", "solo.png", "-p", "dolar")

    assert "4 consultas" in _ok("exportar", "-o", "out/precios.csv")
    lineas = (entorno / "out" / "precios.csv").read_text(encoding="utf-8").splitlines()
    assert lineas[0] == "consultado_en,producto,moneda,precio,error"
    assert len(lineas) == 5


def test_comandos_sin_datos(entorno: Path) -> None:
    assert runner.invoke(cli.app, ["resumen"]).exit_code == 1
    resultado = runner.invoke(cli.app, ["grafico"])
    assert resultado.exit_code == 1
    assert "no hay precios" in resultado.output
    _ok("revisar", "--sin-alertas")
    resultado = runner.invoke(cli.app, ["historial", "otro"])
    assert resultado.exit_code == 1
    assert "Disponibles: dolar, sapiens" in resultado.output


def test_validar(entorno: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    salida = _ok("validar")
    assert "bajo £60,00" in salida
    assert "baja ≥ 1,5 %" in salida
    assert "sin configurar" in salida
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    assert "configurado" in _ok("-v", "validar")
