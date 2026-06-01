"""
Token JWT Personalizado — App 'usuarios'
=========================================

Este módulo extiende el serializador de tokens estándar de simplejwt para
enriquecer el payload del JWT con datos adicionales del usuario.

POR QUÉ PERSONALIZAR EL TOKEN:
    El token JWT estándar solo contiene el ID del usuario (user_id).
    El frontend necesita conocer el ROL y el NOMBRE del usuario para:
      1. Renderizar el nombre en el header (DashboardLayout.tsx).
      2. Determinar qué items del menú mostrar (admin vs vendedor).
      3. Proteger rutas de admin en el frontend (AdminRoute.tsx).

    Sin estos claims adicionales, el frontend tendría que hacer una petición
    extra a /api/usuarios/me/ en cada inicio de sesión, aumentando la latencia.
    Al incluirlos en el token, el frontend puede leerlos instantáneamente con
    jwt-decode() sin necesidad de un round-trip al servidor.

SEGURIDAD:
    Los claims personalizados ('rol', 'nombre') son parte del payload del JWT,
    que puede ser leído por cualquiera que tenga el token (aunque no modificado
    sin la SECRET_KEY). Por eso, estos campos deben ser datos no sensibles.
    Nunca incluir contraseñas, números de tarjeta u otros secretos en el token.
"""

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from datetime import timedelta
from math import ceil


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Serializador JWT personalizado que añade claims de negocio al token.

    Hereda de TokenObtainPairSerializer para mantener toda la lógica de
    autenticación estándar (verificación de credenciales, generación de
    access y refresh token) y solo sobreescribe la construcción del payload.
    """

    @classmethod
    def get_token(cls, user):
        """
        Genera el token JWT y añade claims personalizados al payload.

        Se llama automáticamente después de que las credenciales del usuario
        son verificadas. Recibe el objeto usuario de la BD y devuelve el
        token enriquecido.

        Args:
            user (Usuario): Instancia del modelo Usuario autenticado.

        Returns:
            Token: Objeto de token de simplejwt con claims adicionales.
                   El payload resultante tendrá la forma:
                   {
                       "token_type": "access",
                       "exp": 1234567890,
                       "user_id": 1,
                       "rol": "admin",        ← CLAIM PERSONALIZADO
                       "nombre": "Juan López"  ← CLAIM PERSONALIZADO
                   }
        """
        # Llamar al método padre primero para obtener el token base con
        # los claims estándar (token_type, exp, iat, jti, user_id).
        token = super().get_token(user)

        # Añadir claims de negocio que el frontend leerá en AuthContext.tsx
        # con jwtDecode(token).rol y jwtDecode(token).nombre
        token['rol'] = user.Rol
        token['nombre'] = user.Nombre

        return token

    def validate(self, attrs):
        """
        Flujo de validación de seguridad en 5 pasos antes de emitir tokens.

        Pasos:
            A) Verificar que el usuario exista en la BD.
            B) Verificar que la cuenta esté activa (Estado == 'Activo').
            C) Verificar si hay un bloqueo temporal activo y cuánto falta.
            D) Validar contraseña; acumular intentos y bloquear al llegar a 3.
            E) Resetear contador y emitir tokens directamente con get_token().

        DECISIÓN DE DISEÑO — Por qué NO usamos super().validate():
            TokenObtainPairSerializer.validate() llama internamente a
            authenticate(), que volvería a verificar la contraseña por segunda
            vez. Eso generaría una condición de carrera: nuestro paso D ya
            determinó si la contraseña es válida; una segunda verificación es
            redundante y rompe el flujo de intentos fallidos. En su lugar,
            usamos get_token() directamente una vez que NOSOTROS validamos todo.

        Args:
            attrs (dict): Credenciales del request {'Email': ..., 'password': ...}.

        Returns:
            dict: {'refresh': str, 'access': str} con los tokens JWT firmados.

        Raises:
            AuthenticationFailed: En cualquier paso A–D si la validación falla.
        """
        # Import local obligatorio: evita circular import al arranque de Django
        # (usuarios.token_serializers → usuarios.models → al inicializarse)
        from usuarios.models import Usuario

        email = attrs.get(self.username_field)  # Campo configurado como 'Email'

        # ── PASO A: Verificar existencia del usuario ─────────────────────────
        try:
            usuario = Usuario.objects.get(Email=email)
        except Usuario.DoesNotExist:
            # Mensaje genérico: no revelar si el email existe o no en el sistema.
            raise AuthenticationFailed(
                'Credenciales incorrectas. Verifica tu email y contraseña.'
            )

        # ── PASO B: Verificar que la cuenta esté activa ──────────────────────
        # Doble guarda: campo 'Estado' del negocio Y flag 'is_active' de Django.
        # Un usuario puede tener is_active=True pero Estado='Inactivo' si fue
        # desactivado manualmente desde el panel de administración.
        if usuario.Estado != 'Activo' or not usuario.is_active:
            raise AuthenticationFailed(
                'Tu cuenta está desactivada. Contacta al administrador del sistema.'
            )

        # ── PASO C: Verificar si hay un bloqueo temporal activo ──────────────
        ahora = timezone.now()
        if usuario.bloqueado_hasta and ahora < usuario.bloqueado_hasta:
            # Calcular segundos restantes redondeando hacia arriba para dar
            # el tiempo más generoso al usuario (no decir 0s cuando quedan 0.1s).
            segundos_restantes = ceil(
                (usuario.bloqueado_hasta - ahora).total_seconds()
            )
            raise AuthenticationFailed(
                f'Cuenta bloqueada temporalmente. Intenta de nuevo en '
                f'{segundos_restantes} segundo(s).'
            )

        # ── PASO D: Validar contraseña y gestionar contador de intentos ──────
        # check_password() compara contra el hash almacenado en BD de forma segura.
        if not usuario.check_password(attrs.get('password')):
            usuario.intentos_fallidos += 1

            if usuario.intentos_fallidos >= 3:
                # Bloqueo de 30 segundos exactos desde este momento.
                usuario.bloqueado_hasta = ahora + timedelta(seconds=30)
                usuario.intentos_fallidos = 0  # Reset para el próximo ciclo
                usuario.save(update_fields=['intentos_fallidos', 'bloqueado_hasta'])
                raise AuthenticationFailed(
                    'Has superado el máximo de intentos permitidos. '
                    'Tu cuenta ha sido bloqueada por 30 segundo(s).'
                )

            # Guardar solo los campos modificados (más eficiente que save() completo)
            usuario.save(update_fields=['intentos_fallidos'])
            intentos_restantes = 3 - usuario.intentos_fallidos
            raise AuthenticationFailed(
                f'Contraseña incorrecta. '
                f'Te queda(n) {intentos_restantes} intento(s) antes del bloqueo.'
            )

        # ── PASO E: Autenticación exitosa → resetear contador y emitir tokens ─
        # Solo escribir en BD si había algo que resetear (evita UPDATE innecesario).
        if usuario.intentos_fallidos != 0 or usuario.bloqueado_hasta is not None:
            usuario.intentos_fallidos = 0
            usuario.bloqueado_hasta = None
            usuario.save(update_fields=['intentos_fallidos', 'bloqueado_hasta'])

        # Generar el par de tokens directamente, sin pasar por super().validate()
        # que volvería a invocar authenticate() y duplicaría la verificación.
        # get_token() llama internamente a nuestro get_token() sobreescrito,
        # que añade los claims personalizados (rol, nombre).
        refresh = self.get_token(usuario)
        data = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
        return data


class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Vista de obtención de tokens JWT que usa el serializador personalizado.

    Además de las funciones estándar de simplejwt, sobreescribe `post()`
    para registrar cada intento de login (exitoso o fallido) en el log
    de auditoría. Esto permite al administrador monitorear quién inicia
    sesión, desde qué IP y cuándo, sin instalar dependencias adicionales.

    Endpoints:
        POST /api/token/ → {email, password} → {access, refresh}
    """
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        """
        Intenta autenticar al usuario y registra el resultado en auditoria.

        La auditoría captura tres escenarios distintos:
          1. Login exitoso: reseteo de intentos, tokens emitidos.
          2. Cuenta bloqueada: intento durante el período de bloqueo.
          3. Credenciales incorrectas: email/contraseña inválidos.

        El import local de registrar_evento_manual evita importaciones
        circulares en el arranque de Django (auditoria → usuarios → auditoria).
        """
        # Import local para evitar circular import en bootstrap de Django
        from auditoria.mixins import registrar_evento_manual

        response = super().post(request, *args, **kwargs)

        email = request.data.get('Email') or request.data.get('username', 'desconocido')

        if response.status_code == status.HTTP_200_OK:
            # Login exitoso: el serializer ya validó las credenciales
            # Intentamos obtener el nombre del usuario desde la BD
            nombre_usuario = email  # Fallback al email si no encontramos el nombre
            try:
                from usuarios.models import Usuario
                usuario = Usuario.objects.get(Email=email)
                nombre_usuario = usuario.Nombre
            except Exception:
                pass

            registrar_evento_manual(
                request=request,
                accion='LOGIN',
                modulo='SISTEMA',
                descripcion=f'Inicio de sesión exitoso: {nombre_usuario}',
                resultado='EXITOSO',
            )
        else:
            # Login fallido: puede ser contraseña incorrecta, cuenta bloqueada
            # o cuenta inactiva. El mensaje de error ya va en el response body.
            registrar_evento_manual(
                request=request,
                accion='LOGIN',
                modulo='SISTEMA',
                descripcion=f'Intento de login fallido para: {email}',
                resultado='FALLIDO',
            )

        return response
