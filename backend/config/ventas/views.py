"""
Vistas de Ventas y Reportes — App 'ventas'
============================================

Contiene:
  - ViewSets CRUD básicos para Venta y DetalleVenta.
  - Vistas de procesamiento atómico: ProcesarVenta, AnularVenta.
  - Vista de estadísticas del Dashboard.
  - Vistas de reportes gerenciales (funciones SQL simples).
  - Vistas de reportes avanzados (stored procedures sp_*).

PATRÓN GENERAL DE REPORTES:
    Cada vista de reporte delega la lógica a una función PL/pgSQL en PostgreSQL.
    Django solo actúa como proxy HTTP: recibe parámetros, los valida, los pasa
    a la función SQL y serializa la respuesta.
    Ventaja: La lógica de negocio compleja (JOINs, agregaciones, filtros) vive
    en la BD donde puede optimizarse con índices y planes de ejecución eficientes.

NORMALIZACIÓN DE CLAVES (k.lower()):
    Todas las respuestas de reportes normalizan las claves de columna a
    minúsculas con `{k.lower(): v for k, v in zip(columns, row)}`.
    PostgreSQL puede devolver columnas en cualquier capitalización dependiendo
    de cómo se definieron en la función. El frontend React espera siempre
    snake_case en minúsculas (ej: 'total_ventas', no 'Total_Ventas').
"""

import logging
from decimal import Decimal

from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from datetime import timedelta
from django.db import transaction, connection
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncDate

from .models import Venta, DetalleVenta
from catalogo.models import Producto, Proveedor
from inventario.models import (
    DetalleEntradaInventario,
    EntradaInventario,
    Inventario,
    Perdida,
)
from usuarios.permissions import IsAdminRole, IsAdminOrReadOnly, CanProcessSale
from .serializers import VentaSerializer, DetalleVentaSerializer
from auditoria.mixins import AuditoriaMixin, registrar_evento_manual
from usuarios.tenant import TenantScopedViewSet


logger = logging.getLogger(__name__)


class AnulacionInvalida(Exception):
    """Error de negocio seguro al rechazar una anulación."""


def _preleer_referencias_venta(ids_inventario, tenant_id):
    """Obtiene los padres que se bloquearán; no valida la venta definitivamente."""
    referencias_unidad = list(
        Inventario.objects.filter(
            IdInventario__in=ids_inventario,
            Miscelanea_id=tenant_id,
        )
        .order_by('IdInventario')
        .values_list('IdInventario', 'IdDetalleEntrada_id')
    )
    if len(referencias_unidad) != len(ids_inventario):
        raise ValueError(
            'Todas las unidades deben existir y pertenecer a la miscelánea.'
        )

    detalle_por_unidad = dict(referencias_unidad)
    ids_detalle = sorted(set(detalle_por_unidad.values()))
    referencias_detalle = list(
        DetalleEntradaInventario.objects.filter(
            IdDetalleEntrada__in=ids_detalle,
            Miscelanea_id=tenant_id,
        )
        .order_by('IdDetalleEntrada')
        .values_list('IdDetalleEntrada', 'IdEntradaInventario_id')
    )
    if len(referencias_detalle) != len(ids_detalle):
        raise ValueError(
            'La unidad seleccionada tiene relaciones inconsistentes.'
        )

    return detalle_por_unidad, dict(referencias_detalle)


# ─── VIEWSETS CRUD ────────────────────────────────────────────────────────────

class VentaViewSet(AuditoriaMixin, TenantScopedViewSet, viewsets.ReadOnlyModelViewSet):
    """
    Consulta de ventas; las escrituras se realizan por operaciones dedicadas.

    pagination_class = None: Se desactiva para que el historial completo
    sea accesible en una sola petición. El frontend pagina visualmente.

    Permisos: IsAdminOrReadOnly → vendedores pueden consultar (GET) el
    historial de ventas pero no crear/modificar directamente (para eso
    existe ProcesarVentaView que tiene su propia lógica atómica).
    """
    queryset = Venta.objects.all()
    serializer_class = VentaSerializer
    permission_classes = [IsAuthenticated, IsAdminOrReadOnly]
    pagination_class = None
    MODULO_AUDITORIA = 'VENTAS'


class DetalleVentaViewSet(AuditoriaMixin, TenantScopedViewSet, viewsets.ReadOnlyModelViewSet):
    """
    Consulta de detalles individuales de venta.

    Restringido a IsAdminRole porque modificar detalles de venta directamente
    (sin pasar por ProcesarVentaView) podría dejar el inventario inconsistente.
    """
    queryset = DetalleVenta.objects.all()
    serializer_class = DetalleVentaSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    MODULO_AUDITORIA = 'VENTAS'


# ─── PROCESAMIENTO DE VENTAS ──────────────────────────────────────────────────

