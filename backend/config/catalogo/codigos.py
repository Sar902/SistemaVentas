import re

from django.core.exceptions import ValidationError


LONGITUD_MAXIMA_CODIGO_PRODUCTO = 64
PATRON_CODIGO_PRODUCTO = re.compile(r"^[A-Za-z0-9._/+%$-]+$")
MENSAJE_CODIGO_INVALIDO = (
    "El código solo puede contener letras ASCII, números y los símbolos "
    ". _ / + % $ -."
)


def normalizar_codigo_producto(value):
    """Quita únicamente espacios exteriores y conserva el contenido restante."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("El código debe ser texto.")
    normalized = value.strip()
    return normalized or None


def validar_codigo_producto(value):
    """Valida el subconjunto de caracteres emitible por lectores tipo teclado."""
    if value in (None, ""):
        return
    if len(value) > LONGITUD_MAXIMA_CODIGO_PRODUCTO:
        raise ValidationError(
            f"El código no puede superar {LONGITUD_MAXIMA_CODIGO_PRODUCTO} caracteres."
        )
    if not PATRON_CODIGO_PRODUCTO.fullmatch(value):
        raise ValidationError(MENSAJE_CODIGO_INVALIDO)


def limpiar_y_validar_codigo_producto(value):
    normalized = normalizar_codigo_producto(value)
    validar_codigo_producto(normalized)
    return normalized


def tiene_checksum_gtin_valido(value):
    """Comprueba el dígito de control de un UPC-A o EAN-13 numérico ASCII."""
    if not isinstance(value, str) or len(value) not in (12, 13):
        return False
    if not value.isascii() or not value.isdigit():
        return False

    total = sum(
        int(digit) * (3 if index % 2 == 0 else 1)
        for index, digit in enumerate(reversed(value[:-1]))
    )
    expected = (10 - total % 10) % 10
    return expected == int(value[-1])


def variantes_equivalentes_codigo_producto(value):
    """Devuelve solo la pareja válida UPC-A/EAN-13, conservando el original."""
    if not tiene_checksum_gtin_valido(value):
        return (value,)
    if len(value) == 12:
        return (value, f"0{value}")
    if len(value) == 13 and value.startswith("0"):
        return (value, value[1:])
    return (value,)
