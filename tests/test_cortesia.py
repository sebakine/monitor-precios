import httpx
import pytest

from monitor_precios.cortesia import LimitePorSitio, Robots

from .conftest import Sitio

pytestmark = pytest.mark.anyio

ROBOTS = "https://tienda.cl/robots.txt"
UA = "monitor-precios/0.1"


async def _robots(sitio: Sitio) -> Robots:
    return Robots(httpx.AsyncClient(transport=sitio.transport), UA)


async def test_respeta_disallow_y_crawl_delay() -> None:
    sitio = Sitio().html(ROBOTS, "User-agent: *\nDisallow: /carro/\nCrawl-delay: 4\n")
    robots = await _robots(sitio)
    assert await robots.permite("https://tienda.cl/producto/1")
    assert not await robots.permite("https://tienda.cl/carro/ver")
    assert await robots.espera_minima("https://tienda.cl/producto/1") == 4
    assert sitio.veces(ROBOTS) == 1  # se descarga una vez por sitio


async def test_sin_crawl_delay_la_espera_es_cero() -> None:
    robots = await _robots(Sitio().html(ROBOTS, "User-agent: *\nAllow: /\n"))
    assert await robots.espera_minima("https://tienda.cl/x") == 0


async def test_404_permite_todo() -> None:
    robots = await _robots(Sitio())
    assert await robots.permite("https://tienda.cl/lo-que-sea")


@pytest.mark.parametrize("status", [401, 403, 500, 503])
async def test_acceso_denegado_o_error_del_servidor_bloquea(status: int) -> None:
    robots = await _robots(Sitio().html(ROBOTS, "", status=status))
    assert not await robots.permite("https://tienda.cl/producto/1")


async def test_error_de_red_bloquea() -> None:
    robots = await _robots(Sitio().agregar(ROBOTS, httpx.ConnectError("sin red")))
    assert not await robots.permite("https://tienda.cl/producto/1")


async def test_reglas_para_un_user_agent_especifico() -> None:
    sitio = Sitio().html(
        ROBOTS, "User-agent: monitor-precios\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    )
    robots = await _robots(sitio)
    assert not await robots.permite("https://tienda.cl/producto/1")


class Reloj:
    def __init__(self) -> None:
        self.ahora = 100.0
        self.dormido: list[float] = []

    def __call__(self) -> float:
        return self.ahora

    async def dormir(self, segundos: float) -> None:
        self.dormido.append(segundos)
        self.ahora += segundos


async def test_limite_espera_entre_consultas_al_mismo_sitio() -> None:
    reloj = Reloj()
    limite = LimitePorSitio(2.0, reloj=reloj, dormir=reloj.dormir)
    await limite.esperar_turno("a.cl")
    reloj.ahora += 0.5
    await limite.esperar_turno("a.cl")
    await limite.esperar_turno("b.cl")  # otro sitio: no espera
    await limite.esperar_turno("a.cl", minimo=5)  # crawl-delay mayor gana
    assert reloj.dormido == [1.5, 5.0]


async def test_limite_no_espera_si_ya_paso_el_tiempo() -> None:
    reloj = Reloj()
    limite = LimitePorSitio(1.0, reloj=reloj, dormir=reloj.dormir)
    await limite.esperar_turno("a.cl")
    reloj.ahora += 3
    await limite.esperar_turno("a.cl")
    assert reloj.dormido == []
