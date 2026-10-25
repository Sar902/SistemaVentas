from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Prefetch

from inventario.models import (
    DetallePerdida,
    DetalleSolicitudDevolucion,
    Perdida,
    SolicitudDevolucion,
)


CONFIRMADAS = (
    "solicitudes_sin_detalles",
    "unidades_repetidas_en_solicitud",
    "relaciones_tenant_inconsistentes",
    "unidad_fuera_entrada",
    "costo_solicitud_distinto_compra",
    "estado_solicitud_detalle_incompatible",
    "estado_solicitud_unidad_incompatible",
)

POR_REVISAR = (
    "referencia_unidad_entre_solicitudes",
    "estado_historico_no_resoluble",
    "rechazo_sin_perdida_candidata",
    "perdida_candidata_sin_vinculo",
    "multiples_perdidas_candidatas",
    "ausencia_no_verificable_por_rls",
)

TABLAS_RELEVANTES = (
    "SolicitudDevolucion",
    "DetalleSolicitudDevolucion",
    "Inventario",
    "DetalleEntradaInventario",
    "EntradaInventario",
    "Perdida",
    "DetallePerdida",
)


class Command(BaseCommand):
    help = (
        "Diagnostica inconsistencias históricas de devoluciones sin modificar "
        "registros ni generar auditoría."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--miscelanea",
            type=int,
            dest="miscelanea_id",
            help="Limita el diagnóstico al IdMiscelanea indicado.",
        )

    def handle(self, *args, **options):
        miscelanea_id = options.get("miscelanea_id")
        limitadas_rls = set(self._rls_limited_tables())
        confirmadas = {categoria: set() for categoria in CONFIRMADAS}
        revision = {categoria: set() for categoria in POR_REVISAR}

        solicitudes = self._solicitudes(miscelanea_id)
        perdidas = self._perdidas_rechazo(miscelanea_id)
        perdidas_por_tenant = defaultdict(list)
        for perdida in perdidas:
            unidades = frozenset(
                detalle.IdInventario_id
                for detalle in perdida._detalles_diagnostico
            )
            perdidas_por_tenant[perdida.Miscelanea_id].append(
                (perdida.pk, unidades)
            )

        solicitudes_por_unidad = defaultdict(set)
        for solicitud in solicitudes:
            detalles = solicitud._detalles_diagnostico
            sid = solicitud.pk
            tenant_id = solicitud.Miscelanea_id
            if not detalles:
                evidencia = f"solicitud={sid}"
                if "DetalleSolicitudDevolucion" in limitadas_rls:
                    revision["ausencia_no_verificable_por_rls"].add(evidencia)
                else:
                    confirmadas["solicitudes_sin_detalles"].add(evidencia)
                continue

            ids_unidades = [detalle.IdInventario_id for detalle in detalles]
            repetidas = sorted(
                unidad_id
                for unidad_id in set(ids_unidades)
                if ids_unidades.count(unidad_id) > 1
            )
            if repetidas:
                for inventario_id in repetidas:
                    detalles_ids = sorted(
                        detalle.pk
                        for detalle in detalles
                        if detalle.IdInventario_id == inventario_id
                    )
                    confirmadas["unidades_repetidas_en_solicitud"].add(
                        f"solicitud={sid};inventario={inventario_id};"
                        f"detalles={self._ids(detalles_ids)}"
                    )

            if solicitud.Estado == "ParcialmenteAceptada":
                revision["estado_historico_no_resoluble"].add(
                    f"solicitud={sid};estado=ParcialmenteAceptada"
                )

            estado_detalle_esperado = {
                "Pendiente": "Pendiente",
                "Aceptada": "Aceptado",
                "Rechazada": "Rechazado",
            }.get(solicitud.Estado)
            estado_unidad_esperado = {
                "Pendiente": "Devuelto",
                "Aceptada": "Devuelto",
                "Rechazada": "Perdido",
            }.get(solicitud.Estado)

            if (
                solicitud.IdEntradaInventario.Miscelanea_id != tenant_id
                or solicitud.IdUsuario.Miscelanea_id != tenant_id
                or solicitud.IdEntradaInventario.IdProveedor.Miscelanea_id
                != tenant_id
            ):
                confirmadas["relaciones_tenant_inconsistentes"].add(
                    f"solicitud={sid}"
                )

            for detalle in detalles:
                did = detalle.pk
                inventario = detalle.IdInventario
                detalle_compra = inventario.IdDetalleEntrada
                entrada_compra = detalle_compra.IdEntradaInventario
                producto = detalle_compra.IdProducto
                evidencia = (
                    f"solicitud={sid};detalle={did};inventario={inventario.pk}"
                )
                solicitudes_por_unidad[inventario.pk].add(sid)

                if (
                    detalle.Miscelanea_id != tenant_id
                    or inventario.Miscelanea_id != tenant_id
                    or detalle_compra.Miscelanea_id != tenant_id
                    or entrada_compra.Miscelanea_id != tenant_id
                    or producto.Miscelanea_id != tenant_id
                ):
                    confirmadas["relaciones_tenant_inconsistentes"].add(evidencia)
                if entrada_compra.pk != solicitud.IdEntradaInventario_id:
                    confirmadas["unidad_fuera_entrada"].add(
                        f"{evidencia};entrada_solicitud={solicitud.IdEntradaInventario_id};"
                        f"entrada_unidad={entrada_compra.pk}"
                    )
                if detalle.PrecioCompraUnitario != detalle_compra.PrecioCompraUnitario:
                    confirmadas["costo_solicitud_distinto_compra"].add(evidencia)
                if (
                    estado_detalle_esperado is not None
                    and detalle.EstadoItem != estado_detalle_esperado
                ):
                    confirmadas["estado_solicitud_detalle_incompatible"].add(
                        evidencia
                    )
                if (
                    estado_unidad_esperado is not None
                    and inventario.Estado != estado_unidad_esperado
                ):
                    confirmadas["estado_solicitud_unidad_incompatible"].add(
                        evidencia
                    )

            if solicitud.Estado == "Rechazada":
                candidatos = sorted(
                    perdida_id
                    for perdida_id, unidades in perdidas_por_tenant[tenant_id]
                    if unidades == frozenset(ids_unidades)
                )
                evidencia = f"solicitud={sid}"
                if not candidatos:
                    if "DetallePerdida" in limitadas_rls:
                        revision["ausencia_no_verificable_por_rls"].add(evidencia)
                    else:
                        revision["rechazo_sin_perdida_candidata"].add(evidencia)
                elif len(candidatos) == 1:
                    revision["perdida_candidata_sin_vinculo"].add(
                        f"{evidencia};perdida={candidatos[0]}"
                    )
                else:
                    revision["multiples_perdidas_candidatas"].add(
                        f"{evidencia};perdidas={self._ids(candidatos)}"
                    )

        for inventario_id, solicitudes_ids in solicitudes_por_unidad.items():
            if len(solicitudes_ids) > 1:
                revision["referencia_unidad_entre_solicitudes"].add(
                    f"inventario={inventario_id};solicitudes="
                    f"{self._ids(sorted(solicitudes_ids))}"
                )

        alcance = (
            f"miscelanea={miscelanea_id}"
            if miscelanea_id is not None
            else "miscelanea=todas_las_visibles"
        )
        self.stdout.write(f"ALCANCE\t{alcance}")
        if limitadas_rls:
            self.stdout.write(
                "VISIBILIDAD\tno verificable globalmente por RLS; "
                f"tablas={self._ids(sorted(limitadas_rls))}; "
                "cero hallazgos no demuestra integridad"
            )
        else:
            self.stdout.write(
                "VISIBILIDAD\tconsulta global permitida por los privilegios del rol "
                "actual; el resultado solo cubre las filas visibles"
            )

        for clasificacion, categorias in (
            ("CONFIRMADA", confirmadas),
            ("REVISION", revision),
        ):
            for categoria, evidencias in categorias.items():
                ordenadas = sorted(evidencias)
                detalle = " | ".join(ordenadas) if ordenadas else "-"
                self.stdout.write(
                    f"{clasificacion}\t{categoria}\t{len(ordenadas)}\t{detalle}"
                )

        total_confirmadas = sum(len(items) for items in confirmadas.values())
        total_revision = sum(len(items) for items in revision.values())
        self.stdout.write(
            f"RESUMEN\tconfirmadas={total_confirmadas}\tpor_revisar={total_revision}"
        )
        self.stdout.write(
            "LIMITES\tNo existe FK entre solicitud y pérdida; las coincidencias "
            "por unidades son candidatas para revisión, no relaciones demostradas. "
            "La ausencia de auditoría no se evalúa como corrupción."
        )

    @staticmethod
    def _solicitudes(miscelanea_id):
        detalles = (
            DetalleSolicitudDevolucion.objects.select_related(
                "IdInventario__IdDetalleEntrada__IdEntradaInventario",
                "IdInventario__IdDetalleEntrada__IdProducto",
            )
            .order_by("IdDetalleSolicitudDevolucion")
        )
        queryset = (
            SolicitudDevolucion.objects.select_related(
                "IdEntradaInventario__IdProveedor",
                "IdUsuario",
            )
            .prefetch_related(
                Prefetch(
                    "detallesolicituddevolucion_set",
                    queryset=detalles,
                    to_attr="_detalles_diagnostico",
                )
            )
            .order_by("IdSolicitudDevolucion")
        )
        if miscelanea_id is not None:
            queryset = queryset.filter(Miscelanea_id=miscelanea_id)
        return list(queryset)

    @staticmethod
    def _perdidas_rechazo(miscelanea_id):
        detalles = DetallePerdida.objects.order_by("IdDetallePerdida")
        queryset = (
            Perdida.objects.filter(TipoPerdida="RechazoDevolucion")
            .prefetch_related(
                Prefetch(
                    "detalleperdida_set",
                    queryset=detalles,
                    to_attr="_detalles_diagnostico",
                )
            )
            .order_by("IdPerdida")
        )
        if miscelanea_id is not None:
            queryset = queryset.filter(Miscelanea_id=miscelanea_id)
        return list(queryset)

    @staticmethod
    def _ids(values):
        return ",".join(str(value) for value in values)

    @staticmethod
    def _rls_limited_tables():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.relname,
                    c.relrowsecurity,
                    c.relforcerowsecurity,
                    pg_get_userbyid(c.relowner) = current_user AS is_owner,
                    r.rolsuper,
                    r.rolbypassrls
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_roles r ON r.rolname = current_user
                WHERE n.nspname = current_schema() AND c.relname = ANY(%s)
                """,
                [list(TABLAS_RELEVANTES)],
            )
            rows = cursor.fetchall()
        return sorted(
            table
            for table, enabled, forced, is_owner, superuser, bypass in rows
            if enabled
            and not superuser
            and not bypass
            and (forced or not is_owner)
        )