class ProcesarVentaView(APIView):
    """
    Endpoint principal de facturación. Registra una venta completa de forma atómica.

    POR QUÉ UNA VISTA SEPARADA (no usar VentaViewSet.create()):
        El proceso de venta involucra múltiples tablas (Venta, DetalleVenta,
        Inventario) y requiere select_for_update() para evitar vender la misma
        unidad dos veces en peticiones concurrentes. Un ModelViewSet estándar
        no tiene esta lógica transaccional.

    Endpoint: POST /api/ventas/procesar/

    Payload:
        {
          "usuarioId": 2,
          "fecha": "2026-04-20T14:30:00Z",
          "total": 150.00,
          "detalles": [
            {"inventarioId": 42, "precioVentaUnitario": 75.00},
            {"inventarioId": 43, "precioVentaUnitario": 75.00}
          ]
        }
    """
    # CanProcessSale: Permite tanto a admins como a vendedores registrar ventas.
    permission_classes = [CanProcessSale]

    def post(self, request):
        """
        Procesa una venta completa de forma transaccional.

        El bloque atómico va dentro del try para que, si algo falla, el rollback
        ocurra antes de registrar el error en auditoría (si no, la transacción
        dañada impide escribir y el cliente recibe un 500 sin mensaje).
        """
        if request.user.Miscelanea_id is None:
            return Response(
                {'detail': 'La cuenta no tiene una miscelánea asignada.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        fecha = request.data.get('fecha')
        detalles = request.data.get('detalles', [])

        try:
            if not detalles:
                raise ValueError('La venta no tiene productos.')
            ids_inventario = [detalle.get('inventarioId') for detalle in detalles]
            if len(ids_inventario) != len(set(ids_inventario)):
                raise ValueError('No se puede repetir una unidad en la venta.')
            with transaction.atomic():
                (
                    detalle_preleido_por_unidad,
                    entrada_preleida_por_detalle,
                ) = _preleer_referencias_venta(
                    ids_inventario,
                    request.user.Miscelanea_id,
                )

                ids_entrada = sorted(set(entrada_preleida_por_detalle.values()))
                entradas = list(
                    EntradaInventario.objects.select_for_update(of=('self',))
                    .select_related('IdProveedor')
                    .filter(
                        IdEntradaInventario__in=ids_entrada,
                        Miscelanea_id=request.user.Miscelanea_id,
                    )
                    .order_by('IdEntradaInventario')
                )
                entradas_por_id = {
                    entrada.IdEntradaInventario: entrada for entrada in entradas
                }
                if set(entradas_por_id) != set(ids_entrada) or any(
                    entrada.IdProveedor.Miscelanea_id
                    != request.user.Miscelanea_id
                    for entrada in entradas
                ):
                    raise ValueError(
                        'La unidad seleccionada tiene relaciones inconsistentes.'
                    )

                ids_detalle = sorted(entrada_preleida_por_detalle)
                detalles_compra = list(
                    DetalleEntradaInventario.objects.select_for_update(
                        of=('self',)
                    )
                    .select_related(
                        'IdEntradaInventario',
                        'IdEntradaInventario__IdProveedor',
                        'IdProducto',
                        'IdProducto__IdCategoria',
                    )
                    .filter(
                        IdDetalleEntrada__in=ids_detalle,
                        Miscelanea_id=request.user.Miscelanea_id,
                    )
                    .order_by('IdDetalleEntrada')
                )
                detalles_compra_por_id = {
                    detalle.IdDetalleEntrada: detalle
                    for detalle in detalles_compra
                }
                if set(detalles_compra_por_id) != set(ids_detalle):
                    raise ValueError(
                        'La unidad seleccionada tiene relaciones inconsistentes.'
                    )
                for detalle_compra in detalles_compra:
                    entrada_id = entrada_preleida_por_detalle.get(
                        detalle_compra.IdDetalleEntrada
                    )
                    if (
                        detalle_compra.IdEntradaInventario_id != entrada_id
                        or entrada_id not in entradas_por_id
                        or detalle_compra.IdEntradaInventario.Miscelanea_id
                        != request.user.Miscelanea_id
                        or detalle_compra.IdEntradaInventario.IdProveedor.Miscelanea_id
                        != request.user.Miscelanea_id
                        or detalle_compra.IdProducto.Miscelanea_id
                        != request.user.Miscelanea_id
                        or detalle_compra.IdProducto.IdCategoria.Miscelanea_id
                        != request.user.Miscelanea_id
                    ):
                        raise ValueError(
                            'La unidad seleccionada tiene relaciones inconsistentes.'
                        )

                inventarios = list(
                    Inventario.objects.select_for_update(of=('self',))
                    .filter(
                        IdInventario__in=ids_inventario,
                        Miscelanea_id=request.user.Miscelanea_id,
                    )
                    .order_by('IdInventario')
                )
                inventarios_por_id = {
                    inventario.IdInventario: inventario
                    for inventario in inventarios
                }
                if set(inventarios_por_id) != set(ids_inventario):
                    raise ValueError(
                        'Todas las unidades deben existir y pertenecer a la miscelánea.'
                    )
                for inventario in inventarios:
                    detalle_id = detalle_preleido_por_unidad.get(
                        inventario.IdInventario
                    )
                    if (
                        inventario.IdDetalleEntrada_id != detalle_id
                        or detalle_id not in detalles_compra_por_id
                    ):
                        raise ValueError(
                            'La unidad seleccionada tiene relaciones inconsistentes.'
                        )
                    if inventario.Estado != 'Disponible':
                        raise ValueError(
                            'Uno de los productos ya fue vendido desde otro dispositivo. '
                            'Se actualizó el inventario, intenta de nuevo.'
                        )

                venta = Venta.objects.create(
                    IdUsuario=request.user,
                    Miscelanea_id=request.user.Miscelanea_id,
                    Fecha=fecha,
                    Total=Decimal('0.00'),
                )
                total_calculado = Decimal('0.00')
                for d in detalles:
                    inv_id = d.get('inventarioId')
                    inventario = inventarios_por_id[inv_id]
                    detalle_compra = detalles_compra_por_id[
                        inventario.IdDetalleEntrada_id
                    ]
                    precio = Decimal(str(
                        detalle_compra.IdProducto.calcular_precio_venta()
                    ))
                    DetalleVenta.objects.create(
                        IdVenta=venta,
                        IdInventario=inventario,
                        Miscelanea_id=request.user.Miscelanea_id,
                        PrecioVentaUnitario=precio
                    )
                    total_calculado += precio
                    inventario.Estado = "Vendido"
                    inventario.save()
                venta.Total = total_calculado
                venta.save(update_fields=['Total'])
                registrar_evento_manual(
                    request=request,
                    accion='PROCESAR',
                    modulo='VENTAS',
                    descripcion=f'Venta #{venta.IdVenta} procesada por {request.user.Nombre}.',
                    datos_nuevos={
                        'id_venta': venta.IdVenta,
                        'usuario_id': request.user.IdUsuario,
                        'total': str(venta.Total),
                        'cantidad_detalles': len(detalles),
                    },
                )
            return Response({
                "message": "Venta exitosa",
                "id": venta.IdVenta,
                "fecha": venta.Fecha,
                "total": venta.Total
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            try:
                registrar_evento_manual(
                    request=request,
                    accion='PROCESAR',
                    modulo='VENTAS',
                    descripcion=f'Error al procesar venta: {str(e)}',
                    resultado='FALLIDO',
                )
            except Exception:
                pass
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class AnularVentaView(APIView):
    """
    Anula una venta y revierte el stock de todas sus unidades.

    POR QUÉ ANULAR EN LUGAR DE BORRAR:
        Borrar una venta elimina el registro contable. La anulación preserva
        el historial (importante para auditoría y reportes financieros) mientras
        deja de contar la venta como ingreso activo.

    Endpoint: POST /api/ventas/<pk>/anular/
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, pk):
        """
        Anula la venta con ID=pk y revierte el inventario.

        GUARD: No se puede anular una venta que ya fue anulada (idempotencia).

        Args:
            request: Petición HTTP.
            pk (int): ID de la venta a anular.

        Returns:
            Response: 200 OK si exitoso. 404 si no existe. 400 si ya estaba anulada.
        """
        if request.user.Miscelanea_id is None:
            return Response(
                {'detail': 'La cuenta no tiene una miscelánea asignada.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            with transaction.atomic():
                # El bloqueo de la venta serializa dos anulaciones simultáneas.
                venta = Venta.objects.select_for_update().get(
                    pk=pk,
                    Miscelanea_id=request.user.Miscelanea_id,
                )
                if venta.Estado != 'Completada':
                    raise AnulacionInvalida(
                        "Solo se pueden anular ventas completadas."
                    )

                detalles = list(
                    DetalleVenta.objects.select_for_update()
                    .filter(
                        IdVenta=venta,
                        Miscelanea_id=request.user.Miscelanea_id,
                    )
                    .order_by('IdDetalleVenta')
                )
                if not detalles:
                    raise AnulacionInvalida(
                        "La venta no tiene unidades que se puedan restituir."
                    )

                ids_inventario = [
                    detalle.IdInventario_id for detalle in detalles
                ]
                if len(ids_inventario) != len(set(ids_inventario)):
                    raise AnulacionInvalida(
                        "La venta contiene referencias de inventario duplicadas."
                    )

                inventarios = list(
                    Inventario.objects.select_for_update()
                    .filter(
                        IdInventario__in=ids_inventario,
                        Miscelanea_id=request.user.Miscelanea_id,
                    )
                    .order_by('IdInventario')
                )
                inventarios_por_id = {
                    inventario.IdInventario: inventario
                    for inventario in inventarios
                }
                if (
                    len(inventarios_por_id) != len(ids_inventario)
                    or any(
                        inventarios_por_id[inventario_id].Estado != 'Vendido'
                        for inventario_id in ids_inventario
                        if inventario_id in inventarios_por_id
                    )
                ):
                    raise AnulacionInvalida(
                        "No se pudo restituir el inventario de la venta."
                    )

                fecha_movimiento = timezone.now()
                for inventario in inventarios:
                    inventario.Estado = 'Disponible'
                    inventario.FechaMovimiento = fecha_movimiento
                    inventario.save(update_fields=['Estado', 'FechaMovimiento'])

                venta.Estado = 'Anulada'
                venta.save(update_fields=['Estado'])

                registrar_evento_manual(
                    request=request,
                    accion='ANULAR',
                    modulo='VENTAS',
                    descripcion=f'Venta #{pk} anulada por {getattr(request.user, "Nombre", "Admin")}. Stock revertido.',
                    datos_anteriores={'estado': 'Completada', 'id_venta': pk},
                    datos_nuevos={'estado': 'Anulada', 'id_venta': pk},
                )

            return Response(
                {"message": f"Venta #{pk} anulada correctamente. Stock revertido."},
                status=status.HTTP_200_OK,
            )
        except Venta.DoesNotExist:
            return Response(
                {"error": "Venta no encontrada."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except AnulacionInvalida as exc:
            logger.warning("Anulación de venta rechazada: %s", exc)
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception("Error interno al anular la venta %s", pk)
            return Response(
                {"error": "No se pudo anular la venta."},
                status=status.HTTP_400_BAD_REQUEST,
            )


# ─── DASHBOARD ────────────────────────────────────────────────────────────────

class DashboardStatsView(APIView):
    """
    Provee todas las estadísticas necesarias para el Dashboard en una sola petición.

    FILTRO DE PRIVACIDAD POR ROL:
        Los vendedores ven el Dashboard pero NO los datos financieros sensibles
        (weeklySales, weeklyLosses). Estos campos se anulan a None para su rol.
        El frontend maneja None mostrando '---' en lugar del valor.
        Esto implementa el principio de mínimo privilegio: cada rol solo ve
        lo que necesita para hacer su trabajo.

    Endpoint: GET /api/ventas/dashboard/stats/
    """
    permission_classes = [IsAuthenticated, IsAdminOrReadOnly]

    def get(self, request):
        """
        Calcula y devuelve las estadísticas del Dashboard.

        Returns:
            Response: JSON con métricas de los últimos 7 días, productos con
                      stock bajo, ventas recientes y datos para el gráfico.
        """
        if request.user.Miscelanea_id is None:
            return Response({
                "totalProducts": 0,
                "weeklySales": 0.0,
                "totalProveedores": 0,
                "lowStockItems": [],
                "recentSales": [],
                "weeklyLosses": 0.0,
                "chartData": [{"date": "Hoy", "total": 0}],
            })

        now = timezone.now()
        seven_days_ago = now - timedelta(days=7)

        # ── Métricas de conteo simples ─────────────────────────────────────
        tenant_id = request.user.Miscelanea_id
        total_products = Producto.objects.filter(Miscelanea_id=tenant_id).count()
        ventas_semana = Venta.objects.filter(Miscelanea_id=tenant_id, Fecha__gte=seven_days_ago)
        # aggregate(total=Sum('Total')): Un solo query SQL con SUM().
        # El `or 0` maneja el caso de que no haya ventas (Sum devuelve None).
        weekly_sales = ventas_semana.aggregate(total=Sum('Total'))['total'] or 0
        total_proveedores = Proveedor.objects.filter(Miscelanea_id=tenant_id).count()

        # ── Productos con stock bajo (entre 10 y 20 unidades disponibles inclusive) ────
        # annotate(): Añade el campo calculado 'stock' a cada Producto del queryset.
        # Count con filter(): Equivale a COUNT(CASE WHEN Estado='Disponible' THEN 1 END) en SQL.
        # detalleentradainventario__inventarios: Navegación de FK a través de related_name.
        low_stock_qs = Producto.objects.filter(
            Miscelanea_id=tenant_id,
            IdCategoria__Miscelanea_id=tenant_id,
        ).annotate(
            stock=Count(
                'detalleentradainventario__inventarios',
                filter=Q(
                    detalleentradainventario__Miscelanea_id=tenant_id,
                    detalleentradainventario__inventarios__Miscelanea_id=tenant_id,
                    detalleentradainventario__inventarios__Estado='Disponible',
                )
            )
        ).filter(stock__lte=20, stock__gte=10).order_by('stock')[:5]

        low_stock_items = [{
            "id": p.IdProducto,
            "name": p.Nombre,
            "stock": p.stock,
            "category": p.IdCategoria.Nombre if p.IdCategoria else "General"
        } for p in low_stock_qs]

        # ── 5 ventas más recientes ─────────────────────────────────────────
        recent_sales_qs = Venta.objects.filter(
            Miscelanea_id=tenant_id
        ).order_by('-Fecha')[:5]
        recent_sales = [{
            "id": v.IdVenta,
            "time": v.Fecha.strftime("%I:%M %p"),
            "items": v.detalles.filter(
                Miscelanea_id=tenant_id,
                IdInventario__Miscelanea_id=tenant_id,
                IdInventario__IdDetalleEntrada__Miscelanea_id=tenant_id,
                IdInventario__IdDetalleEntrada__IdEntradaInventario__Miscelanea_id=tenant_id,
                IdInventario__IdDetalleEntrada__IdProducto__Miscelanea_id=tenant_id,
            ).count(),
            "total": float(v.Total)
        } for v in recent_sales_qs]

        # ── Pérdidas de los últimos 7 días ─────────────────────────────────
        perdidas_semana = Perdida.objects.filter(Miscelanea_id=tenant_id, Fecha__gte=seven_days_ago)
        weekly_losses = perdidas_semana.aggregate(total=Sum('Total'))['total'] or 0

        # ── Datos para el gráfico de barras de ventas diarias ──────────────
        # TruncDate: Trunca el DateTimeField a solo la fecha (sin hora).
        # Permite agrupar ventas del mismo día aunque tengan horas distintas.
        sales_by_date = ventas_semana.annotate(date=TruncDate('Fecha')).values('date').annotate(
            total=Sum('Total')
        ).order_by('date')

        chart_data = [{"date": s['date'].strftime('%a'), "total": float(s['total'])} for s in sales_by_date]
        # Fallback: Si no hay ventas en la semana, el gráfico no queda vacío
        if not chart_data:
            chart_data = [{"date": "Hoy", "total": 0}]

        response_data = {
            "totalProducts": total_products,
            "weeklySales": float(weekly_sales),
            "totalProveedores": total_proveedores,
            "lowStockItems": low_stock_items,
            "recentSales": recent_sales,
            "weeklyLosses": float(weekly_losses),
            "chartData": chart_data
        }

        # ── Filtro de privacidad: vendedores no ven datos financieros ───────
        # getattr con None como fallback: defensa contra usuarios sin el campo 'Rol'
        if getattr(request.user, 'Rol', None) == 'vendedor':
            response_data['weeklySales'] = None
            response_data['weeklyLosses'] = None

        return Response(response_data)


# ─── REPORTES SIMPLES (funciones PostgreSQL sin prefijo sp_) ──────────────────

class ReporteGerencialView(APIView):
    """
    Reporte ejecutivo: total vendido, promedio y producto estrella en un periodo.

    Llama a la función PostgreSQL: reporte_gerencial(inicio DATE, fin DATE)
    Retorna una sola fila con: (total_ventas, promedio_venta, producto_mas_vendido)
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        inicio, fin = request.query_params.get('inicio'), request.query_params.get('fin')
        t = request.user.Miscelanea_id
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    (SELECT SUM(v."Total") FROM "Venta" v
                      WHERE v."IdMiscelanea" = %s AND v."Fecha"::DATE BETWEEN %s::DATE AND %s::DATE),
                    (SELECT AVG(v."Total") FROM "Venta" v
                      WHERE v."IdMiscelanea" = %s AND v."Fecha"::DATE BETWEEN %s::DATE AND %s::DATE),
                    (SELECT p."Nombre"
                       FROM "DetalleVenta" dv
                       JOIN "Venta" v ON dv."IdVenta" = v."IdVenta"
                       JOIN "Inventario" i ON dv."IdInventario" = i."IdInventario"
                       JOIN "DetalleEntradaInventario" dei ON i."IdDetalleEntrada" = dei."IdDetalleEntrada"
                       JOIN "Producto" p ON dei."IdProducto" = p."IdProducto"
                      WHERE dv."IdMiscelanea" = %s
                        AND v."IdMiscelanea" = %s
                        AND i."IdMiscelanea" = %s
                        AND dei."IdMiscelanea" = %s
                        AND p."IdMiscelanea" = %s
                        AND v."Fecha"::DATE BETWEEN %s::DATE AND %s::DATE
                      GROUP BY p."Nombre" ORDER BY COUNT(*) DESC LIMIT 1)
            """, [
                t, inicio, fin,
                t, inicio, fin,
                t, t, t, t, t, inicio, fin,
            ])
            row = cursor.fetchone()
        return Response({
            "total_ventas": float(row[0]) if row and row[0] else 0,
            "promedio_venta": float(row[1]) if row and row[1] else 0,
            "producto_mas_vendido": row[2] if row and row[2] else "N/A"
        })


class ReportePivotVentasView(APIView):
    """
    Análisis mensual de ventas de un producto en un año (tabla pivot 12 columnas).

    Retorna una fila con columnas: producto, ene, feb, mar, abr, may, jun,
                                   jul, ago, sep, oct, nov, dic

    DECISIÓN DE DISEÑO — Pivot inline con CASE WHEN:
        Se reemplazó la dependencia de crosstab() (extensión tablefunc) por
        un pivot directo con CASE WHEN. Esto garantiza compatibilidad con
        cualquier instancia PostgreSQL (Railway, Render, etc.) sin requerir
        extensiones adicionales. El frontend (Reportes.tsx) mapea directamente
        cada columna (d.ene, d.feb...) a una celda de la tabla visual.
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    EMPTY_ROW = {"producto": "Sin datos", "ene": 0, "feb": 0, "mar": 0, "abr": 0,
                 "may": 0, "jun": 0, "jul": 0, "ago": 0, "sep": 0, "oct": 0, "nov": 0, "dic": 0}

    def get(self, request):
        anio = request.query_params.get('anio')
        prod_id = request.query_params.get('productoId') or request.query_params.get('producto_id')

        if not anio or not prod_id:
            return Response({"error": "Faltan parámetros (anio o producto_id)"}, status=400)

        try:
            # Pivot inline: no depende de tablefunc/crosstab
            query = """
                SELECT
                    p."Nombre"                                                          AS producto,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") =  1 THEN dv."PrecioVentaUnitario" END), 0) AS ene,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") =  2 THEN dv."PrecioVentaUnitario" END), 0) AS feb,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") =  3 THEN dv."PrecioVentaUnitario" END), 0) AS mar,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") =  4 THEN dv."PrecioVentaUnitario" END), 0) AS abr,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") =  5 THEN dv."PrecioVentaUnitario" END), 0) AS may,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") =  6 THEN dv."PrecioVentaUnitario" END), 0) AS jun,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") =  7 THEN dv."PrecioVentaUnitario" END), 0) AS jul,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") =  8 THEN dv."PrecioVentaUnitario" END), 0) AS ago,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") =  9 THEN dv."PrecioVentaUnitario" END), 0) AS sep,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") = 10 THEN dv."PrecioVentaUnitario" END), 0) AS oct,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") = 11 THEN dv."PrecioVentaUnitario" END), 0) AS nov,
                    COALESCE(SUM(CASE WHEN EXTRACT(MONTH FROM v."Fecha") = 12 THEN dv."PrecioVentaUnitario" END), 0) AS dic
                FROM "Producto" p
                JOIN "DetalleEntradaInventario" dei ON p."IdProducto" = dei."IdProducto"
                JOIN "Inventario" i ON dei."IdDetalleEntrada" = i."IdDetalleEntrada"
                JOIN "DetalleVenta" dv ON i."IdInventario" = dv."IdInventario"
                JOIN "Venta" v ON dv."IdVenta" = v."IdVenta"
                WHERE EXTRACT(YEAR FROM v."Fecha") = %s
                  AND p."IdProducto" = %s
                                    AND p."IdMiscelanea" = %s
                                    AND dei."IdMiscelanea" = %s
                                    AND i."IdMiscelanea" = %s
                                    AND dv."IdMiscelanea" = %s
                                    AND v."IdMiscelanea" = %s
                GROUP BY p."Nombre"
            """
            with connection.cursor() as cursor:
                tenant_id = request.user.Miscelanea_id
                cursor.execute(query, [
                    int(anio), int(prod_id),
                    tenant_id, tenant_id, tenant_id, tenant_id, tenant_id,
                ])
                columns = [col[0] for col in cursor.description]
                row = cursor.fetchone()

            if row:
                return Response({k.lower(): v for k, v in zip(columns, row)})
            return Response(self.EMPTY_ROW)

        except Exception as e:
            return Response({"error": str(e)}, status=500)


class ProductosPorProveedorView(APIView):
    """
    Lista los productos vigentes suministrados por un proveedor específico.
    Llama a: reporte_productos_por_proveedor(proveedor_id INT)
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        proveedor_id = request.query_params.get('proveedorId') or request.query_params.get('proveedor_id')
        if not proveedor_id:
            return Response({"error": "Falta proveedorId o proveedor_id"}, status=400)
        with connection.cursor() as cursor:
            t = request.user.Miscelanea_id
            cursor.execute("""
                SELECT p."IdProducto" AS id, p."Nombre"::VARCHAR AS producto,
                       (SELECT COUNT(*) FROM "Inventario" inv
                          JOIN "DetalleEntradaInventario" d2 ON inv."IdDetalleEntrada" = d2."IdDetalleEntrada"
                         WHERE d2."IdProducto" = p."IdProducto" AND inv."Estado" = 'Disponible'
                           AND inv."IdMiscelanea" = %(t)s
                           AND d2."IdMiscelanea" = %(t)s) AS unidades_disponibles
                FROM "Producto" p
                WHERE p."IdMiscelanea" = %(t)s AND EXISTS (
                    SELECT 1 FROM "DetalleEntradaInventario" dei
                    JOIN "EntradaInventario" ei ON dei."IdEntradaInventario" = ei."IdEntradaInventario"
                    JOIN "Proveedor" pr ON ei."IdProveedor" = pr."IdProveedor"
                    WHERE dei."IdProducto" = p."IdProducto"
                      AND dei."IdMiscelanea" = %(t)s
                      AND ei."IdMiscelanea" = %(t)s
                      AND pr."IdMiscelanea" = %(t)s
                      AND ei."IdProveedor" = %(provider)s)
            """, {'t': t, 'provider': proveedor_id})
            columns = [col[0] for col in cursor.description]
            result = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return Response(result)


class ReporteDevolucionesView(APIView):
    """
    Reporte de devoluciones procesadas en un rango de fechas.
    Llama a: devoluciones_por_fecha(inicio DATE, fin DATE)
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        inicio = request.query_params.get('inicio')
        fin = request.query_params.get('fin')
        if not inicio or not fin:
            return Response({"error": "Faltan fechas de inicio y fin"}, status=400)
        try:
            with connection.cursor() as cursor:
                # Unimos los resultados de la función con la tabla de Usuarios para obtener el nombre
                query = """
                    SELECT sd."IdSolicitudDevolucion" AS id_solicitud,
                           p."Nombre"::TEXT AS producto,
                           COUNT(*)::INT AS cantidad,
                           COALESCE(dsd."MotivoRechazo", '')::TEXT AS motivo,
                           sd."Fecha"::DATE AS fecha,
                           sd."Estado"::TEXT AS estado,
                           u."Nombre" AS usuario
                    FROM "SolicitudDevolucion" sd
                    JOIN "DetalleSolicitudDevolucion" dsd ON sd."IdSolicitudDevolucion" = dsd."IdSolicitudDevolucion"
                    JOIN "Inventario" i ON dsd."IdInventario" = i."IdInventario"
                    JOIN "DetalleEntradaInventario" dei ON i."IdDetalleEntrada" = dei."IdDetalleEntrada"
                    JOIN "EntradaInventario" ei ON sd."IdEntradaInventario" = ei."IdEntradaInventario"
                    JOIN "Producto" p ON dei."IdProducto" = p."IdProducto"
                    LEFT JOIN "Usuario" u ON sd."IdUsuario" = u."IdUsuario"
                      AND u."IdMiscelanea" = %(t)s
                    WHERE sd."IdMiscelanea" = %(t)s
                      AND dsd."IdMiscelanea" = %(t)s
                      AND i."IdMiscelanea" = %(t)s
                      AND dei."IdMiscelanea" = %(t)s
                      AND ei."IdMiscelanea" = %(t)s
                      AND p."IdMiscelanea" = %(t)s
                      AND dei."IdEntradaInventario" = ei."IdEntradaInventario"
                      AND sd."Fecha" >= %(inicio)s::DATE
                      AND sd."Fecha" < (%(fin)s::DATE + INTERVAL '1 day')
                    GROUP BY sd."IdSolicitudDevolucion", p."Nombre", dsd."MotivoRechazo",
                             sd."Fecha", sd."Estado", u."Nombre"
                    ORDER BY sd."Fecha"
                """
                cursor.execute(query, {
                    't': request.user.Miscelanea_id,
                    'inicio': inicio,
                    'fin': fin,
                })
                columns = [col[0] for col in cursor.description]
                # Normalización a minúsculas: El frontend accede a d.producto, d.cantidad, etc.
                result = [{k.lower(): v for k, v in zip(columns, row)} for row in cursor.fetchall()]
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=400)


class ReportePerdidasView(APIView):
    """
    Reporte de pérdidas registradas en un rango de fechas.
    Llama a: perdidas_por_fecha(inicio DATE, fin DATE)
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        inicio = request.query_params.get('inicio')
        fin = request.query_params.get('fin')
        if not inicio or not fin:
            return Response({"error": "Faltan fechas"}, status=400)
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT pda."IdPerdida" AS id_perdida,
                           pr."Nombre"::TEXT AS producto,
                           COUNT(*)::INT AS cantidad,
                           pda."TipoPerdida"::TEXT AS motivo,
                           pda."Total" AS total,
                           pda."Fecha"::DATE AS fecha
                    FROM "Perdida" pda
                    JOIN "DetallePerdida" dp ON pda."IdPerdida" = dp."IdPerdida"
                    JOIN "Inventario" i ON dp."IdInventario" = i."IdInventario"
                    JOIN "DetalleEntradaInventario" dei ON i."IdDetalleEntrada" = dei."IdDetalleEntrada"
                    JOIN "Producto" pr ON dei."IdProducto" = pr."IdProducto"
                    WHERE pda."IdMiscelanea" = %(t)s
                      AND dp."IdMiscelanea" = %(t)s
                      AND i."IdMiscelanea" = %(t)s
                      AND dei."IdMiscelanea" = %(t)s
                      AND pr."IdMiscelanea" = %(t)s
                      AND pda."Fecha" >= %(inicio)s::DATE
                      AND pda."Fecha" < (%(fin)s::DATE + INTERVAL '1 day')
                    GROUP BY pda."IdPerdida", pr."Nombre", pda."TipoPerdida", pda."Total", pda."Fecha"
                    ORDER BY pda."Fecha"
                """, {
                    't': request.user.Miscelanea_id,
                    'inicio': inicio,
                    'fin': fin,
                })
                columns = [col[0] for col in cursor.description]
                result = [{k.lower(): v for k, v in zip(columns, row)} for row in cursor.fetchall()]
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=400)


# ─── REPORTES AVANZADOS (stored procedures sp_) ───────────────────────────────

class ReporteVentasFiltradasView(APIView):
    """
    Filtra ventas por rango de fechas y estado (Completada/Anulada/Todas).

    Llama a: sp_ventas_filtradas(inicio, fin, estado, usuario_id, monto_min, monto_max)
    Los parámetros 4, 5, 6 se pasan como NULL (sin filtro adicional).

    NORMALIZACIÓN DE 'estado':
        Si el usuario elige "Todas" en el frontend, el select envía una cadena
        vacía (""). Se debe normalizar a None antes de enviarlo a PostgreSQL,
        porque la función SQL espera NULL para "sin filtro de estado", no "".
        Pasar "" causaría que la función busque ventas con estado="" (vacío), 
        devolviendo cero resultados.
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        inicio = request.query_params.get('inicio')
        fin = request.query_params.get('fin')
        estado = request.query_params.get('estado', None)

        if not inicio or not fin:
            return Response({"error": "Faltan fechas de inicio y fin"}, status=400)

        try:
            with connection.cursor() as cursor:
                # Normalizar cadena vacía → None → NULL en PostgreSQL
                estado_param = estado if estado else None
                cursor.execute("""
                    SELECT v."IdVenta" AS id_venta, v."Fecha"::DATE AS fecha,
                           v."Estado"::VARCHAR AS estado, v."Total" AS total
                    FROM "Venta" v
                    WHERE v."IdMiscelanea" = %s
                      AND v."Fecha"::DATE BETWEEN %s::DATE AND %s::DATE
                      AND (%s::VARCHAR IS NULL OR v."Estado" = %s::VARCHAR)
                    ORDER BY v."Fecha" DESC
                """, [request.user.Miscelanea_id, inicio, fin, estado_param, estado_param])
                columns = [col[0] for col in cursor.description]
                result = [{k.lower(): v for k, v in zip(columns, row)} for row in cursor.fetchall()]
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=400)


class ReporteTopProductosView(APIView):
    """
    Devuelve los N productos más vendidos en un periodo, ordenados por ingreso.

    Llama a: sp_top_productos(inicio DATE, fin DATE)
    El LIMIT se aplica en Python (LIMIT %s en el SELECT) porque la función
    SQL no acepta un parámetro de límite en su firma actual. Esto evita
    modificar la función almacenada y mantiene la compatibilidad.
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        inicio = request.query_params.get('inicio')
        fin = request.query_params.get('fin')
        limite = request.query_params.get('limite', 10)

        if not inicio or not fin:
            return Response({"error": "Faltan fechas de inicio y fin"}, status=400)

        try:
            # Validar y convertir 'limite' a entero. Si el usuario envía "abc", falla aquí.
            limite = int(limite)
        except (ValueError, TypeError):
            return Response({"error": "El parámetro 'limite' debe ser un número entero"}, status=400)

        try:
            with connection.cursor() as cursor:
                # LIMIT aplicado directamente en el SQL envolvente,
                # ya que la función sp_top_productos solo acepta (inicio, fin).
                cursor.execute("""
                    SELECT p."Nombre"::VARCHAR AS nombre_producto,
                           c."Nombre"::VARCHAR AS categoria,
                           COUNT(dv."IdDetalleVenta")::BIGINT AS cantidad_vendida,
                           SUM(dv."PrecioVentaUnitario")::NUMERIC AS ingreso_bruto
                    FROM "DetalleVenta" dv
                    JOIN "Venta" v ON dv."IdVenta" = v."IdVenta"
                    JOIN "Inventario" i ON dv."IdInventario" = i."IdInventario"
                    JOIN "DetalleEntradaInventario" dei ON i."IdDetalleEntrada" = dei."IdDetalleEntrada"
                    JOIN "Producto" p ON dei."IdProducto" = p."IdProducto"
                    JOIN "Categoria" c ON p."IdCategoria" = c."IdCategoria"
                    WHERE dv."IdMiscelanea" = %s
                      AND v."IdMiscelanea" = %s
                      AND i."IdMiscelanea" = %s
                      AND dei."IdMiscelanea" = %s
                      AND p."IdMiscelanea" = %s
                      AND c."IdMiscelanea" = %s
                      AND v."Fecha"::DATE BETWEEN %s::DATE AND %s::DATE
                    GROUP BY p."IdProducto", p."Nombre", c."Nombre"
                    ORDER BY cantidad_vendida DESC
                    LIMIT %s
                """, [
                    request.user.Miscelanea_id,
                    request.user.Miscelanea_id,
                    request.user.Miscelanea_id,
                    request.user.Miscelanea_id,
                    request.user.Miscelanea_id,
                    request.user.Miscelanea_id,
                    inicio,
                    fin,
                    limite,
                ])
                columns = [col[0] for col in cursor.description]
                result = [{k.lower(): v for k, v in zip(columns, row)} for row in cursor.fetchall()]
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=400)


