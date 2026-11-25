import json
from decimal import Decimal

import httpx
import pytest

from monitor_precios.alertas import Consola, Telegram, desde_entorno, evaluar, formatear
from monitor_precios.config import desde_dict

from .conftest import dolar, libro

pytestmark = pytest.mark.anyio


def _p(**extra: object):  # type: ignore[no-untyped-def]
    return desde_dict({"producto": [libro(**extra)]}).productos[0]


D = Decimal


def test_umbral_avisa_solo_al_cruzarlo() -> None:
    p = _p(umbral=50)
    assert [a.tipo for a in evaluar(p, D(55), D(49))] == ["bajo_umbral"]
    assert evaluar(p, D(49), D(48)) == []  # ya estaba abajo: no repite
    assert [a.tipo for a in evaluar(p, None, D(50))] == ["bajo_umbral"]  # primera consulta
    assert evaluar(p, None, D(51)) == []


def test_baja_porcentual() -> None:
    p = _p(baja_pct=10)
    assert [a.tipo for a in evaluar(p, D(100), D(90))] == ["baja"]
    assert evaluar(p, D(100), D("90.01")) == []
    assert evaluar(p, D(100), D(120)) == []
    assert evaluar(p, None, D(10)) == []


def test_ambas_reglas_a_la_vez() -> None:
    alertas = evaluar(_p(umbral=50, baja_pct=10), D(60), D(45))
    assert [a.tipo for a in alertas] == ["bajo_umbral", "baja"]


def test_mensajes() -> None:
    p = _p(umbral=50, baja_pct=10)
    umbral, baja = evaluar(p, D(60), D("45.5"))
    assert umbral.mensaje.splitlines() == ["Sapiens bajó a £45,50 (umbral £50,00)", p.url]
    assert baja.mensaje.startswith("Sapiens bajó 24.2 %: de £60,00 a £45,50")


@pytest.mark.parametrize(
    ("valor", "moneda", "esperado"),
    [
        (D(12990), "CLP", "$12.990"),
        (D("1025.06"), "CLP", "$1.025,06"),
        (D("1299.9"), "USD", "US$1.299,90"),
        (D("5"), "EUR", "€5,00"),
        (D("3.5"), "PEN", "PEN 3,50"),
    ],
)
def test_formatear(valor: Decimal, moneda: str, esperado: str) -> None:
    assert formatear(valor, moneda) == esperado


class TelegramFalso:
    def __init__(self, *respuestas: httpx.Response | Exception) -> None:
        self.respuestas = list(respuestas)
        self.cuerpos: list[dict[str, object]] = []
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        self.cuerpos.append(json.loads(request.content))
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


async def test_telegram_envia_el_mensaje() -> None:
    falso = TelegramFalso(httpx.Response(200, json={"ok": True}))
    await Telegram("123:abc", "42", transport=httpx.MockTransport(falso)).enviar("hola")
    assert falso.urls == ["https://api.telegram.org/bot123:abc/sendMessage"]
    assert falso.cuerpos[0] == {"chat_id": "42", "text": "hola", "disable_web_page_preview": True}


async def test_telegram_reintenta_errores_de_red() -> None:
    falso = TelegramFalso(httpx.ConnectError("x"), httpx.Response(200, json={"ok": True}))
    telegram = Telegram("t", "1", transport=httpx.MockTransport(falso), espera_reintento=0)
    await telegram.enviar("hola")
    assert len(falso.urls) == 2


async def test_telegram_error_no_expone_el_token() -> None:
    falso = TelegramFalso(httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    with pytest.raises(RuntimeError) as error:
        await Telegram("secreto", "1", transport=httpx.MockTransport(falso)).enviar("x")
    assert str(error.value) == "Telegram respondió 401 Unauthorized"
    assert "secreto" not in str(error.value)


async def test_telegram_error_sin_json() -> None:
    falso = TelegramFalso(httpx.Response(502, text="Bad Gateway"))
    with pytest.raises(RuntimeError, match="respondió 502"):
        await Telegram("t", "1", transport=httpx.MockTransport(falso)).enviar("x")


async def test_consola_registra_en_el_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    await Consola().enviar("línea 1\nlínea 2")
    assert "línea 1 | línea 2" in caplog.text


def test_desde_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert isinstance(desde_entorno(), Consola)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    assert isinstance(desde_entorno(), Consola)  # falta el chat
    monkeypatch.setenv("TELEGRAM_CHAT_ID", " 42 ")
    assert isinstance(desde_entorno(), Telegram)


def test_dolar_sin_reglas_no_alerta() -> None:
    p = desde_dict({"producto": [dolar()]}).productos[0]
    assert evaluar(p, D(1000), D(1)) == []
