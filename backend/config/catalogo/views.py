"""
Vistas del Catálogo — App 'catalogo'
======================================

Expone los endpoints CRUD para las entidades maestras del negocio:
Categoría, Proveedor y Producto.

PERMISOS — IsAdminOrReadOnly (heredado de DRF DEFAULT_PERMISSION_CLASSES):
    Los ViewSets del catálogo no declaran permission_classes explícitamente,
    por lo que heredan la clase global definida en settings.py.
    Esto significa que:
    - Vendedores: Pueden consultar el catálogo (GET) para saber qué productos
      existen y a qué precio venderlos.
    - Admins: Pueden crear, editar y eliminar categorías, proveedores y productos.

    Para una seguridad más granular en el futuro, añadir:
        permission_classes = [IsAuthenticated, IsAdminOrReadOnly]

PAGINACIÓN — Desactivada (pagination_class = None):
    El frontend carga todo el catálogo en memoria al inicio de cada sesión
    de venta (fetchInventoryForSale en Ventas.tsx). La paginación haría
    necesario manejar múltiples peticiones para tener el catálogo completo,
    lo que aumentaría la complejidad del cliente. Para catálogos pequeños
    (<500 productos), cargar todo de una vez es más simple y eficiente.

ENDPOINTS GENERADOS AUTOMÁTICAMENTE POR ModelViewSet:
    GET    /api/catalogo/categorias/          → listar categorías
    POST   /api/catalogo/categorias/          → crear categoría
    GET    /api/catalogo/categorias/<id>/     → detalle de categoría
    PUT    /api/catalogo/categorias/<id>/     → actualizar categoría
    PATCH  /api/catalogo/categorias/<id>/     → actualización parcial
    DELETE /api/catalogo/categorias/<id>/     → eliminar (RESTRICT si tiene productos)
    (ídem para proveedores/ y productos/)

AUDITORÍA:
    AuditoriaMixin intercepta automáticamente las mutaciones (POST, PUT, PATCH, DELETE)
    y registra el log en la tabla LogAuditoria. Los GETs no se registran.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from usuarios.permissions import IsAdminOrReadOnly
from auditoria.mixins import AuditoriaMixin
from usuarios.tenant import TenantScopedViewSet
from .models import Categoria, Proveedor, Producto
from .serializers import CategoriaSerializer, ProveedorSerializer, ProductoSerializer
from .codigos import (
    limpiar_y_validar_codigo_producto,
    variantes_equivalentes_codigo_producto,
)


class CatalogoWriteSafetyMixin:
    """Aplica las guardias de escritura comunes al catálogo."""

    def create(self, request, *args, **kwargs):
        if request.user.Miscelanea_id is None:
            return Response(
                {'detail': 'La cuenta no tiene una miscelánea asignada.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().create(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            with transaction.atomic():
                return super().destroy(request, *args, **kwargs)
        except RestrictedError:
            return Response(
                {
                    'detail': (
                        'No se puede eliminar porque el registro tiene '
                        'datos relacionados.'
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )


class CategoriaViewSet(
    CatalogoWriteSafetyMixin,
    AuditoriaMixin,
    TenantScopedViewSet,
    viewsets.ModelViewSet,
):
    """
    CRUD completo para el modelo Categoria.

    Las categorías definen el PorcentajeGanancia que se usa para calcular
    el precio de venta de todos los productos que pertenecen a ella.
    Cambiar el porcentaje afecta dinámicamente el precio de venta de todos
    sus productos en el siguiente request.
    """
    queryset = Categoria.objects.all()
    serializer_class = CategoriaSerializer
    permission_classes = [IsAuthenticated, IsAdminOrReadOnly]
    pagination_class = None
    # Identificador de módulo para los logs de auditoría
    MODULO_AUDITORIA = 'CATEGORIAS'


class ProveedorViewSet(
    CatalogoWriteSafetyMixin,
    AuditoriaMixin,
    TenantScopedViewSet,
    viewsets.ModelViewSet,
):
    """
    CRUD completo para el modelo Proveedor.

    Los proveedores se vinculan a las EntradaInventario al registrar compras.
    Un proveedor con historial de compras no puede ser eliminado (RESTRICT en BD),
    solo desactivado cambiando su Estado a 'Inactivo'.
    """
    queryset = Proveedor.objects.all()
    serializer_class = ProveedorSerializer
    permission_classes = [IsAuthenticated, IsAdminOrReadOnly]
    pagination_class = None
    MODULO_AUDITORIA = 'PROVEEDORES'


class ProductoViewSet(
    CatalogoWriteSafetyMixin,
    AuditoriaMixin,
    TenantScopedViewSet,
    viewsets.ModelViewSet,
):
    """
    CRUD completo para el modelo Producto.

    Los productos son el núcleo del catálogo. El serializer calcula en tiempo
    real el 'stock' (unidades disponibles) y 'salePrice' (precio de venta)
    para cada producto listado. Este cálculo dinámico garantiza que los datos
    mostrados en el POS (Ventas.tsx) siempre reflejen el estado actual del
    inventario sin necesidad de actualizar un campo almacenado.
    """
    queryset = Producto.objects.all()
    serializer_class = ProductoSerializer
    permission_classes = [IsAuthenticated, IsAdminOrReadOnly]
    pagination_class = None
    MODULO_AUDITORIA = 'PRODUCTOS'

    @staticmethod
    def _codigo_duplicado_response():
        return Response(
            {'code': ['Ya existe un producto con este código en esta miscelánea.']},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @staticmethod
    def _constraint_name(error):
        cause = getattr(error, '__cause__', None)
        return getattr(getattr(cause, 'diag', None), 'constraint_name', None)

    def create(self, request, *args, **kwargs):
        try:
            with transaction.atomic():
                return super().create(request, *args, **kwargs)
        except IntegrityError as error:
            if self._constraint_name(error) == 'unique_codigo_producto_por_miscelanea':
                return self._codigo_duplicado_response()
            return Response(
                {'detail': 'No se pudo crear el producto por un conflicto de datos.'},
                status=status.HTTP_409_CONFLICT,
            )

    def update(self, request, *args, **kwargs):
        try:
            with transaction.atomic():
                return super().update(request, *args, **kwargs)
        except IntegrityError as error:
            if self._constraint_name(error) == 'unique_codigo_producto_por_miscelanea':
                return self._codigo_duplicado_response()
            return Response(
                {'detail': 'No se pudo actualizar el producto por un conflicto de datos.'},
                status=status.HTTP_409_CONFLICT,
            )

    @action(detail=False, methods=['get'], url_path='buscar-codigo')
    def buscar_codigo(self, request):
        try:
            codigo = limpiar_y_validar_codigo_producto(
                request.query_params.get('codigo')
            )
        except DjangoValidationError as error:
            return Response(
                {'code': list(error.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if codigo is None:
            return Response(
                {'code': ['Ingresa un código para buscar.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        productos = list(
            self.get_queryset()
            .filter(Codigo__in=variantes_equivalentes_codigo_producto(codigo))
            .order_by('IdProducto')[:2]
        )
        if not productos:
            return Response(
                {'detail': 'No se encontró un producto con ese código.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if len(productos) > 1:
            return Response(
                {
                    'detail': (
                        'El código coincide con más de un producto por la '
                        'equivalencia UPC-A/EAN-13. Corrige los códigos del '
                        'catálogo antes de continuar.'
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(self.get_serializer(productos[0]).data)