class ReporteGananciaProductoView(APIView):
    """
    Calcula la ganancia bruta por producto (o todos) en un periodo.

    Llama a: sp_ganancia_producto(inicio DATE, fin DATE, producto_id INT|NULL)

    Si producto_id es NULL, la función devuelve la ganancia de TODOS los productos.
    Si producto_id tiene un valor, filtra solo ese producto.

    NORMALIZACIÓN DE producto_id:
        Al igual que con 'estado' en otras vistas, una cadena vacía se normaliza
        a None para que PostgreSQL reciba NULL y la función aplique el comportamiento
        de "todos los productos".
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        inicio = request.query_params.get('inicio')
        fin = request.query_params.get('fin')
        producto_id = request.query_params.get('productoId') or request.query_params.get('producto_id') or None

        if not inicio or not fin:
            return Response({"error": "Faltan fechas de inicio y fin"}, status=400)

        try:
            with connection.cursor() as cursor:
                # Normalizar cadena vacía → None → NULL en SQL
                producto_id_param = producto_id if producto_id else None
                cursor.execute("""
                    SELECT p."Nombre"::VARCHAR AS nombre_producto,
                           SUM(dv."PrecioVentaUnitario")::NUMERIC AS total_ventas,
                           SUM(dei."PrecioCompraUnitario")::NUMERIC AS costo_total,
                           SUM(dv."PrecioVentaUnitario" - dei."PrecioCompraUnitario")::NUMERIC AS ganancia_bruta
                    FROM "DetalleVenta" dv
                    JOIN "Venta" v ON dv."IdVenta" = v."IdVenta"
                    JOIN "Inventario" i ON dv."IdInventario" = i."IdInventario"
                    JOIN "DetalleEntradaInventario" dei ON i."IdDetalleEntrada" = dei."IdDetalleEntrada"
                    JOIN "Producto" p ON dei."IdProducto" = p."IdProducto"
                    WHERE dv."IdMiscelanea" = %s
                      AND v."IdMiscelanea" = %s
                      AND i."IdMiscelanea" = %s
                      AND dei."IdMiscelanea" = %s
                      AND p."IdMiscelanea" = %s
                      AND v."Fecha"::DATE BETWEEN %s::DATE AND %s::DATE
                      AND (%s::INT IS NULL OR p."IdProducto" = %s::INT)
                    GROUP BY p."IdProducto", p."Nombre"
                """, [
                    request.user.Miscelanea_id,
                    request.user.Miscelanea_id,
                    request.user.Miscelanea_id,
                    request.user.Miscelanea_id,
                    request.user.Miscelanea_id,
                    inicio,
                    fin,
                    producto_id_param,
                    producto_id_param,
                ])
                columns = [col[0] for col in cursor.description]
                result = [{k.lower(): v for k, v in zip(columns, row)} for row in cursor.fetchall()]
            return Response(result)
        except Exception as e:
            return Response({"error": str(e)}, status=400)


class ReporteComparacionVentasView(APIView):
    """
    Compara el desempeño de ventas entre dos periodos de tiempo distintos.

    CASTING EXPLÍCITO ::DATE EN SQL:
        Las fechas se pasan como strings ('2026-01-01') desde el frontend.
        PostgreSQL puede inferir el tipo en muchos casos, pero con funciones
        polimórficas o cuando hay ambigüedad de tipos, el cast explícito
        ::DATE garantiza que PostgreSQL interprete el parámetro correctamente.
        Sin el cast, podría surgir un error: "could not determine data type of
        parameter $1" o resultados incorrectos por comparación de tipos mixtos.

    La función sp_comparar_ventas() devuelve UNA SOLA FILA con estas columnas:
        ventas_a, productos_a  ← Métricas del Periodo A
        ventas_b, productos_b  ← Métricas del Periodo B

    El frontend (ejecutarComparacion en Reportes.tsx) transforma esa única fila
    en MÚLTIPLES filas visuales para la tabla comparativa, calculando además
    la diferencia (periodo_b - periodo_a) para cada métrica.

    Endpoint: GET /api/ventas/reporte-comparacion-ventas/
    Query params: inicioA, finA, inicioB, finB (fechas YYYY-MM-DD)
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        # Capturar los 4 parámetros de fecha (2 períodos de comparación)
        inicio_a = request.query_params.get('inicioA') or request.query_params.get('inicio_a')
        fin_a = request.query_params.get('finA') or request.query_params.get('fin_a')
        inicio_b = request.query_params.get('inicioB') or request.query_params.get('inicio_b')
        fin_b = request.query_params.get('finB') or request.query_params.get('fin_b')

        # Validar que todos los parámetros estén presentes
        if not all([inicio_a, fin_a, inicio_b, fin_b]):
            return Response({"error": "Faltan fechas para comparar"}, status=400)

        try:
            with connection.cursor() as cursor:
                # ::DATE: Cast explícito para garantizar que PostgreSQL interprete
                # los parámetros como DATE y no como TEXT/TIMESTAMP, evitando
                # ambigüedad en la resolución de la firma de la función.
                t = request.user.Miscelanea_id
                cursor.execute("""
                    SELECT
                      COALESCE((SELECT SUM(v."Total") FROM "Venta" v
                                WHERE v."IdMiscelanea" = %(t)s AND v."Estado" = 'Completada'
                                  AND v."Fecha"::DATE BETWEEN %(ia)s::DATE AND %(fa)s::DATE), 0)::NUMERIC AS ventas_a,
                      COALESCE((SELECT COUNT(dv."IdDetalleVenta") FROM "DetalleVenta" dv
                                JOIN "Venta" v ON dv."IdVenta" = v."IdVenta"
                                JOIN "Inventario" i ON dv."IdInventario" = i."IdInventario"
                                WHERE dv."IdMiscelanea" = %(t)s
                                  AND v."IdMiscelanea" = %(t)s
                                  AND i."IdMiscelanea" = %(t)s
                                  AND v."Estado" = 'Completada'
                                  AND v."Fecha"::DATE BETWEEN %(ia)s::DATE AND %(fa)s::DATE), 0)::BIGINT AS productos_a,
                      COALESCE((SELECT SUM(v."Total") FROM "Venta" v
                                WHERE v."IdMiscelanea" = %(t)s AND v."Estado" = 'Completada'
                                  AND v."Fecha"::DATE BETWEEN %(ib)s::DATE AND %(fb)s::DATE), 0)::NUMERIC AS ventas_b,
                      COALESCE((SELECT COUNT(dv."IdDetalleVenta") FROM "DetalleVenta" dv
                                JOIN "Venta" v ON dv."IdVenta" = v."IdVenta"
                                JOIN "Inventario" i ON dv."IdInventario" = i."IdInventario"
                                WHERE dv."IdMiscelanea" = %(t)s
                                  AND v."IdMiscelanea" = %(t)s
                                  AND i."IdMiscelanea" = %(t)s
                                  AND v."Estado" = 'Completada'
                                  AND v."Fecha"::DATE BETWEEN %(ib)s::DATE AND %(fb)s::DATE), 0)::BIGINT AS productos_b
                """, {'t': t, 'ia': inicio_a, 'fa': fin_a, 'ib': inicio_b, 'fb': fin_b})
                columns = [col[0] for col in cursor.description]
                # Normalizar a minúsculas: El frontend accede a d.ventas_a, d.productos_b, etc.
                result = [{k.lower(): v for k, v in zip(columns, row)} for row in cursor.fetchall()]
            return Response(result)
        except Exception as e:
            # print() ayuda a diagnosticar errores de tipo de dato en la terminal de Django
            print(f"Error en comparación: {str(e)}")
            return Response({"error": str(e)}, status=400)
