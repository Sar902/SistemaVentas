from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from catalogo.models import Categoria, Producto, Proveedor
from inventario.models import (
    DetalleEntradaInventario,
    DetallePerdida,
    DetalleSolicitudDevolucion,
    EntradaInventario,
    Inventario,
    Perdida,
    SolicitudDevolucion,
)
from usuarios.models import Miscelanea, Usuario
from usuarios.test_utils import TemporaryRLSNeutralizationMixin


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DiagnosticarDevolucionesTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Diagnostico devoluciones A", Codigo="DIAG-DEV-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Diagnostico devoluciones B", Codigo="DIAG-DEV-B"
        )
        self.data_a = self._domain(self.tenant_a, "a")
        self.data_b = self._domain(self.tenant_b, "b")

    @staticmethod
    def _domain(tenant, suffix):
        user = Usuario.objects.create_user(
            Email=f"diagnostico-{suffix}@example.test",
            Password="A-strong-test-password-2026!",
            Nombre=f"Persona sensible {suffix}",
            Miscelanea=tenant,
            Rol="admin",
            Estado="Activo",
        )
        category = Categoria.objects.create(
            Miscelanea=tenant,
            Nombre=f"Categoria diagnostico {suffix}",
            PorcentajeGanancia=Decimal("20.00"),
        )
        provider = Proveedor.objects.create(
            Miscelanea=tenant, Nombre=f"Proveedor diagnostico {suffix}"
        )
        product = Producto.objects.create(
            Miscelanea=tenant,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre=f"Producto diagnostico {suffix}",
        )
        entries = []
        details = []
        units = []
        for index, cost in enumerate((Decimal("10.00"), Decimal("13.00")), 1):
            entry = EntradaInventario.objects.create(
                Miscelanea=tenant,
                IdProveedor=provider,
                IdUsuario=user,
                FechaEntrada=timezone.now(),
                Total=cost * 8,
            )
            detail = DetalleEntradaInventario.objects.create(
                Miscelanea=tenant,
                IdEntradaInventario=entry,
                IdProducto=product,
                TipoCompra=f"unidad-{index}",
                Cantidad=8,
                PrecioCompraUnitario=cost,
            )
            entries.append(entry)
            details.append(detail)
            units.extend(detail.inventarios.order_by("IdInventario"))
        return {
            "user": user,
            "entries": entries,
            "details": details,
            "units": units,
        }

    def _request(
        self,
        data,
        unit=None,
        *,
        state="Pendiente",
        item_state="Pendiente",
        observations=None,
        cost=None,
        entry_index=0,
    ):
        request_record = SolicitudDevolucion.objects.create(
            Miscelanea=data["user"].Miscelanea,
            IdEntradaInventario=data["entries"][entry_index],
            IdUsuario=data["user"],
            Estado=state,
            Observaciones=observations,
            Fecha=timezone.now(),
        )
        detail = None
        if unit is not None:
            detail = DetalleSolicitudDevolucion.objects.create(
                Miscelanea=data["user"].Miscelanea,
                IdSolicitudDevolucion=request_record,
                IdInventario=unit,
                PrecioCompraUnitario=(
                    cost
                    if cost is not None
                    else unit.IdDetalleEntrada.PrecioCompraUnitario
                ),
                EstadoItem=item_state,
            )
        return request_record, detail

    @staticmethod
    def _run(*args):
        output = StringIO()
        call_command("diagnosticar_devoluciones", *args, stdout=output)
        return output.getvalue()

    @staticmethod
    def _snapshot():
        models = (
            SolicitudDevolucion,
            DetalleSolicitudDevolucion,
            Inventario,
            Perdida,
            DetallePerdida,
        )
        return {
            model._meta.label_lower: list(
                model.objects.order_by(model._meta.pk.name).values_list()
            )
            for model in models
        }

    def test_coherent_data_has_no_false_positives_and_command_is_read_only(self):
        unit = self.data_a["units"][0]
        unit.Estado = "Devuelto"
        unit.save(update_fields=["Estado"])
        request_record, _ = self._request(
            self.data_a,
            unit,
            state="Aceptada",
            item_state="Aceptado",
            observations="Texto privado que no debe mostrarse",
        )
        before = self._snapshot()

        output = self._run("--miscelanea", str(self.tenant_a.pk))

        self.assertIn("RESUMEN\tconfirmadas=0\tpor_revisar=0", output)
        self.assertIn("VISIBILIDAD", output)
        self.assertNotIn("Texto privado", output)
        self.assertNotIn(self.data_a["user"].Email, output)
        self.assertEqual(before, self._snapshot())
        self.assertIn(f"miscelanea={self.tenant_a.pk}", output)
        self.assertNotIn(f"solicitud={request_record.pk}\tdetalle=", output)

    def test_reports_every_demonstrable_category_with_only_identifiers(self):
        empty, _ = self._request(self.data_a)

        duplicate_unit = self.data_a["units"][1]
        duplicate_unit.Estado = "Devuelto"
        duplicate_unit.save(update_fields=["Estado"])
        duplicate_request, first_duplicate = self._request(
            self.data_a, duplicate_unit
        )
        second_duplicate = DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdSolicitudDevolucion=duplicate_request,
            IdInventario=duplicate_unit,
            PrecioCompraUnitario=duplicate_unit.IdDetalleEntrada.PrecioCompraUnitario,
            EstadoItem="Pendiente",
        )

        tenant_unit = self.data_a["units"][2]
        tenant_unit.Estado = "Devuelto"
        tenant_unit.save(update_fields=["Estado"])
        tenant_request, tenant_detail = self._request(self.data_a, tenant_unit)
        DetalleSolicitudDevolucion.objects.filter(pk=tenant_detail.pk).update(
            Miscelanea=self.tenant_b
        )

        outside_unit = self.data_a["units"][8]
        outside_unit.Estado = "Devuelto"
        outside_unit.save(update_fields=["Estado"])
        outside_request, outside_detail = self._request(self.data_a, outside_unit)

        cost_unit = self.data_a["units"][3]
        cost_unit.Estado = "Devuelto"
        cost_unit.save(update_fields=["Estado"])
        cost_request, cost_detail = self._request(
            self.data_a, cost_unit, cost=Decimal("99.00")
        )

        state_unit = self.data_a["units"][4]
        state_request, state_detail = self._request(
            self.data_a,
            state_unit,
            state="Aceptada",
            item_state="Pendiente",
        )
        before = self._snapshot()

        output = self._run("--miscelanea", str(self.tenant_a.pk))

        expected = {
            "solicitudes_sin_detalles": empty.pk,
            "unidades_repetidas_en_solicitud": duplicate_request.pk,
            "relaciones_tenant_inconsistentes": tenant_request.pk,
            "unidad_fuera_entrada": outside_request.pk,
            "costo_solicitud_distinto_compra": cost_request.pk,
            "estado_solicitud_detalle_incompatible": state_request.pk,
            "estado_solicitud_unidad_incompatible": state_request.pk,
        }
        for category, request_id in expected.items():
            with self.subTest(category=category):
                self.assertIn(f"CONFIRMADA\t{category}\t", output)
                self.assertIn(f"solicitud={request_id}", output)
        for identifier in (
            tenant_detail.pk,
            outside_detail.pk,
            cost_detail.pk,
            state_detail.pk,
        ):
            self.assertIn(f"detalle={identifier}", output)
        self.assertIn(
            f"detalles={first_duplicate.pk},{second_duplicate.pk}", output
        )
        self.assertNotIn(self.data_a["user"].Email, output)
        self.assertNotIn(self.data_a["user"].Nombre, output)
        self.assertEqual(before, self._snapshot())

    def test_ambiguous_references_and_loss_correspondence_are_for_review(self):
        shared_unit = self.data_a["units"][5]
        shared_unit.Estado = "Devuelto"
        shared_unit.save(update_fields=["Estado"])
        first, _ = self._request(self.data_a, shared_unit)
        second, _ = self._request(self.data_a, shared_unit)

        no_loss_unit = self.data_a["units"][6]
        no_loss_unit.Estado = "Perdido"
        no_loss_unit.save(update_fields=["Estado"])
        no_loss, _ = self._request(
            self.data_a,
            no_loss_unit,
            state="Rechazada",
            item_state="Rechazado",
        )

        candidate_unit = self.data_a["units"][7]
        candidate_unit.Estado = "Perdido"
        candidate_unit.save(update_fields=["Estado"])
        candidate, _ = self._request(
            self.data_a,
            candidate_unit,
            state="Rechazada",
            item_state="Rechazado",
        )
        loss = Perdida.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.data_a["user"],
            TipoPerdida="RechazoDevolucion",
            Fecha=timezone.now(),
            Total=candidate_unit.IdDetalleEntrada.PrecioCompraUnitario,
        )
        DetallePerdida.objects.create(
            Miscelanea=self.tenant_a,
            IdPerdida=loss,
            IdInventario=candidate_unit,
            PrecioCompraUnitario=candidate_unit.IdDetalleEntrada.PrecioCompraUnitario,
        )

        partial_unit = self.data_a["units"][9]
        partial_unit.Estado = "Devuelto"
        partial_unit.save(update_fields=["Estado"])
        partial, _ = self._request(
            self.data_a,
            partial_unit,
            state="ParcialmenteAceptada",
            item_state="Aceptado",
            entry_index=1,
        )

        multiple_unit = self.data_a["units"][10]
        multiple_unit.Estado = "Perdido"
        multiple_unit.save(update_fields=["Estado"])
        multiple, _ = self._request(
            self.data_a,
            multiple_unit,
            state="Rechazada",
            item_state="Rechazado",
            entry_index=1,
        )
        multiple_losses = []
        for _ in range(2):
            repeated_loss = Perdida.objects.create(
                Miscelanea=self.tenant_a,
                IdUsuario=self.data_a["user"],
                TipoPerdida="RechazoDevolucion",
                Fecha=timezone.now(),
                Total=multiple_unit.IdDetalleEntrada.PrecioCompraUnitario,
            )
            DetallePerdida.objects.create(
                Miscelanea=self.tenant_a,
                IdPerdida=repeated_loss,
                IdInventario=multiple_unit,
                PrecioCompraUnitario=(
                    multiple_unit.IdDetalleEntrada.PrecioCompraUnitario
                ),
            )
            multiple_losses.append(repeated_loss.pk)

        output = self._run("--miscelanea", str(self.tenant_a.pk))

        self.assertIn("REVISION\treferencia_unidad_entre_solicitudes", output)
        self.assertIn(f"solicitudes={first.pk},{second.pk}", output)
        self.assertIn("REVISION\trechazo_sin_perdida_candidata", output)
        self.assertIn(f"solicitud={no_loss.pk}", output)
        self.assertIn("REVISION\tperdida_candidata_sin_vinculo", output)
        self.assertIn(
            f"solicitud={candidate.pk};perdida={loss.pk}", output
        )
        self.assertIn("REVISION\testado_historico_no_resoluble", output)
        self.assertIn(f"solicitud={partial.pk}", output)
        self.assertIn("REVISION\tmultiples_perdidas_candidatas", output)
        self.assertIn(
            f"solicitud={multiple.pk};perdidas="
            f"{multiple_losses[0]},{multiple_losses[1]}",
            output,
        )
        self.assertNotIn("CONFIRMADA\tperdida_candidata_sin_vinculo", output)

    def test_filter_limits_findings_to_the_requested_miscelanea(self):
        request_a, _ = self._request(self.data_a)
        request_b, _ = self._request(self.data_b)

        output = self._run("--miscelanea", str(self.tenant_a.pk))

        self.assertIn(f"solicitud={request_a.pk}", output)
        self.assertNotIn(f"solicitud={request_b.pk}", output)

    def test_output_warns_when_global_visibility_is_limited_by_rls(self):
        output = StringIO()
        with patch(
            "inventario.management.commands.diagnosticar_devoluciones.Command."
            "_rls_limited_tables",
            return_value=["SolicitudDevolucion", "DetalleSolicitudDevolucion"],
        ), redirect_stdout(output):
            call_command("diagnosticar_devoluciones", stdout=output)

        text = output.getvalue()
        self.assertIn("no verificable globalmente por RLS", text)
        self.assertIn("cero hallazgos no demuestra integridad", text.lower())
