"""Prueba real con Chromium: una página que pinta el precio con JavaScript."""

import threading
from collections.abc import Iterator
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from monitor_precios.consulta import consultar

from .conftest import hacer_config, libro

pytest.importorskip("playwright.async_api")
pytestmark = [pytest.mark.anyio, pytest.mark.js]

PAGINA = b"""<!doctype html><html><body><div id="precio">cargando...</div>
<script>
  setTimeout(() => { document.getElementById("precio").textContent = "$ 24.990"; }, 50);
</script></body></html>"""


class Manejador(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGINA)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def servidor() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


async def test_renderiza_javascript_con_chromium(servidor: str) -> None:
    url = f"{servidor}/producto"
    sin_js, con_js = await consultar(
        hacer_config(
            libro(id="sin-js", url=url, selector="#precio", moneda="CLP"),
            libro(id="con-js", url=url, selector="#precio", moneda="CLP", js=True),
        )
    )
    assert sin_js.error is not None  # sin navegador solo se ve "cargando..."
    assert con_js.precio == Decimal(24990)
