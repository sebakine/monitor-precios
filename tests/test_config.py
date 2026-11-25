from decimal import Decimal
from pathlib import Path

import pytest

from monitor_precios.config import Ajustes, ConfigInvalida, cargar, desde_dict

from .conftest import dolar, libro

RAIZ = Path(__file__).parent.parent


def test_el_ejemplo_del_repositorio_es_valido() -> None:
    config = cargar(RAIZ / "productos.toml")
    assert len(config.productos) >= 3
    assert {p.tipo for p in config.productos} == {"html", "json"}


def test_valores_por_defecto() -> None:
    config = desde_dict({"producto": [libro()]})
    p = config.productos[0]
    assert config.ajustes == Ajustes()
    assert (p.tipo, p.js, p.umbral, p.baja_pct, p.decimal) == ("html", False, None, None, None)
    assert p.host == "books.toscrape.com"


def test_umbral_y_baja_se_guardan_como_decimal_exacto() -> None:
    p = desde_dict({"producto": [libro(umbral=49.9, baja_pct=5)]}).productos[0]
    assert p.umbral == Decimal("49.9")
    assert p.baja_pct == Decimal("5")


def test_la_moneda_se_normaliza_a_mayusculas() -> None:
    assert desde_dict({"producto": [libro(moneda="gbp")]}).productos[0].moneda == "GBP"


def test_lista_todos_los_errores_juntos() -> None:
    datos = {
        "ajustes": {"concurrencia": 0, "timeout": "rápido", "otro": 1, "user_agent": ""},
        "producto": [
            {"id": "Con Espacios", "url": "ftp://x", "selector": "p"},
            libro(tipo="xml"),
            dolar(ruta=None, js=True),
            libro(id="b", selector=None, umbral=-1, baja_pct=150, decimal=";", color="rojo"),
            libro(id="c", nombre="  "),
            "no soy tabla",
        ],
    }
    with pytest.raises(ConfigInvalida) as error:
        desde_dict(datos)
    mensaje = str(error.value)
    for esperado in [
        "'concurrencia' debe ser un número >= 1",
        "'timeout' debe ser un número",
        "campos desconocidos otro",
        "'user_agent' debe ser texto",
        "'id' debe ser minúsculas",
        "'url' debe empezar con http",
        "'tipo' debe ser html o json",
        "el tipo json necesita 'ruta'",
        "'js' solo aplica al tipo html",
        "el tipo html necesita 'selector'",
        "'umbral' debe ser un número mayor que 0",
        "'baja_pct' debe ser menor que 100",
        "'decimal' debe ser '.' o ','",
        "campos desconocidos color",
        "'nombre' debe ser texto",
        "producto 6: debe ser una tabla",
    ]:
        assert esperado in mensaje


def test_ids_repetidos() -> None:
    with pytest.raises(ConfigInvalida, match="está repetido"):
        desde_dict({"producto": [libro(), libro()]})


@pytest.mark.parametrize("datos", [{}, {"producto": []}, {"producto": "x"}])
def test_sin_productos(datos: dict[str, object]) -> None:
    with pytest.raises(ConfigInvalida, match="al menos un"):
        desde_dict(datos)


def test_ajustes_deben_ser_tabla() -> None:
    with pytest.raises(ConfigInvalida, match=r"\[ajustes\] debe ser una tabla"):
        desde_dict({"ajustes": 3, "producto": [libro()]})


def test_baja_pct_no_acepta_booleanos() -> None:
    with pytest.raises(ConfigInvalida, match="baja_pct"):
        desde_dict({"producto": [libro(baja_pct=True)]})


def test_archivo_inexistente_o_mal_formado(tmp_path: Path) -> None:
    with pytest.raises(ConfigInvalida, match="no existe"):
        cargar(tmp_path / "nada.toml")
    roto = tmp_path / "roto.toml"
    roto.write_text("[[producto]\nid = 1", encoding="utf-8")
    with pytest.raises(ConfigInvalida, match="TOML inválido"):
        cargar(roto)
