"""
Permisos Personalizados — App 'usuarios'
=========================================

Este módulo implementa el sistema de Control de Acceso Basado en Roles (RBAC)
del sistema. Django REST Framework permite definir permisos granulares
sobrescribiendo la clase BasePermission.

POR QUÉ PERMISOS PERSONALIZADOS:
    Los permisos estándar de DRF (IsAuthenticated, IsAdminUser) solo verifican
    si un usuario está autenticado o es un superusuario de Django. No tienen
    concepto de "rol de negocio" (admin vs vendedor). Estos permisos personalizados
    leen el campo 'Rol' de nuestro modelo Usuario para tomar decisiones más
    granulares y específicas del dominio de la miscelánea.

JERARQUÍA DE PERMISOS:
    IsAdminRole       → Solo administradores (reportes, anular ventas, gestión de usuarios)
    IsAdminOrReadOnly → Admins: todo. Cajeros: solo lectura (GET, HEAD, OPTIONS).
    CanProcessSale    → Admins y Cajeros: pueden registrar ventas.
"""

from rest_framework import permissions
from django.conf import settings
from django.utils import timezone
from .models import DelegacionAdmin


def is_technical_admin(user):
    """Use exactly the identity conditions enforced by Django Admin."""
    return bool(
        user
        and user.is_active
        and user.is_staff
        and user.Email.lower() == settings.DJANGO_ADMIN_EMAIL
    )


class IsTechnicalAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return is_technical_admin(request.user)


class HasTenant(permissions.BasePermission):
    """Exige tenant para APIs administrativas del negocio, no para rutas técnicas."""

    message = 'La cuenta no tiene una miscelánea asignada.'

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        return bool(
            user
            and user.is_authenticated
            and getattr(user, 'Miscelanea_id', None) is not None
        )


def _delegacion_activa(user):
    return DelegacionAdmin.objects.filter(
        Empleado_id=getattr(user, 'IdUsuario', None),
        Miscelanea_id=getattr(user, 'Miscelanea_id', None),
        UsadoEn__isnull=False,
        Revocado=False,
        ConcedidoHasta__gt=timezone.now(),
    )


def has_module_access(user, modulo):
    if getattr(user, 'Rol', None) == 'admin':
        return True
    if not modulo:
        return False
    return any(modulo in (delegacion.Modulos or []) for delegacion in _delegacion_activa(user))


def has_admin_access(user):
    return has_module_access(user, 'SISTEMA') or _delegacion_activa(user).exists()


def module_for_view(view):
    """Resolve the business module required by a view for delegated admins."""
    explicit = getattr(view, 'MODULO_PERMISO', None)
    if explicit:
        return explicit
    name = view.__class__.__name__.lower()
    if 'auditoria' in name or name in ('estadobdview', 'sesionesactivasview'):
        return 'AUDITORIA'
    if 'usuario' in name or 'branding' in name:
        return 'USUARIOS'
    if 'reporte' in name or name == 'productosporproveedorview':
        return 'REPORTES'
    if any(word in name for word in ('inventario', 'perdida', 'compras', 'devolucion', 'stock')):
        return 'INVENTARIO' if 'inventario' in name or 'compras' in name else (
            'PERDIDAS' if 'perdida' in name else 'DEVOLUCIONES'
        )
    if 'reporte' in name or 'dashboard' in name:
        return 'REPORTES'
    return getattr(view, 'MODULO_AUDITORIA', 'SISTEMA')


class IsAdminRole(permissions.BasePermission):
    """
    Permiso que restringe el acceso exclusivamente a usuarios con Rol == 'admin'.

    Uso típico:
        - Reportes gerenciales (datos financieros sensibles).
        - Gestión de usuarios (crear, editar, eliminar cuentas).
        - Anulación de ventas (operación irreversible de alto impacto).
        - Gestión de entradas de inventario y pérdidas.

    El uso de `getattr(request.user, 'Rol', None)` es una práctica defensiva:
    si por algún motivo el objeto `request.user` no tiene el atributo 'Rol'
    (ej: usuario anónimo, o usuario del admin de Django sin ese campo),
    devuelve None en lugar de lanzar un AttributeError.
    """

    def has_permission(self, request, view):
        """
        Evalúa si el usuario actual tiene permiso de acceso.

        Returns:
            bool: True si el usuario está autenticado Y su Rol es 'admin'.
        """
        return (
            request.user is not None
            and request.user.is_authenticated
            and has_module_access(request.user, module_for_view(view))
        )


class CanManageUserProfile(permissions.BasePermission):
    """Admins manage tenant users; other users may manage only themselves."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if has_module_access(request.user, 'USUARIOS'):
            return True
        user_id = view.kwargs.get('pk')
        return user_id is not None and str(user_id) == str(request.user.IdUsuario) and request.method in ('GET', 'PATCH', 'PUT')


class IsAdminOrReadOnly(permissions.BasePermission):
    """
    Permiso mixto para endpoints donde vendedores solo consultan, pero no modifican.

    Lógica de negocio:
        - Un vendedor necesita VER el catálogo de productos para registrar una venta.
        - Pero NO debe poder CREAR ni MODIFICAR productos (solo un admin puede).
        - Esta clase implementa ese contrato de forma declarativa.

    SAFE_METHODS en DRF son: GET, HEAD, OPTIONS.
    Todos los demás (POST, PUT, PATCH, DELETE) son considerados "de escritura".

    Cualquier usuario autenticado sin un rol reconocido ('admin' o 'vendedor')
    es denegado. Esto aplica el principio de "deny by default".
    """

    def has_permission(self, request, view):
        """
        Evalúa si el usuario puede acceder al recurso.

        Returns:
            bool: True si es admin (acceso total) o vendedor con método seguro.
        """
        if not request.user or not request.user.is_authenticated:
            return False

        rol = getattr(request.user, 'Rol', None)

        if has_module_access(request.user, module_for_view(view)):
            return True  # Acceso total a todos los métodos HTTP

        # Los vendedores solo pueden hacer peticiones de solo lectura
        if rol in ('cajero', 'vendedor') and request.method in permissions.SAFE_METHODS:
            return True

        # Cualquier otro caso (rol desconocido, método de escritura para vendedor)
        return False


class CanProcessSale(permissions.BasePermission):
    """
    Permiso granular para el endpoint de facturación (POST /api/ventas/procesar/).

    Permite el acceso a AMBOS roles porque el negocio requiere que los
    vendedores puedan registrar ventas directamente, no solo los administradores.

    A diferencia de IsAdminOrReadOnly, este permiso NO distingue entre métodos
    seguros y de escritura: si tienes el rol correcto, puedes hacer cualquier
    petición al endpoint protegido.
    """

    def has_permission(self, request, view):
        """
        Evalúa si el usuario puede procesar ventas.

        Returns:
            bool: True si el usuario está autenticado y es 'admin' o 'vendedor'.
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # Uso de `in` para verificar pertenencia al conjunto de roles autorizados.
        # Es más limpio y extensible que dos comparaciones == separadas.
        return getattr(request.user, 'Rol', None) in ('admin', 'cajero', 'vendedor')
