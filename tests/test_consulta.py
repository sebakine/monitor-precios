import asyncio
from decimal import Decimal

import httpx
import pytest

from monitor_precios.consulta import consultar

from .conftest import DOLAR_URL, SAPIENS_URL, Sitio, dolar, hacer_config, libro

pytestmark = pytest.mark.anyio


async def test_consulta_html_y_json_en_el_orden_de_la_config(sitio: Sitio) -> None:
    config = hacer_config(dolar(), libro())
    consultas = await consultar(config, transport=sitio.transport)
    assert [c.producto.id for c in consultas] == ["dolar", "sapiens"]
    assert [c.precio for c in consultas] == [Decimal("1003.21"), Decimal("54.23")]
    assert all(c.ok and c.duracion_ms >= 0 for c in consultas)
    assert consultas[0].consultado_en.tzinfo is not None


async def test_un_producto_que_falla_no_detiene_a_los_demas(sitio: Sitio) -> None:
    config = hacer_config(
        libro(), libro(id="roto", url="https://books.toscrape.com/no-existe"), dolar()
    )
    consultas = await consultar(config, transport=sitio.transport, espera_reintento=0)
    assert [c.ok for c in consultas] == [True, False, True]
    assert consultas[1].error == "HTTP 404"
    assert consultas[1].precio is None


async def test_reintenta_errores_temporales(sapiens_html: str) -> None:
    sitio = Sitio().agregar(
        SAPIENS_URL,
        httpx.Response(503),
        httpx.ConnectError("conexión reiniciada"),
        httpx.Response(200, text=sapiens_html),
    )
    [c] = await consultar(hacer_config(libro()), transport=sitio.transport, espera_reintento=0)
    assert c.precio == Decimal("54.23")
    assert sitio.veces(SAPIENS_URL) == 3


async def test_429_se_reintenta_y_404_no() -> None:
    sitio = Sitio().agregar(SAPIENS_URL, httpx.Response(429))
    [c] = await consultar(hacer_config(libro()), transport=sitio.transport, espera_reintento=0)
    assert c.error == "HTTP 429"
    assert sitio.veces(SAPIENS_URL) == 3

    sitio = Sitio().agregar(SAPIENS_URL, httpx.Response(404))
    await consultar(hacer_config(libro()), transport=sitio.transport, espera_reintento=0)
    assert sitio.veces(SAPIENS_URL) == 1


async def test_describe_timeouts_y_errores_de_red() -> None:
    sitio = (
        Sitio()
        .agregar(SAPIENS_URL, httpx.ReadTimeout("lento"))
        .agregar(DOLAR_URL, httpx.ConnectError(""))
    )
    consultas = await consultar(
        hacer_config(libro(), dolar(), intentos=1), transport=sitio.transport
    )
    assert [c.error for c in consultas] == [
        "tiempo de espera agotado",
        "ConnectError: ConnectError",
    ]


async def test_precio_no_encontrado_queda_como_error(sitio: Sitio) -> None:
    [c] = await consultar(hacer_config(libro(selector=".nada")), transport=sitio.transport)
    assert c.error is not None
    assert "no encontró nada" in c.error


async def test_respeta_robots_txt(sitio: Sitio) -> None:
    sitio.html("https://books.toscrape.com/robots.txt", "User-agent: *\nDisallow: /catalogue/\n")
    consultas = await consultar(hacer_config(libro(), dolar()), transport=sitio.transport)
    assert consultas[0].error == "robots.txt no permite consultar esta URL"
    assert sitio.veces(SAPIENS_URL) == 0
    assert consultas[1].ok


async def test_se_puede_desactivar_robots(sitio: Sitio) -> None:
    sitio.html("https://books.toscrape.com/robots.txt", "User-agent: *\nDisallow: /\n")
    config = hacer_config(libro(), respetar_robots=False)
    [c] = await consultar(config, transport=sitio.transport)
    assert c.ok
    assert "https://books.toscrape.com/robots.txt" not in sitio.pedidas


async def test_envia_user_agent_identificable(sitio: Sitio) -> None:
    agentes: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        agentes.append(request.headers["user-agent"])
        return httpx.Response(200, text='{"serie": [{"valor": 1}]}')

    sitio.agregar(DOLAR_URL, responder)
    await consultar(hacer_config(dolar()), transport=sitio.transport)
    assert agentes[0].startswith("monitor-precios/")


async def test_la_concurrencia_tiene_tope() -> None:
    activas = maximo = 0

    async def lento(request: httpx.Request) -> httpx.Response:
        nonlocal activas, maximo
        activas += 1
        maximo = max(maximo, activas)
        await asyncio.sleep(0.01)
        activas -= 1
        return httpx.Response(200, text='{"v": 10}')

    class Transporte(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return await lento(request)

    productos = [dolar(id=f"p{i}", url=f"https://sitio{i}.cl/api", ruta="v") for i in range(8)]
    consultas = await consultar(hacer_config(*productos, concurrencia=3), transport=Transporte())
    assert all(c.ok for c in consultas)
    assert maximo == 3


class RenderizadorFalso:
    def __init__(self, html: str) -> None:
        self._html = html
        self.urls: list[str] = []

    async def html(self, url: str) -> str:
        self.urls.append(url)
        return self._html


async def test_productos_js_usan_el_navegador(sitio: Sitio) -> None:
    renderizador = RenderizadorFalso('<div id="p">$ 19.990</div>')
    config = hacer_config(libro(js=True, selector="#p", moneda="CLP"))
    [c] = await consultar(config, transport=sitio.transport, renderizador=renderizador)
    assert c.precio == Decimal(19990)
    assert renderizador.urls == [SAPIENS_URL]
    assert SAPIENS_URL not in sitio.pedidas


async def test_js_sin_playwright_da_un_error_claro(
    sitio: Sitio, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from monitor_precios.navegador import PlaywrightNoInstalado

    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    with pytest.raises(PlaywrightNoInstalado, match=r"monitor-precios\[js\]"):
        await consultar(hacer_config(libro(js=True)), transport=sitio.transport)
