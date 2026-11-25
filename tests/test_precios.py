from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from monitor_precios.precios import PrecioInvalido, parsear_precio


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("£51.77", "51.77"),
        ("$12.990", "12990"),
        ("$ 1.299.990", "1299990"),
        ("CLP 1.299.990,50", "1299990.50"),
        ("US$1,299.99", "1299.99"),
        ("1.299,90 €", "1299.90"),
        ("Precio: 4,5", "4.5"),
        ("1,000,000", "1000000"),
        ("990", "990"),
        ("Oferta 19 990 CLP", "19990"),
        ("Total: 1 234,56 €.", "1234.56"),
        ("$12.990.", "12990"),
    ],
)
def test_parsea_formatos_comunes(texto: str, esperado: str) -> None:
    assert parsear_precio(texto) == Decimal(esperado)


def test_el_separador_explicito_gana_a_la_heuristica() -> None:
    # "1.299" sería mil doscientos noventa y nueve en Chile, pero el sitio dice que "." es decimal.
    assert parsear_precio("1.299", decimal=".") == Decimal("1.299")
    assert parsear_precio("12,990", decimal=",") == Decimal("12.990")
    assert parsear_precio("1.299.990", decimal=",") == Decimal("1299990")


@pytest.mark.parametrize("texto", ["", "Agotado", "precio a convenir", "-$5.000", "- 12"])
def test_rechaza_textos_sin_precio_valido(texto: str) -> None:
    with pytest.raises(PrecioInvalido):
        parsear_precio(texto)


@given(st.integers(min_value=0, max_value=10**10))
def test_formato_chileno_sin_decimales_ida_y_vuelta(n: int) -> None:
    texto = "$" + f"{n:,}".replace(",", ".")
    assert parsear_precio(texto, decimal=",") == n


@given(st.decimals(min_value=0, max_value=10**8, places=2, allow_nan=False))
def test_formato_ingles_con_decimales_ida_y_vuelta(valor: Decimal) -> None:
    assert parsear_precio(f"£{valor:,.2f}", decimal=".") == valor
