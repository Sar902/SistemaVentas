import logging

from django.db import connection


logger = logging.getLogger(__name__)


def _write_tenant_context(value):
    """Escribe el contexto de sesión usado por las políticas RLS."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config(%s, %s, false)",
            ["app.miscelanea_id", value],
        )


def set_tenant_context(tenant_id):
    """Activa el tenant autenticado; una cuenta técnica usa contexto vacío."""
    _write_tenant_context(str(tenant_id) if tenant_id is not None else "")


def clear_tenant_context():
    """Vacía el contexto o descarta la conexión si no puede garantizarlo."""
    try:
        _write_tenant_context("")
    except Exception:
        logger.exception("No se pudo limpiar el contexto tenant de PostgreSQL")
        try:
            connection.close()
        except Exception:
            logger.exception(
                "No se pudo descartar la conexión tras fallar la limpieza tenant"
            )
        return False
    return True


class TenantContextMiddleware:
    """Delimita el contexto RLS de la conexión al ciclo de cada petición."""

    sync_capable = True
    async_capable = False

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        clear_tenant_context()
        try:
            return self.get_response(request)
        finally:
            # Nunca sustituye la respuesta o excepción original. Si PostgreSQL
            # rechaza la limpieza, clear_tenant_context descarta la conexión.
            clear_tenant_context()
