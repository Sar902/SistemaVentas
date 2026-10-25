"""
Serializadores de Auditoría — App 'auditoria'
===============================================

Solo se define un serializador de LECTURA para LogAuditoria.
Los logs no se crean desde la API externa — solo se leen por los administradores.
La creación ocurre internamente a través del AuditoriaMixin y registrar_evento_manual.
"""

from rest_framework import serializers
from .models import LogAuditoria


REDACTED_AUDIT_VALUE = '[REDACTADO]'
_SENSITIVE_KEY_PARTS = (
    'password',
    'passwd',
    'contrasena',
    'credential',
    'secret',
    'token',
    'hash',
    'authorization',
    'api_key',
    'apikey',
)
_SENSITIVE_EXACT_KEYS = {'access', 'refresh'}
_SENSITIVE_EXACT_KEYS.update({'emailpendiente', 'email_pendiente'})


def _normalized_key(key):
    return str(key).strip().lower().replace('-', '_')


def _is_sensitive_audit_key(key):
    normalized = _normalized_key(key)
    return (
        normalized in _SENSITIVE_EXACT_KEYS
        or any(part in normalized for part in _SENSITIVE_KEY_PARTS)
    )


def sanitize_audit_payload(value):
    """Redacta secretos en estructuras de auditoría sin mutar el valor recibido."""
    if isinstance(value, dict):
        return {
            key: (
                REDACTED_AUDIT_VALUE
                if _is_sensitive_audit_key(key)
                else sanitize_audit_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_audit_payload(item) for item in value]
    return value


class LogAuditoriaSerializer(serializers.ModelSerializer):
    """
    Serializador de solo lectura para LogAuditoria.

    Expone todos los campos relevantes para la vista de auditoría en el frontend.
    El campo `timestamp` se formatea con timezone para coherencia con el frontend.
    """

    datos_anteriores = serializers.SerializerMethodField()
    datos_nuevos = serializers.SerializerMethodField()

    def get_datos_anteriores(self, obj):
        return sanitize_audit_payload(obj.datos_anteriores)

    def get_datos_nuevos(self, obj):
        return sanitize_audit_payload(obj.datos_nuevos)

    class Meta:
        model = LogAuditoria
        fields = [
            'IdLog',
            'nombre_usuario',
            'accion',
            'modulo',
            'descripcion',
            'ip_address',
            'user_agent',
            'datos_anteriores',
            'datos_nuevos',
            'resultado',
            'timestamp',
        ]
        read_only_fields = fields
