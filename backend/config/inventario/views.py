import logging
import base64
import google.genai as genai


from decimal import Decimal

from usuarios.tenant import TenantScopedViewSet
"""
Vistas del Inventario — App 'inventario'
=========================================

Expone los endpoints para gestión completa del inventario: entradas de
mercancía, stock físico, procesamiento de pérdidas, gestión de devoluciones
y reportes especializados.

PATRÓN DE TRANSACCIONES ATÓMICAS (@transaction.atomic):
    Las operaciones que modifican múltiples tablas simultáneamente (ej:
    crear una pérdida Y marcar las unidades de inventario) usan @transaction.atomic.
    Esto garantiza que si cualquier paso falla, todos los cambios anteriores
    se revierten (rollback), dejando la BD en un estado consistente.

    Ejemplo: Si se registran 5 unidades perdidas y la 3ra falla, las 2 primeras
    NO quedarán como 'Perdido' en la BD. La transacción completa se deshace.

REPORTES SQL (sp_):
    Las vistas de reporte usan funciones PL/pgSQL almacenadas en PostgreSQL
    (prefijadas con 'sp_' = stored procedure). Estas funciones implementan
    lógica compleja de agregación y filtrado en la BD, donde es más eficiente
    que traer miles de registros a Python para procesarlos en memoria.
"""

from rest_framework import mixins, viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from django.db import IntegrityError, transaction, connection
from datetime import timedelta
from django.utils import timezone
from .models import (
    EntradaInventario, DetalleEntradaInventario, Inventario,
    Perdida, DetallePerdida, SolicitudDevolucion, DetalleSolicitudDevolucion
)
from ventas.models import DetalleVenta
from usuarios.permissions import IsAdminRole, IsAdminOrReadOnly
from .serializers import (
    EntradaInventarioSerializer, DetalleEntradaInventarioSerializer, InventarioSerializer,
    PerdidaSerializer, DetallePerdidaSerializer, SolicitudDevolucionSerializer,
    DetalleSolicitudDevolucionSerializer, CompraCompletaInputSerializer,
    EditarCompraCompletaInputSerializer, ProcesarPerdidaInputSerializer,
    ProcesarDevolucionInputSerializer, ResolverDevolucionInputSerializer,
    ActualizarObservacionesDevolucionSerializer, validar_total_perdida,
)
from auditoria.mixins import AuditoriaMixin, registrar_evento_manual


from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.conf import settings
from google.genai import types

logger = logging.getLogger(__name__)

