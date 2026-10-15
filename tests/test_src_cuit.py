"""cuit_validar — validación local, sin red."""

from __future__ import annotations

from arg_legal_mcp.sources import cuit


def test_cuit_valido_con_y_sin_guiones():
    # 33-69345023-9 es el CUIT de la propia AFIP/ARCA.
    for entrada in ("33-69345023-9", "33693450239", " 33 69345023 9 "):
        out = cuit.validar(entrada)
        assert out["valido"] is True
        assert out["cuit"] == "33-69345023-9"
        assert out["tipo_persona"] == "juridica"
        assert out["prefijo"] == "33"


def test_cuit_persona_fisica_y_dni():
    out = cuit.validar("20-12345678-6")
    assert out["valido"] is True
    assert out["tipo_persona"] == "fisica"
    assert out["dni"] == "12345678"


def test_cuit_digito_verificador_incorrecto():
    out = cuit.validar("20-12345678-0")
    assert out["valido"] is False
    assert out["digito_verificador_esperado"] == 6
    assert "6" in out["motivo"]


def test_cuit_largo_y_caracteres_invalidos():
    assert cuit.validar("123")["valido"] is False
    assert "11 digitos" in cuit.validar("123")["motivo"]
    assert cuit.validar("20-1234567X-6")["valido"] is False
    assert "error" in cuit.validar("")
    assert "error" in cuit.validar(None)  # type: ignore[arg-type]


def test_cuit_prefijo_desconocido_es_indeterminado():
    dv = cuit.digito_verificador("5500000001")
    assert dv is not None
    out = cuit.validar(f"5500000001{dv}")
    assert out["valido"] is True
    assert out["tipo_persona"] == "indeterminado"


def test_cuit_valido_helper():
    assert cuit.cuit_valido("33693450239")
    assert not cuit.cuit_valido("33693450230")
    assert not cuit.cuit_valido("abc")
