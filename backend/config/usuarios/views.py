"""
Vistas de Usuario — App 'usuarios'
=====================================

Expone los endpoints REST para gestión de cuentas de usuario.

DISEÑO DE SEGURIDAD:
    Todos los endpoints de este módulo están protegidos por IsAdminRole,
    ya que la gestión de cuentas (crear, editar, desactivar usuarios) es una
    operación administrativa sensible. Un vendedor no puede crear ni ver
    otras cuentas del sistema.

ENDPOINTS:
    GET    /api/usuarios/           → Listar todos los usuarios
    POST   /api/usuarios/           → Crear nuevo usuario
    GET    /api/usuarios/<id>/      → Detalle de un usuario
    PUT    /api/usuarios/<id>/      → Actualización completa
    PATCH  /api/usuarios/<id>/      → Actualización parcial (ej: solo cambiar estado)
    DELETE /api/usuarios/<id>/      → Eliminar usuario
    PATCH  /api/usuarios/<id>/cambiar-email/ → Cambio directo de email
"""

from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
import re
import secrets
from datetime import timedelta
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from rest_framework.response import Response

from .models import DelegacionAdmin, Usuario
from .serializers import UsuarioSerializer
from .permissions import CanManageUserProfile, IsAdminRole, has_admin_access
from auditoria.mixins import AuditoriaMixin, registrar_evento_manual
from .tenant import TenantScopedViewSet


class CurrentUserView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        tenant = user.Miscelanea
        logo = None
        if tenant and tenant.Logo:
            logo = request.build_absolute_uri(tenant.Logo.url)
        logo_oscuro = None
        if tenant and tenant.LogoOscuro:
            logo_oscuro = request.build_absolute_uri(tenant.LogoOscuro.url)

        return Response({
            'id': user.IdUsuario,
            'nombre': user.Nombre,
            'rol': user.Rol,
            'es_admin': user.Rol == 'admin' or DelegacionAdmin.objects.filter(
                Empleado=user,
                Miscelanea=tenant,
                UsadoEn__isnull=False,
                ConcedidoHasta__gt=timezone.now(),
            ).exists(),
            'miscelanea': {
                'nombre': tenant.Nombre if tenant else 'Sistema de Ventas',
                'codigo': tenant.Codigo if tenant else '',
                'logo': logo,
                'logo_claro': logo,
                'logo_oscuro': logo_oscuro,
            },
        })