class EscanearReciboAIView(APIView):
    """
    Recibe la imagen del recibo/factura desde el frontend, la procesa con 
    Google Gemini 1.5 Flash y devuelve los datos estructurados en formato JSON 
    para auto-llenar el formulario de compras.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # 1. Validar que venga la imagen en la petición
        imagen_file = request.FILES.get('imagen')
        if not imagen_file:
            return Response(
                {"error": "No se ha proporcionado ninguna imagen."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # 2. Leer los bytes de la imagen y prepararlos para la IA
            image_bytes = imagen_file.read()
            mime_type = imagen_file.content_type or 'image/jpeg'

            # 3. Inicializar el cliente de la nueva librería google-genai
            # (Toma la API key directamente desde settings.py)
            client = genai.Client(api_key=settings.GEMINI_API_KEY)

            prompt = """
            Analiza esta imagen de una factura, recibo o nota de compra comercial.
            Extrae la información y devuélvela EXACTAMENTE en formato JSON plano con esta estructura:
            {
              "proveedor_sugerido": "Nombre del proveedor",
              "fecha": "YYYY-MM-DD o null",
              "detalles": [
                {
                  "nombre_producto": "Nombre del producto",
                  "cantidad": 1,
                  "precio_compra_unitario": 0.00
                }
              ]
            }
            """

            # Usamos gemini-3.8-flash asegurando el formato correcto de entrada
            response = client.models.generate_content(
                model='gemini-3.8-flash',
                contents=[
                    prompt,
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                ]
            )

            # 6. Limpiar y parsear la respuesta JSON que entrega la IA
            import json
            texto_respuesta = response.text.strip()
            
            # Limpieza por si el modelo incluye bloques ```json ... ```
            if texto_respuesta.startswith("```json"):
                texto_respuesta = texto_respuesta[7:]
            if texto_respuesta.endswith("```"):
                texto_respuesta = texto_respuesta[:-3]
            texto_respuesta = texto_respuesta.strip()

            datos_extraidos = json.loads(texto_respuesta)

            return Response(datos_extraidos, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Error al procesar la imagen con la IA: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class EdicionCompraInvalida(Exception):
    """Error de negocio controlado que debe revertir una edición completa."""


class OperacionInventarioInvalida(Exception):
    """Error de negocio controlado para operaciones sobre inventario."""

def get_param_flexible(request, key_snake, key_camel):
    """Busca el parámetro en snake_case o camelCase para evitar errores 400/500."""
    return request.query_params.get(key_snake) or request.query_params.get(key_camel)


class EntradaInventarioViewSet(
    AuditoriaMixin,
    TenantScopedViewSet,
    viewsets.ReadOnlyModelViewSet,
):
    """
    Consulta entradas y concentra sus escrituras en acciones transaccionales.

    pagination_class = None: Se desactiva la paginación para que el frontend
    reciba TODAS las entradas en una sola petición. Esto facilita mostrar el
    historial completo de compras sin necesidad de manejar páginas.

    La acción personalizada `delete_completo` es necesaria porque el borrado
    en cascada por defecto de Django no respeta el orden correcto de eliminación
    cuando hay relaciones circulares o restricciones FK complejas.
    """
    queryset = EntradaInventario.objects.all()
    serializer_class = EntradaInventarioSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    pagination_class = None
    MODULO_AUDITORIA = 'INVENTARIO'

    @action(detail=False, methods=['post'], url_path='crear-completa')
    def crear_completa(self, request):
        """Crea cabecera, detalles y unidades de una compra en una transacción."""
        if request.user.Miscelanea_id is None:
            return Response(
                {'detail': 'La cuenta no tiene una miscelánea asignada.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = CompraCompletaInputSerializer(
            data=request.data,
            context=self.get_serializer_context(),
        )
        if not serializer.is_valid():
            return Response(
                {'error': self._primer_error(serializer.errors)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        datos = serializer.validated_data
        try:
            with transaction.atomic():
                entrada = EntradaInventario.objects.create(
                    Miscelanea_id=request.user.Miscelanea_id,
                    IdProveedor=datos['proveedor'],
                    IdUsuario=request.user,
                    FechaEntrada=datos['fechaEntrada'],
                    Total=datos['total_calculado'],
                )
                for detalle in datos['detalles']:
                    DetalleEntradaInventario.objects.create(
                        Miscelanea_id=request.user.Miscelanea_id,
                        IdEntradaInventario=entrada,
                        IdProducto=detalle['producto'],
                        TipoCompra=detalle.get('tipoCompra', 'unidad'),
                        Cantidad=detalle['cantidad'],
                        PrecioCompraUnitario=detalle['precioCompraUnitario'],
                    )
        except Exception:
            logger.exception('Error interno al crear una compra completa')
            return Response(
                {'error': 'No se pudo crear la compra.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        registrar_evento_manual(
            request=request,
            accion='CREAR',
            modulo='INVENTARIO',
            descripcion=f'Compra #{entrada.pk} creada completamente.',
            datos_nuevos={
                'id_entrada': entrada.pk,
                'proveedor_id': entrada.IdProveedor_id,
                'total': str(entrada.Total),
                'numero_detalles': len(datos['detalles']),
                'numero_unidades': sum(
                    detalle['cantidad'] for detalle in datos['detalles']
                ),
            },
        )
        return Response(
            EntradaInventarioSerializer(
                entrada,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['put'], url_path='editar-completa')
    def editar_completa(self, request, pk=None):
        """Edita cabecera, detalles y unidades de una compra atómicamente."""
        if request.user.Miscelanea_id is None:
            return Response(
                {'detail': 'La cuenta no tiene una miscelánea asignada.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = EditarCompraCompletaInputSerializer(
            data=request.data,
            context=self.get_serializer_context(),
        )
        if not serializer.is_valid():
            return Response(
                {'error': self._primer_error(serializer.errors)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        datos = serializer.validated_data
        tenant_id = request.user.Miscelanea_id
        try:
            with transaction.atomic():
                entrada = EntradaInventario.objects.select_for_update().get(
                    pk=pk,
                    Miscelanea_id=tenant_id,
                )
                detalles_actuales = list(
                    DetalleEntradaInventario.objects.select_for_update(
                        of=('self',)
                    )
                    .filter(
                        IdEntradaInventario=entrada,
                        Miscelanea_id=tenant_id,
                    )
                    .order_by('IdDetalleEntrada')
                )
                unidades = list(
                    Inventario.objects.select_for_update(of=('self',))
                    .filter(
                        IdDetalleEntrada__IdEntradaInventario=entrada,
                        Miscelanea_id=tenant_id,
                    )
                    .order_by('IdInventario')
                )
                unidades_por_detalle = {
                    detalle.pk: [
                        unidad
                        for unidad in unidades
                        if unidad.IdDetalleEntrada_id == detalle.pk
                    ]
                    for detalle in detalles_actuales
                }
                actuales_por_id = {
                    detalle.pk: detalle for detalle in detalles_actuales
                }

                for detalle in detalles_actuales:
                    if len(unidades_por_detalle[detalle.pk]) != detalle.Cantidad:
                        raise EdicionCompraInvalida(
                            'La cantidad del detalle no coincide con sus unidades.'
                        )

                ids_recibidos = [
                    detalle['id']
                    for detalle in datos['detalles']
                    if 'id' in detalle
                ]
                if len(ids_recibidos) != len(set(ids_recibidos)):
                    raise EdicionCompraInvalida(
                        'No se puede repetir un detalle en la compra.'
                    )
                if set(ids_recibidos) - set(actuales_por_id):
                    raise EdicionCompraInvalida(
                        'Uno o más detalles no pertenecen a esta compra.'
                    )

                recibidos_por_id = {
                    detalle['id']: detalle
                    for detalle in datos['detalles']
                    if 'id' in detalle
                }
                ids_eliminados = set(actuales_por_id) - set(recibidos_por_id)
                ids_modificados = set()
                for detalle_id, recibido in recibidos_por_id.items():
                    actual = actuales_por_id[detalle_id]
                    if actual.IdProducto_id != recibido['producto'].pk:
                        raise EdicionCompraInvalida(
                            'No se puede cambiar el producto de un detalle existente.'
                        )
                    if (
                        actual.TipoCompra != recibido.get('tipoCompra', 'unidad')
                        or actual.Cantidad != recibido['cantidad']
                        or actual.PrecioCompraUnitario
                        != recibido['precioCompraUnitario']
                    ):
                        ids_modificados.add(detalle_id)

                for detalle_id in sorted(ids_eliminados | ids_modificados):
                    self._validar_detalle_editable(
                        actuales_por_id[detalle_id],
                        unidades_por_detalle[detalle_id],
                        tenant_id,
                    )

                proveedor_cambia = (
                    entrada.IdProveedor_id != datos['proveedor'].pk
                )
                fecha_cambia = (
                    'fechaEntrada' in datos
                    and entrada.FechaEntrada != datos['fechaEntrada']
                )
                if proveedor_cambia or fecha_cambia:
                    for detalle in detalles_actuales:
                        self._validar_detalle_editable(
                            detalle,
                            unidades_por_detalle[detalle.pk],
                            tenant_id,
                        )

                for detalle_id in sorted(ids_eliminados):
                    ids_unidades = [
                        unidad.pk for unidad in unidades_por_detalle[detalle_id]
                    ]
                    Inventario.objects.filter(
                        pk__in=ids_unidades,
                        Miscelanea_id=tenant_id,
                    ).delete()
                    DetalleEntradaInventario.objects.filter(
                        pk=detalle_id,
                        Miscelanea_id=tenant_id,
                    ).delete()

                for detalle_id in sorted(ids_modificados):
                    actual = actuales_por_id[detalle_id]
                    recibido = recibidos_por_id[detalle_id]
                    unidades_detalle = unidades_por_detalle[detalle_id]
                    nueva_cantidad = recibido['cantidad']
                    if nueva_cantidad < actual.Cantidad:
                        ids_a_eliminar = [
                            unidad.pk
                            for unidad in unidades_detalle[nueva_cantidad:]
                        ]
                        Inventario.objects.filter(
                            pk__in=ids_a_eliminar,
                            Miscelanea_id=tenant_id,
                        ).delete()
                    elif nueva_cantidad > actual.Cantidad:
                        Inventario.objects.bulk_create(
                            [
                                Inventario(
                                    Miscelanea_id=tenant_id,
                                    IdDetalleEntrada=actual,
                                    Estado='Disponible',
                                    FechaMovimiento=timezone.now(),
                                )
                                for _ in range(nueva_cantidad - actual.Cantidad)
                            ]
                        )
                    actual.TipoCompra = recibido.get('tipoCompra', 'unidad')
                    actual.Cantidad = nueva_cantidad
                    actual.PrecioCompraUnitario = recibido[
                        'precioCompraUnitario'
                    ]
                    actual.save(
                        update_fields=[
                            'TipoCompra',
                            'Cantidad',
                            'PrecioCompraUnitario',
                        ]
                    )

                for detalle in datos['detalles']:
                    if 'id' not in detalle:
                        DetalleEntradaInventario.objects.create(
                            Miscelanea_id=tenant_id,
                            IdEntradaInventario=entrada,
                            IdProducto=detalle['producto'],
                            TipoCompra=detalle.get('tipoCompra', 'unidad'),
                            Cantidad=detalle['cantidad'],
                            PrecioCompraUnitario=detalle[
                                'precioCompraUnitario'
                            ],
                        )

                entrada.IdProveedor = datos['proveedor']
                if 'fechaEntrada' in datos:
                    entrada.FechaEntrada = datos['fechaEntrada']
                entrada.Total = datos['total_calculado']
                entrada.save(
                    update_fields=['IdProveedor', 'FechaEntrada', 'Total']
                )
        except EntradaInventario.DoesNotExist:
            return Response(
                {'error': 'Compra no encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except EdicionCompraInvalida as exc:
            logger.warning('Edición de compra rechazada: %s', exc)
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception('Error interno al editar una compra completa')
            return Response(
                {'error': 'No se pudo editar la compra.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        registrar_evento_manual(
            request=request,
            accion='MODIFICAR',
            modulo='INVENTARIO',
            descripcion=f'Compra #{entrada.pk} editada completamente.',
            datos_nuevos={
                'id_entrada': entrada.pk,
                'proveedor_id': entrada.IdProveedor_id,
                'total': str(entrada.Total),
                'numero_detalles': len(datos['detalles']),
                'numero_unidades': sum(
                    detalle['cantidad'] for detalle in datos['detalles']
                ),
            },
        )
        return Response(
            EntradaInventarioSerializer(
                entrada,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _validar_detalle_editable(detalle, unidades, tenant_id):
        if any(unidad.Estado != 'Disponible' for unidad in unidades):
            raise EdicionCompraInvalida(
                'No se puede modificar una unidad que no está disponible.'
            )
        ids_unidades = [unidad.pk for unidad in unidades]
        tiene_historial = (
            DetalleVenta.objects.filter(
                IdInventario_id__in=ids_unidades,
                Miscelanea_id=tenant_id,
            ).exists()
            or DetallePerdida.objects.filter(
                IdInventario_id__in=ids_unidades,
                Miscelanea_id=tenant_id,
            ).exists()
            or DetalleSolicitudDevolucion.objects.filter(
                IdInventario_id__in=ids_unidades,
                Miscelanea_id=tenant_id,
            ).exists()
        )
        if tiene_historial:
            raise EdicionCompraInvalida(
                'No se puede modificar un detalle con historial.'
            )

    @staticmethod
    def _primer_error(errores):
        if isinstance(errores, dict):
            return EntradaInventarioViewSet._primer_error(
                next(iter(errores.values()))
            )
        if isinstance(errores, (list, tuple)):
            return EntradaInventarioViewSet._primer_error(errores[0])
        return str(errores)

    @action(detail=True, methods=["delete"])
    def delete_completo(self, request, pk=None):
        """Elimina una compra únicamente si sus unidades no tienen historial."""
        if request.user.Miscelanea_id is None:
            return Response(
                {'detail': 'La cuenta no tiene una miscelánea asignada.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        tenant_id = request.user.Miscelanea_id
        try:
            with transaction.atomic():
                entrada = EntradaInventario.objects.select_for_update().get(
                    pk=pk,
                    Miscelanea_id=tenant_id,
                )
                detalles = list(
                    DetalleEntradaInventario.objects.select_for_update(
                        of=('self',)
                    )
                    .filter(
                        IdEntradaInventario=entrada,
                        Miscelanea_id=tenant_id,
                    )
                    .order_by('IdDetalleEntrada')
                )
                unidades = list(
                    Inventario.objects.select_for_update(of=('self',))
                    .filter(
                        IdDetalleEntrada__IdEntradaInventario=entrada,
                        Miscelanea_id=tenant_id,
                    )
                    .order_by('IdInventario')
                )
                unidades_por_detalle = {
                    detalle.pk: [
                        unidad
                        for unidad in unidades
                        if unidad.IdDetalleEntrada_id == detalle.pk
                    ]
                    for detalle in detalles
                }

                for detalle in detalles:
                    unidades_detalle = unidades_por_detalle[detalle.pk]
                    if len(unidades_detalle) != detalle.Cantidad:
                        raise EdicionCompraInvalida(
                            'La cantidad del detalle no coincide con sus unidades.'
                        )
                    self._validar_detalle_editable(
                        detalle,
                        unidades_detalle,
                        tenant_id,
                    )

                datos_anteriores = {
                    'id_entrada': entrada.pk,
                    'proveedor_id': entrada.IdProveedor_id,
                    'total': str(entrada.Total),
                    'numero_detalles': len(detalles),
                    'numero_unidades': len(unidades),
                }
                Inventario.objects.filter(
                    pk__in=[unidad.pk for unidad in unidades],
                    Miscelanea_id=tenant_id,
                ).delete()
                DetalleEntradaInventario.objects.filter(
                    pk__in=[detalle.pk for detalle in detalles],
                    Miscelanea_id=tenant_id,
                ).delete()
                entrada.delete()
        except EntradaInventario.DoesNotExist:
            return Response(
                {'error': 'Compra no encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except EdicionCompraInvalida as exc:
            logger.warning('Eliminación de compra rechazada: %s', exc)
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except IntegrityError:
            logger.warning(
                'Eliminación de compra rechazada por restricción de integridad.'
            )
            return Response(
                {'error': 'No se puede eliminar la compra porque tiene historial.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception('Error interno al eliminar una compra completa')
            return Response(
                {'error': 'No se pudo eliminar la compra.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        registrar_evento_manual(
            request=request,
            accion='ELIMINAR',
            modulo='INVENTARIO',
            descripcion=f'Compra #{pk} eliminada completamente.',
            datos_anteriores=datos_anteriores,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class DetalleEntradaInventarioViewSet(
    TenantScopedViewSet,
    viewsets.ReadOnlyModelViewSet,
):
    """
    Consulta de los detalles (líneas) de una entrada de inventario.

    Incluye filtrado por entrada: GET /api/inventario/detalles/?entradaInventarioId=5
    Las escrituras se realizan exclusivamente mediante las acciones completas
    de EntradaInventarioViewSet.
    """
    queryset = DetalleEntradaInventario.objects.all()
    serializer_class = DetalleEntradaInventarioSerializer
    permission_classes = [IsAuthenticated, IsAdminOrReadOnly]
    pagination_class = None

    def get_queryset(self):
        """
        Filtra los detalles por entrada de inventario si se proporciona el parámetro.

        POR QUÉ FILTRAR EN get_queryset():
            Este es el patrón correcto de DRF para filtros dinámicos. Si se filtrara
            en el método `list()`, se rompería la compatibilidad con otras acciones
            del ViewSet (retrieve, update, delete) que también usan get_queryset().

        Returns:
            QuerySet: Todos los detalles si no hay filtro, o los del ID especificado.
        """
        entrada_id = self.request.query_params.get("entradaInventarioId")
        queryset = super().get_queryset()
        if entrada_id:
            queryset = queryset.filter(IdEntradaInventario_id=entrada_id)
        return queryset




class InventarioViewSet(TenantScopedViewSet, viewsets.ReadOnlyModelViewSet):
    """
    Consulta de registros individuales de inventario.

    Los cambios de estado ocurren exclusivamente durante las operaciones
    transaccionales de ventas, pérdidas y devoluciones.
    """
    queryset = Inventario.objects.all()
    serializer_class = InventarioSerializer
    permission_classes = [IsAuthenticated, IsAdminOrReadOnly]
    pagination_class = None


class PerdidaViewSet(TenantScopedViewSet, viewsets.ReadOnlyModelViewSet):
    """ViewSet de solo lectura (para admins) del historial de pérdidas."""
    queryset = Perdida.objects.all()
    serializer_class = PerdidaSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    pagination_class = None


class DetallePerdidaViewSet(TenantScopedViewSet, viewsets.ReadOnlyModelViewSet):
    """ViewSet para consultar líneas de detalle de pérdidas."""
    queryset = DetallePerdida.objects.all()
    serializer_class = DetallePerdidaSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    pagination_class = None


class SolicitudDevolucionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    TenantScopedViewSet,
    viewsets.GenericViewSet,
):
    """
    Consulta y actualización parcial de solicitudes de devolución.

    LÓGICA DE NEGOCIO CRÍTICA:
        Una solicitud de devolución que ya fue aprobada o rechazada NO puede
        ser modificada. Esto simula un proceso de aprobación formal donde
        una vez tomada la decisión, el registro es inmutable.

        PATCH permite editar exclusivamente las observaciones mientras la
        solicitud sigue pendiente. La acción POST `resolver` concentra la
        resolución atómica. POST genérico, PUT y DELETE no forman parte de
        esta ruta.
    """
    queryset = SolicitudDevolucion.objects.all()
    serializer_class = SolicitudDevolucionSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    pagination_class = None

    def partial_update(self, request, *args, **kwargs):
        """Actualiza solo observaciones, serializada con la resolución."""
        input_serializer = ActualizarObservacionesDevolucionSerializer(
            data=request.data
        )
        input_serializer.is_valid(raise_exception=True)

        tenant_id = request.user.Miscelanea_id
        if tenant_id is None:
            return Response(
                {'error': 'El usuario no tiene una miscelánea asignada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                solicitud = (
                    SolicitudDevolucion.objects.select_for_update(of=('self',))
                    .get(pk=kwargs.get('pk'), Miscelanea_id=tenant_id)
                )
                if solicitud.Estado != 'Pendiente':
                    raise OperacionInventarioInvalida(
                        'No se puede modificar una solicitud que ya no está pendiente.'
                    )
                solicitud.Observaciones = input_serializer.validated_data[
                    'observaciones'
                ]
                solicitud.save(update_fields=['Observaciones'])

            return Response(
                SolicitudDevolucionSerializer(
                    solicitud,
                    context=self.get_serializer_context(),
                ).data
            )
        except SolicitudDevolucion.DoesNotExist:
            return Response(
                {'detail': 'No encontrado.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except OperacionInventarioInvalida as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=['post'])
    def resolver(self, request, pk=None):
        """Resolve a pending supplier return exactly once and atomically."""
        tenant_id = request.user.Miscelanea_id
        if tenant_id is None:
            return Response(
                {'error': 'El usuario no tiene una miscelánea asignada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        input_serializer = ResolverDevolucionInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        estado_final = input_serializer.validated_data['estado']

        try:
            with transaction.atomic():
                solicitud = (
                    SolicitudDevolucion.objects.select_for_update(of=('self',))
                    .select_related('IdEntradaInventario', 'IdUsuario')
                    .get(pk=pk, Miscelanea_id=tenant_id)
                )
                if solicitud.Estado != 'Pendiente':
                    raise OperacionInventarioInvalida(
                        'La solicitud ya fue resuelta.'
                    )
                if (
                    solicitud.IdEntradaInventario.Miscelanea_id != tenant_id
                    or solicitud.IdUsuario.Miscelanea_id != tenant_id
                ):
                    raise OperacionInventarioInvalida(
                        'La solicitud tiene relaciones de miscelánea inconsistentes.'
                    )

                detalles = list(
                    DetalleSolicitudDevolucion.objects.select_for_update(
                        of=('self',)
                    )
                    .filter(IdSolicitudDevolucion=solicitud)
                    .order_by('IdDetalleSolicitudDevolucion')
                )
                if not detalles:
                    raise OperacionInventarioInvalida(
                        'La solicitud no contiene detalles.'
                    )
                ids_inventario = [
                    detalle.IdInventario_id for detalle in detalles
                ]
                if len(ids_inventario) != len(set(ids_inventario)):
                    raise OperacionInventarioInvalida(
                        'La solicitud contiene unidades repetidas.'
                    )
                if any(
                    detalle.Miscelanea_id != tenant_id
                    or detalle.EstadoItem != 'Pendiente'
                    for detalle in detalles
                ):
                    raise OperacionInventarioInvalida(
                        'Todos los detalles deben estar pendientes y pertenecer a la miscelánea.'
                    )

                inventarios = list(
                    Inventario.objects.select_for_update(of=('self',))
                    .select_related(
                        'IdDetalleEntrada__IdEntradaInventario',
                        'IdDetalleEntrada__IdProducto',
                    )
                    .filter(
                        IdInventario__in=ids_inventario,
                        Miscelanea_id=tenant_id,
                    )
                    .order_by('IdInventario')
                )
                if len(inventarios) != len(ids_inventario):
                    raise OperacionInventarioInvalida(
                        'Todas las unidades deben existir y pertenecer a la miscelánea.'
                    )
                inventarios_por_id = {
                    inventario.IdInventario: inventario
                    for inventario in inventarios
                }
                for detalle in detalles:
                    inventario = inventarios_por_id[detalle.IdInventario_id]
                    detalle_compra = inventario.IdDetalleEntrada
                    if (
                        inventario.Estado != 'Devuelto'
                        or detalle_compra.Miscelanea_id != tenant_id
                        or detalle_compra.IdEntradaInventario_id
                        != solicitud.IdEntradaInventario_id
                        or detalle_compra.IdEntradaInventario.Miscelanea_id
                        != tenant_id
                        or detalle_compra.IdProducto.Miscelanea_id != tenant_id
                    ):
                        raise OperacionInventarioInvalida(
                            'La unidad devuelta tiene relaciones inconsistentes.'
                        )
                    if (
                        detalle.PrecioCompraUnitario
                        != detalle_compra.PrecioCompraUnitario
                    ):
                        raise OperacionInventarioInvalida(
                            'El costo almacenado no coincide con el detalle de compra.'
                        )

                perdida = None
                fecha_resolucion = timezone.now()
                if estado_final == 'Rechazada':
                    total = sum(
                        (
                            detalle.PrecioCompraUnitario
                            for detalle in detalles
                        ),
                        Decimal('0.00'),
                    )
                    try:
                        total = validar_total_perdida(total)
                    except ValidationError as exc:
                        raise OperacionInventarioInvalida(
                            'El total de la pérdida excede la capacidad permitida.'
                        ) from exc
                    perdida = Perdida.objects.create(
                        Miscelanea_id=tenant_id,
                        IdUsuario=request.user,
                        TipoPerdida='RechazoDevolucion',
                        Fecha=fecha_resolucion,
                        Total=total,
                    )
                    for detalle in detalles:
                        inventario = inventarios_por_id[detalle.IdInventario_id]
                        DetallePerdida.objects.create(
                            Miscelanea_id=tenant_id,
                            IdPerdida=perdida,
                            IdInventario=inventario,
                            PrecioCompraUnitario=detalle.PrecioCompraUnitario,
                        )
                        inventario.Estado = 'Perdido'
                        inventario.FechaMovimiento = fecha_resolucion
                        inventario.save(
                            update_fields=['Estado', 'FechaMovimiento']
                        )
                        detalle.EstadoItem = 'Rechazado'
                        detalle.save(update_fields=['EstadoItem'])
                else:
                    for detalle in detalles:
                        detalle.EstadoItem = 'Aceptado'
                        detalle.save(update_fields=['EstadoItem'])

                solicitud.Estado = estado_final
                solicitud.save(update_fields=['Estado'])
                datos_nuevos = {
                    'id_solicitud': solicitud.pk,
                    'estado': estado_final,
                }
                if perdida is not None:
                    datos_nuevos['id_perdida'] = perdida.pk
                registrar_evento_manual(
                    request=request,
                    accion='RESOLVER',
                    modulo='DEVOLUCIONES',
                    descripcion=(
                        f'Solicitud de devolución #{solicitud.pk} resuelta '
                        f'como {estado_final}.'
                    ),
                    datos_anteriores={'estado': 'Pendiente'},
                    datos_nuevos=datos_nuevos,
                )

            return Response(
                {'message': f'Solicitud resuelta como {estado_final}.'},
                status=status.HTTP_200_OK,
            )
        except SolicitudDevolucion.DoesNotExist:
            return Response(
                {'error': 'La solicitud de devolución no existe.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except OperacionInventarioInvalida as exc:
            logger.warning('Resolución de devolución rechazada: %s', exc)
            registrar_evento_manual(
                request=request,
                accion='RESOLVER',
                modulo='DEVOLUCIONES',
                descripcion=(
                    'Resolución rechazada por estado o relaciones inconsistentes.'
                ),
                resultado='FALLIDO',
            )
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception('Error interno al resolver una devolución')
            registrar_evento_manual(
                request=request,
                accion='RESOLVER',
                modulo='DEVOLUCIONES',
                descripcion='Error interno al resolver una devolución.',
                resultado='FALLIDO',
            )
            return Response(
                {'error': 'No se pudo resolver la devolución.'},
                status=status.HTTP_400_BAD_REQUEST,
            )


class DetalleSolicitudDevolucionViewSet(
    TenantScopedViewSet,
    viewsets.ReadOnlyModelViewSet,
):
    """ViewSet para líneas de detalle de solicitudes de devolución."""
    queryset = DetalleSolicitudDevolucion.objects.all()
    serializer_class = DetalleSolicitudDevolucionSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    pagination_class = None


# ─── VISTAS DE PROCESAMIENTO ───────────────────────────────────────────────────

class ProcesarPerdidaView(APIView):
    """
    Registra una pérdida de mercancía de forma atómica.

    Crea el encabezado (Perdida), los detalles (DetallePerdida) y actualiza
    el estado de cada unidad de inventario involucrada en una sola transacción.

    Endpoint: POST /api/inventario/procesar-perdida/

    Payload aceptado:
        {
          "tipoPerdida": "Otra",
          "detalles": [
            {"inventarioId": 42},
            {"inventarioId": 43}
          ]
        }

    La identidad, fecha, costos y total son autoridad del servidor. Los campos
    heredados que el cliente todavía envíe para esos valores se ignoran.
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        """
        Procesa el registro completo de una pérdida.

        select_for_update() en el Inventario bloquea las filas seleccionadas
        durante la transacción para evitar que dos procesos simultáneos
        modifiquen el mismo registro de inventario.

        Args:
            request: Petición HTTP con los datos de la pérdida.

        Returns:
            Response: 201 Created si exitoso, 400 Bad Request si error.
        """
        serializer = ProcesarPerdidaInputSerializer(data=request.data)
        if not serializer.is_valid():
            registrar_evento_manual(
                request=request,
                accion='PROCESAR',
                modulo='PERDIDAS',
                descripcion='Pérdida rechazada por datos inválidos.',
                resultado='FALLIDO',
            )
            return Response(
                {'error': EntradaInventarioViewSet._primer_error(serializer.errors)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        datos = serializer.validated_data
        tipo_perdida = datos['tipoPerdida']
        ids_inventario = sorted(
            detalle['inventarioId'] for detalle in datos['detalles']
        )
        tenant_id = request.user.Miscelanea_id

        try:
            with transaction.atomic():
                inventarios = list(
                    Inventario.objects.select_for_update(of=('self',))
                    .select_related(
                        'IdDetalleEntrada__IdEntradaInventario',
                        'IdDetalleEntrada__IdProducto',
                    )
                    .filter(
                        IdInventario__in=ids_inventario,
                        Miscelanea_id=tenant_id,
                    )
                    .order_by('IdInventario')
                )
                if len(inventarios) != len(ids_inventario):
                    raise OperacionInventarioInvalida(
                        'Todas las unidades deben existir y pertenecer a la miscelánea.'
                    )
                if any(inventario.Estado != 'Disponible' for inventario in inventarios):
                    raise OperacionInventarioInvalida(
                        'Todas las unidades deben estar disponibles.'
                    )
                for inventario in inventarios:
                    detalle_entrada = inventario.IdDetalleEntrada
                    if (
                        detalle_entrada.Miscelanea_id != tenant_id
                        or detalle_entrada.IdEntradaInventario.Miscelanea_id != tenant_id
                        or detalle_entrada.IdProducto.Miscelanea_id != tenant_id
                    ):
                        raise OperacionInventarioInvalida(
                            'La unidad tiene una relación de compra inconsistente.'
                        )

                total = sum(
                    (
                        inventario.IdDetalleEntrada.PrecioCompraUnitario
                        for inventario in inventarios
                    ),
                    Decimal('0.00'),
                )
                try:
                    total = validar_total_perdida(total)
                except ValidationError as exc:
                    raise OperacionInventarioInvalida(
                        'El total de la pérdida excede la capacidad permitida.'
                    ) from exc

                fecha_procesamiento = timezone.now()
                perdida = Perdida.objects.create(
                    IdUsuario=request.user,
                    Miscelanea_id=tenant_id,
                    TipoPerdida=tipo_perdida,
                    Fecha=fecha_procesamiento,
                    Total=total,
                )
                for inventario in inventarios:
                    costo = inventario.IdDetalleEntrada.PrecioCompraUnitario
                    DetallePerdida.objects.create(
                        IdPerdida=perdida,
                        IdInventario=inventario,
                        Miscelanea_id=tenant_id,
                        PrecioCompraUnitario=costo,
                    )
                    inventario.Estado = 'Perdido'
                    inventario.FechaMovimiento = fecha_procesamiento
                    inventario.save(update_fields=['Estado', 'FechaMovimiento'])

                registrar_evento_manual(
                    request=request,
                    accion='PROCESAR',
                    modulo='PERDIDAS',
                    descripcion=f'Pérdida #{perdida.pk} procesada.',
                    datos_nuevos={
                        'id_perdida': perdida.pk,
                        'tipo': tipo_perdida,
                        'total': str(total),
                        'unidades': ids_inventario,
                    },
                )
                return Response({"message": "Pérdida procesada"}, status=status.HTTP_201_CREATED)
        except OperacionInventarioInvalida as exc:
            logger.warning('Pérdida rechazada: %s', exc)
            registrar_evento_manual(
                request=request,
                accion='PROCESAR',
                modulo='PERDIDAS',
                descripcion='Pérdida rechazada por datos o inventario inválidos.',
                resultado='FALLIDO',
            )
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception('Error interno al procesar una pérdida')
            registrar_evento_manual(
                request=request,
                accion='PROCESAR',
                modulo='PERDIDAS',
                descripcion='Error interno al procesar una pérdida.',
                resultado='FALLIDO',
            )
            return Response(
                {'error': 'No se pudo procesar la pérdida.'},
                status=status.HTTP_400_BAD_REQUEST,
            )


class ProcesarDevolucionView(APIView):
    """
    Registra una solicitud de devolución de mercancía al proveedor.

    Al crear la solicitud, las unidades involucradas se marcan como 'Devuelto'
    para que dejen de aparecer como disponibles en el stock.

    Endpoint: POST /api/inventario/procesar-devolucion/
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        """
        Procesa la creación de una solicitud de devolución.

        Args:
            request: Petición HTTP con los datos de la solicitud.

        Returns:
            Response: 201 Created si exitoso, 400 Bad Request si error.
        """
        tenant_id = request.user.Miscelanea_id
        if tenant_id is None:
            return Response(
                {"error": "El usuario autenticado no tiene una miscelánea asignada."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ProcesarDevolucionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data
        detalles_entrada = datos['detalles']
        ids_inventario = [detalle['inventarioId'] for detalle in detalles_entrada]
        motivos_por_inventario = {
            detalle['inventarioId']: detalle.get('MotivoRechazo')
            for detalle in detalles_entrada
        }
        try:
            with transaction.atomic():
                entrada = EntradaInventario.objects.select_for_update(
                    of=('self',)
                ).get(
                    IdEntradaInventario=datos['IdEntradaInventario'],
                    Miscelanea_id=tenant_id,
                )

                referencias = list(
                    Inventario.objects.filter(
                        IdInventario__in=ids_inventario,
                        Miscelanea_id=tenant_id,
                    )
                    .order_by('IdInventario')
                    .values_list('IdInventario', 'IdDetalleEntrada_id')
                )
                if len(referencias) != len(ids_inventario):
                    raise OperacionInventarioInvalida(
                        'Todas las unidades deben existir y pertenecer a la miscelánea.'
                    )

                ids_detalle_entrada = sorted(
                    {detalle_id for _, detalle_id in referencias}
                )
                detalles_compra = list(
                    DetalleEntradaInventario.objects.select_for_update(
                        of=('self',)
                    )
                    .filter(
                        IdDetalleEntrada__in=ids_detalle_entrada,
                        IdEntradaInventario=entrada,
                        Miscelanea_id=tenant_id,
                    )
                    .order_by('IdDetalleEntrada')
                )
                if {
                    detalle.IdDetalleEntrada for detalle in detalles_compra
                } != set(ids_detalle_entrada):
                    raise OperacionInventarioInvalida(
                        'Todas las unidades deben pertenecer a la entrada indicada.'
                    )
                detalles_compra_por_id = {
                    detalle.IdDetalleEntrada: detalle
                    for detalle in detalles_compra
                }

                inventarios = list(
                    Inventario.objects.select_for_update(of=('self',))
                    .select_related('IdDetalleEntrada')
                    .filter(
                        IdInventario__in=ids_inventario,
                        Miscelanea_id=tenant_id,
                    )
                    .order_by('IdInventario')
                )
                if len(inventarios) != len(ids_inventario):
                    raise OperacionInventarioInvalida(
                        'Todas las unidades deben existir y pertenecer a la miscelánea.'
                    )

                for inventario in inventarios:
                    detalle_compra = detalles_compra_por_id.get(
                        inventario.IdDetalleEntrada_id
                    )
                    if (
                        detalle_compra is None
                        or detalle_compra.Miscelanea_id != tenant_id
                        or detalle_compra.IdEntradaInventario_id != entrada.pk
                        or inventario.Estado != 'Disponible'
                    ):
                        raise OperacionInventarioInvalida(
                            'Todas las unidades deben estar disponibles y pertenecer a la entrada.'
                        )

                fecha_procesamiento = timezone.now()
                solicitud = SolicitudDevolucion.objects.create(
                    IdEntradaInventario=entrada,
                    IdUsuario=request.user,
                    Miscelanea_id=tenant_id,
                    Estado='Pendiente',
                    Observaciones=datos.get('Observaciones', ''),
                    Fecha=fecha_procesamiento,
                )

                for inventario in inventarios:
                    detalle_compra = detalles_compra_por_id[
                        inventario.IdDetalleEntrada_id
                    ]
                    DetalleSolicitudDevolucion.objects.create(
                        IdSolicitudDevolucion=solicitud,
                        IdInventario=inventario,
                        Miscelanea_id=tenant_id,
                        MotivoRechazo=motivos_por_inventario[
                            inventario.IdInventario
                        ],
                        PrecioCompraUnitario=(
                            detalle_compra.PrecioCompraUnitario
                        ),
                        EstadoItem='Pendiente',
                    )
                    inventario.Estado = 'Devuelto'
                    inventario.FechaMovimiento = fecha_procesamiento
                    inventario.save(update_fields=['Estado', 'FechaMovimiento'])

                registrar_evento_manual(
                    request=request,
                    accion='PROCESAR',
                    modulo='DEVOLUCIONES',
                    descripcion=(
                        f'Solicitud de devolución #{solicitud.pk} procesada.'
                    ),
                    datos_nuevos={
                        'id_solicitud': solicitud.pk,
                        'estado': 'Pendiente',
                        'unidades': ids_inventario,
                    },
                )

            return Response(
                {"message": "Solicitud de devolución procesada."},
                status=status.HTTP_201_CREATED,
            )
        except EntradaInventario.DoesNotExist:
            registrar_evento_manual(
                request=request,
                accion='PROCESAR',
                modulo='DEVOLUCIONES',
                descripcion='Devolución rechazada: entrada inexistente o ajena.',
                resultado='FALLIDO',
            )
            return Response(
                {"error": "La entrada de inventario no existe."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except OperacionInventarioInvalida as exc:
            logger.warning('Devolución rechazada: %s', exc)
            registrar_evento_manual(
                request=request,
                accion='PROCESAR',
                modulo='DEVOLUCIONES',
                descripcion='Devolución rechazada por datos o inventario inválidos.',
                resultado='FALLIDO',
            )
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception('Error interno al procesar una devolución')
            registrar_evento_manual(
                request=request,
                accion='PROCESAR',
                modulo='DEVOLUCIONES',
                descripcion='Error interno al procesar una devolución.',
                resultado='FALLIDO',
            )
            return Response(
                {"error": "No se pudo procesar la devolución."},
                status=status.HTTP_400_BAD_REQUEST,
            )


class DevolverStockView(APIView):
    """Ruta heredada cerrada; la resolución se realiza mediante `resolver`."""
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        return Response(
            {
                'error': (
                    'Esta ruta está cerrada. Use la acción resolver de la '
                    'solicitud de devolución.'
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )


# ─── REPORTES DE INVENTARIO (funciones sp_) ────────────────────────────────────

class ReporteComprasFiltradasView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        inicio = request.query_params.get('inicio')
        fin = request.query_params.get('fin')
        # FLEXIBILIDAD: Aceptamos proveedor_id o proveedorId
        proveedor_id = get_param_flexible(request, 'proveedor_id', 'proveedorId')

        if not inicio or not fin:
            return Response({"error": "Faltan fechas de inicio y fin"}, status=400)

        # Limpieza de fechas (por si llega con T)
        inicio = inicio.split('T')[0] if 'T' in inicio else inicio
        fin = fin.split('T')[0] if 'T' in fin else fin

        try:
            with connection.cursor() as cursor:
                # Normalización: convierte a int o None
                p_id = int(proveedor_id) if proveedor_id and str(proveedor_id).strip() not in ["", "null", "undefined"] else None
                
                cursor.execute("""
                    SELECT ei."IdEntradaInventario" AS id_entrada,
                           ei."FechaEntrada"::DATE AS fecha_ingreso,
                           pr."Nombre"::VARCHAR AS nombre_proveedor,
                           ei."Total"::NUMERIC AS total_compra
                    FROM "EntradaInventario" ei
                    JOIN "Proveedor" pr ON ei."IdProveedor" = pr."IdProveedor"
                    WHERE ei."IdMiscelanea" = %s
                      AND pr."IdMiscelanea" = %s
                      AND ei."FechaEntrada"::DATE BETWEEN %s::DATE AND %s::DATE
                      AND (%s::INT IS NULL OR ei."IdProveedor" = %s::INT)
                """, [
                    request.user.Miscelanea_id,
                    request.user.Miscelanea_id,
                    inicio,
                    fin,
                    p_id,
                    p_id,
                ])
                columns = [col[0].lower() for col in cursor.description]
                result = [dict(zip(columns, row)) for row in cursor.fetchall()]
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=400)

class ReporteProductosSinMovimientoView(APIView):
    """
    Reporte de productos que no han tenido ventas en los últimos N días.

    Útil para identificar mercancía estancada que podría vencerse o
    necesitar estrategias de promoción para liquidar el stock.

    Query params:
        dias (int, opcional): Número de días sin movimiento. Por defecto: 30.

    Lógica de conversión:
        El frontend solo envía "días" (ej: 30). Esta vista convierte ese número
        en un rango de fechas real (fecha_inicio = hoy - 30 días, fecha_fin = hoy)
        porque la función SQL sp_productos_sin_movimiento() opera sobre fechas,
        no sobre un contador de días.
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        """
        Ejecuta el reporte de productos sin movimiento.

        Args:
            request: Petición con query param 'dias'.

        Returns:
            Response: Lista de productos estancados con su stock y días sin venta.
        """
        dias = request.query_params.get('dias', 30)
        try:
            # Convertir el parámetro a entero con validación implícita
            dias_int = int(dias)
            # Calcular el rango de fechas dinámicamente desde hoy hacia atrás
            fecha_fin = timezone.now().date()
            fecha_inicio = fecha_fin - timedelta(days=dias_int)

            with connection.cursor() as cursor:
                cursor.execute("""
                    WITH ult AS (
                        SELECT dei."IdProducto", MAX(v."Fecha")::DATE AS ultima
                        FROM "Venta" v
                        JOIN "DetalleVenta" dv ON v."IdVenta" = dv."IdVenta"
                        JOIN "Inventario" i ON dv."IdInventario" = i."IdInventario"
                        JOIN "DetalleEntradaInventario" dei ON i."IdDetalleEntrada" = dei."IdDetalleEntrada"
                        WHERE v."IdMiscelanea" = %(t)s
                          AND dv."IdMiscelanea" = %(t)s
                          AND i."IdMiscelanea" = %(t)s
                          AND dei."IdMiscelanea" = %(t)s
                        GROUP BY dei."IdProducto"
                    )
                    SELECT p."Nombre"::VARCHAR AS nombre_producto,
                           c."Nombre"::VARCHAR AS categoria,
                           (SELECT COUNT(*) FROM "Inventario" inv
                              JOIN "DetalleEntradaInventario" d2 ON inv."IdDetalleEntrada" = d2."IdDetalleEntrada"
                             WHERE d2."IdProducto" = p."IdProducto" AND inv."Estado" = 'Disponible'
                               AND inv."IdMiscelanea" = %(t)s
                               AND d2."IdMiscelanea" = %(t)s)::BIGINT AS stock_actual,
                           ult.ultima AS ultima_venta,
                           COALESCE(CURRENT_DATE - ult.ultima, 0)::INTEGER AS dias_sin_venta
                    FROM "Producto" p
                    JOIN "Categoria" c ON p."IdCategoria" = c."IdCategoria"
                    LEFT JOIN ult ON ult."IdProducto" = p."IdProducto"
                    WHERE p."IdMiscelanea" = %(t)s
                      AND c."IdMiscelanea" = %(t)s
                      AND (ult.ultima IS NULL OR ult.ultima < %(ini)s::DATE)
                """, {'t': request.user.Miscelanea_id, 'ini': fecha_inicio})
                columns = [col[0] for col in cursor.description]
                result = [{k.lower(): v for k, v in zip(columns, row)} for row in cursor.fetchall()]
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=400)