class MiscelaneaBrandingView(APIView):
    """
    Endpoint para consultar y actualizar el branding (logos claro/oscuro y nombre)
    de la miscelánea autenticada.
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        tenant = request.user.Miscelanea
        if not tenant:
            return Response({'error': 'El usuario no tiene una miscelánea asignada.'}, status=404)
        return Response({
            'id': tenant.IdMiscelanea,
            'nombre': tenant.Nombre,
            'codigo': tenant.Codigo,
            'logo': request.build_absolute_uri(tenant.Logo.url) if tenant.Logo else None,
            'logo_claro': request.build_absolute_uri(tenant.Logo.url) if tenant.Logo else None,
            'logo_oscuro': request.build_absolute_uri(tenant.LogoOscuro.url) if tenant.LogoOscuro else None,
        })

    def patch(self, request):
        tenant = request.user.Miscelanea
        if not tenant:
            return Response({'error': 'El usuario no tiene una miscelánea asignada.'}, status=404)

        if 'logo' in request.FILES:
            tenant.Logo = request.FILES['logo']
        elif 'logo_claro' in request.FILES:
            tenant.Logo = request.FILES['logo_claro']

        if 'logo_oscuro' in request.FILES:
            tenant.LogoOscuro = request.FILES['logo_oscuro']

        if 'nombre' in request.data and str(request.data['nombre']).strip():
            tenant.Nombre = str(request.data['nombre']).strip()

        tenant.save()
        return Response({
            'mensaje': 'Branding actualizado correctamente.',
            'nombre': tenant.Nombre,
            'logo': request.build_absolute_uri(tenant.Logo.url) if tenant.Logo else None,
            'logo_claro': request.build_absolute_uri(tenant.Logo.url) if tenant.Logo else None,
            'logo_oscuro': request.build_absolute_uri(tenant.LogoOscuro.url) if tenant.LogoOscuro else None,
        })


class CrearCodigoAdminView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request):
        empleado_id = request.data.get('empleadoId')
        try:
            duracion_horas = int(request.data.get('duracionHoras', 1))
        except (TypeError, ValueError):
            return Response({'error': 'La duración debe ser un número de horas.'}, status=400)

        if not 1 <= duracion_horas <= 24:
            return Response({'error': 'La duración debe estar entre 1 y 24 horas.'}, status=400)

        duracion = duracion_horas * 60

        try:
            empleado = Usuario.objects.get(
                IdUsuario=empleado_id,
                Miscelanea_id=request.user.Miscelanea_id,
                Rol__in=('cajero', 'vendedor'),
            )
        except Usuario.DoesNotExist:
            return Response({'error': 'El empleado no pertenece a esta miscelánea.'}, status=404)

        codigo = secrets.token_urlsafe(6).replace('-', '').replace('_', '').upper()[:8]
        delegacion = DelegacionAdmin.objects.create(
            Miscelanea_id=request.user.Miscelanea_id,
            CreadoPor=request.user,
            Empleado=empleado,
            CodigoHash=make_password(codigo),
            DuracionMinutos=duracion,
            CodigoExpiraEn=timezone.now() + timedelta(minutes=10),
        )
        return Response({
            'id': delegacion.IdDelegacion,
            'codigo': codigo,
            'empleado': empleado.Nombre,
            'expiraCodigoEn': delegacion.CodigoExpiraEn,
            'duracionHoras': duracion_horas,
        }, status=201)


class ActivarCodigoAdminView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        codigo = str(request.data.get('codigo', '')).strip().upper()
        if not codigo:
            return Response({'error': 'Debe ingresar un código.'}, status=400)

        delegaciones = DelegacionAdmin.objects.filter(
            Empleado=request.user,
            Miscelanea_id=request.user.Miscelanea_id,
            UsadoEn__isnull=True,
            CodigoExpiraEn__gt=timezone.now(),
        )
        delegacion = next((item for item in delegaciones if check_password(codigo, item.CodigoHash)), None)
        if not delegacion:
            return Response({'error': 'Código inválido o expirado.'}, status=400)

        ahora = timezone.now()
        delegacion.UsadoEn = ahora
        delegacion.ConcedidoHasta = ahora + timedelta(minutes=delegacion.DuracionMinutos)
        delegacion.save(update_fields=['UsadoEn', 'ConcedidoHasta'])
        return Response({'mensaje': 'Permisos administrativos activados.', 'adminHasta': delegacion.ConcedidoHasta})


class UsuarioViewSet(AuditoriaMixin, TenantScopedViewSet, viewsets.ModelViewSet):
    """
    ViewSet completo para operaciones CRUD sobre el modelo Usuario.

    Usa ModelViewSet porque provee automáticamente los 5 endpoints estándar
    REST (list, create, retrieve, update, destroy) con una sola clase.
    Esto reduce el código boilerplate y mantiene la consistencia de la API.

    Todos los métodos requieren Rol == 'admin'. Un vendedor que intente
    acceder recibirá un HTTP 403 Forbidden.
    """
    # ordering por IdUsuario garantiza paginación estable (sin UnorderedObjectListWarning)
    queryset = Usuario.objects.all().order_by('IdUsuario')
    serializer_class = UsuarioSerializer
    permission_classes = [CanManageUserProfile]
    MODULO_AUDITORIA = 'USUARIOS'

    def perform_update(self, serializer):
        if not has_admin_access(self.request.user):
            for field in ('Rol', 'Estado', 'Miscelanea', 'is_staff', 'is_superuser'):
                serializer.validated_data.pop(field, None)
        serializer.save()


@api_view(['PATCH'])
@permission_classes([IsAdminRole])
def cambiar_email_directo(request, user_id):
    """
    Endpoint de emergencia para cambio directo de email por un administrador.

    POR QUÉ EXISTE ESTE ENDPOINT SEPARADO:
        El flujo normal de cambio de email (a través de UsuarioSerializer)
        incluye validaciones de "nuevo email ≠ email actual" que pueden
        interferir en casos de corrección de errores de escritura.
        Este endpoint permite al admin corregir un email incorrecto
        directamente, con solo validación de formato.

    Seguridad: Solo accesible para usuarios con Rol == 'admin'.
    Método HTTP: PATCH (actualización parcial, solo el campo email).

    Args:
        request: Objeto de petición DRF. request.data debe contener {'email': '...'}.
        user_id (int): ID del usuario cuyo email se modificará.

    Returns:
        Response: JSON con mensaje de confirmación y el nuevo email.
            - 200 OK: Cambio exitoso.
            - 400 Bad Request: Email no proporcionado o formato inválido.
            - 404 Not Found: Usuario con user_id no encontrado.
            - 500 Internal Server Error: Error inesperado del servidor.

    Raises:
        No lanza excepciones directamente; las captura y las devuelve como
        errores HTTP estructurados.
    """
    try:
        user = Usuario.objects.get(
            IdUsuario=user_id,
            Miscelanea_id=request.user.Miscelanea_id,
        )
        nuevo_email = request.data.get('email')

        if not nuevo_email:
            return Response({"error": "Debe proporcionar un nuevo correo"}, status=400)

        # Validación de formato con regex básica.
        # Se usa una expresión regular simple en lugar de EmailValidator de Django
        # porque este endpoint es de uso administrativo rápido y no crítico.
        regex = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
        if not re.match(regex, nuevo_email):
            return Response({"error": "Correo no válido"}, status=400)

        email_anterior = user.Email
        # Guardar directamente sin pasar por el serializer (sin validaciones extra)
        user.Email = nuevo_email
        user.save()

        # Registrar la acción en el log de auditoría
        registrar_evento_manual(
            request=request,
            accion='MODIFICAR',
            modulo='USUARIOS',
            descripcion=f'Cambio directo de email del usuario ID {user_id}: {email_anterior} → {nuevo_email}',
            datos_anteriores={'email': email_anterior},
            datos_nuevos={'email': nuevo_email},
        )

        return Response({"mensaje": "Correo actualizado correctamente", "email": user.Email})

    except Usuario.DoesNotExist:
        return Response({"error": "Usuario no encontrado"}, status=404)

    except Exception as e:
        # Captura genérica para errores inesperados (ej: violación de unicidad en BD)
        return Response({"error": str(e)}, status=500)