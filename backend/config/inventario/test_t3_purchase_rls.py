from decimal import Decimal
from unittest.mock import patch

from django.db import connection
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
from usuarios.test_utils import RLS_TEST_ROLE, RestrictedRLSRoleMixin
from ventas.models import DetalleVenta, Venta


RLS_PURCHASE_TABLES = (
    "EntradaInventario",
    "DetalleEntradaInventario",
    "Inventario",
)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ComprasRLSRealRoleTests(
    RestrictedRLSRoleMixin,
    APITransactionTestCase,
):
    """Valida compras e inventario bajo el rol restringido real."""

    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="RLS compras A", Codigo="RLS-COM-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="RLS compras B", Codigo="RLS-COM-B"
        )
        self.admin_a = self._user("rls-purchase-admin-a@example.test", self.tenant_a)
        self.admin_b = self._user("rls-purchase-admin-b@example.test", self.tenant_b)
        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoria RLS compras A",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Categoria RLS compras B",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor RLS compras A"
        )
        self.provider_a2 = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor RLS compras A2"
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor RLS compras B"
        )
        self.product_a = self._product(
            self.tenant_a, self.category_a, self.provider_a, "Producto RLS compras A"
        )
        self.product_a2 = self._product(
            self.tenant_a, self.category_a, self.provider_a2, "Producto RLS compras A2"
        )
        self.product_b = self._product(
            self.tenant_b, self.category_b, self.provider_b, "Producto RLS compras B"
        )
        self.entry_a, self.detail_a = self._entry(
            self.tenant_a, self.admin_a, self.provider_a, self.product_a,
            quantity=2, cost="5.00",
        )
        self.entry_b, self.detail_b = self._entry(
            self.tenant_b, self.admin_b, self.provider_b, self.product_b,
            quantity=2, cost="50.00",
        )
        self.unit_a = self.detail_a.inventarios.order_by("IdInventario").first()
        self.unit_b = self.detail_b.inventarios.order_by("IdInventario").first()
        self._assert_role_is_prepared()

    @staticmethod
    def _user(email, tenant):
        return Usuario.objects.create_user(
            Email=email,
            Password="A-strong-test-password-2026!",
            Nombre=email.split("@")[0],
            Miscelanea=tenant,
            Rol="admin",
            Estado="Activo",
        )

    @staticmethod
    def _product(tenant, category, provider, name):
        return Producto.objects.create(
            Miscelanea=tenant,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre=name,
        )

    @staticmethod
    def _entry(tenant, user, provider, product, *, quantity=2, cost="5.00"):
        unit_cost = Decimal(cost)
        entry = EntradaInventario.objects.create(
            Miscelanea=tenant,
            IdProveedor=provider,
            IdUsuario=user,
            FechaEntrada=timezone.now(),
            Total=unit_cost * quantity,
        )
        detail = DetalleEntradaInventario.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=quantity,
            PrecioCompraUnitario=unit_cost,
        )
        return entry, detail

    @staticmethod
    def _detail_payload(detail, **changes):
        payload = {
            "id": detail.pk,
            "productoId": detail.IdProducto_id,
            "tipoCompra": detail.TipoCompra,
            "cantidad": detail.Cantidad,
            "precioCompraUnitario": str(detail.PrecioCompraUnitario),
        }
        payload.update(changes)
        return payload

    @staticmethod
    def _snapshot(entry):
        header = list(
            EntradaInventario.objects.filter(pk=entry.pk).values_list(
                "IdEntradaInventario", "IdProveedor_id", "IdUsuario_id",
                "FechaEntrada", "Total", "Miscelanea_id",
            )
        )
        details = list(
            DetalleEntradaInventario.objects.filter(IdEntradaInventario_id=entry.pk)
            .order_by("IdDetalleEntrada")
            .values_list(
                "IdDetalleEntrada", "IdProducto_id", "TipoCompra", "Cantidad",
                "PrecioCompraUnitario", "Miscelanea_id",
            )
        )
        units = list(
            Inventario.objects.filter(
                IdDetalleEntrada__IdEntradaInventario_id=entry.pk
            )
            .order_by("IdInventario")
            .values_list(
                "IdInventario", "IdDetalleEntrada_id", "Estado",
                "FechaMovimiento", "Miscelanea_id",
            )
        )
        return header, details, units

    def test_direct_queries_prove_purchase_rls_for_restricted_role(self):
        self._enter_restricted_role()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user, r.rolsuper, r.rolbypassrls,
                       pg_get_userbyid(c.relowner) = current_user,
                       c.relrowsecurity, c.relforcerowsecurity
                FROM pg_roles r
                CROSS JOIN pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE r.rolname = current_user
                  AND n.nspname = current_schema()
                  AND c.relname = 'EntradaInventario'
                """
            )
            self.assertEqual(
                cursor.fetchone(),
                (RLS_TEST_ROLE, False, False, False, True, True),
            )
            cursor.execute(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema() AND c.relname = ANY(%s)
                ORDER BY c.relname
                """,
                [list(RLS_PURCHASE_TABLES)],
            )
            self.assertEqual(
                cursor.fetchall(),
                sorted((table, True, True) for table in RLS_PURCHASE_TABLES),
            )

        self.assertIsNone(self._raw_context())
        for empty_context in (None, ""):
            if empty_context == "":
                self._set_context("")
            with connection.cursor() as cursor:
                for table in RLS_PURCHASE_TABLES:
                    cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                    self.assertEqual(cursor.fetchone()[0], 0, table)

        self._set_context(self.tenant_a.pk)
        with connection.cursor() as cursor:
            expected = (
                ("EntradaInventario", "IdEntradaInventario", self.entry_a.pk, self.entry_b.pk),
                ("DetalleEntradaInventario", "IdDetalleEntrada", self.detail_a.pk, self.detail_b.pk),
                (
                    "Inventario",
                    "IdInventario",
                    self.unit_a.pk,
                    self.unit_b.pk,
                ),
            )
            for table, column, own_id, foreign_id in expected:
                cursor.execute(f'SELECT "{column}" FROM "{table}" ORDER BY "{column}"')
                visible_ids = {row[0] for row in cursor.fetchall()}
                self.assertIn(own_id, visible_ids)
                self.assertNotIn(foreign_id, visible_ids)
                cursor.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = %s',
                    [foreign_id],
                )
                self.assertEqual(cursor.fetchone()[0], 0, table)

    def test_http_reads_are_isolated_and_generic_writes_remain_closed(self):
        unit_a = self.detail_a.inventarios.order_by("IdInventario").first()
        unit_b = self.detail_b.inventarios.order_by("IdInventario").first()
        before_a = self._snapshot(self.entry_a)
        before_b = self._snapshot(self.entry_b)
        self._enter_restricted_role()
        client = self._client(self.admin_a)

        reads = (
            (f"/api/inventario/entradas/{self.entry_a.pk}/", 200),
            (f"/api/inventario/entradas/{self.entry_b.pk}/", 404),
            (f"/api/inventario/detalles-entrada/{self.detail_a.pk}/", 200),
            (f"/api/inventario/detalles-entrada/{self.detail_b.pk}/", 404),
            (f"/api/inventario/inventarios/{unit_a.pk}/", 200),
            (f"/api/inventario/inventarios/{unit_b.pk}/", 404),
        )
        for url, expected_status in reads:
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, expected_status, response.data)
                self.assertEqual(self._context(), "")

        closed = (
            ("post", "/api/inventario/entradas/", {}),
            ("patch", f"/api/inventario/entradas/{self.entry_a.pk}/", {}),
            ("delete", f"/api/inventario/entradas/{self.entry_a.pk}/", None),
            ("post", "/api/inventario/detalles-entrada/", {}),
            ("patch", f"/api/inventario/detalles-entrada/{self.detail_a.pk}/", {}),
            ("delete", f"/api/inventario/inventarios/{unit_a.pk}/", None),
        )
        for method, url, payload in closed:
            with self.subTest(method=method, url=url):
                response = getattr(client, method)(url, payload, format="json")
                self.assertEqual(response.status_code, 405, response.data)
                self.assertEqual(self._context(), "")

        self._leave_restricted_role()
        self.assertEqual(self._snapshot(self.entry_a), before_a)
        self.assertEqual(self._snapshot(self.entry_b), before_b)

    def test_create_complete_sets_server_identity_tenant_details_and_units(self):
        self._enter_restricted_role()
        response = self._client(self.admin_a).post(
            "/api/inventario/entradas/crear-completa/",
            {
                "proveedorId": self.provider_a.pk,
                "usuarioId": self.admin_b.pk,
                "total": "0.01",
                "miscelaneaId": self.tenant_b.pk,
                "detalles": [
                    {
                        "productoId": self.product_a.pk,
                        "tipoCompra": "unidad",
                        "cantidad": 2,
                        "precioCompraUnitario": "10.25",
                    },
                    {
                        "productoId": self.product_a2.pk,
                        "tipoCompra": "pack",
                        "cantidad": 3,
                        "precioCompraUnitario": "4.10",
                    },
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self._context(), "")
        self._set_context(self.tenant_a.pk)
        entry = EntradaInventario.objects.get(pk=response.json()["id"])
        details = list(
            entry.detalleentradainventario_set.order_by("IdDetalleEntrada")
        )
        units = Inventario.objects.filter(IdDetalleEntrada__in=details)
        self.assertEqual(entry.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(entry.IdUsuario_id, self.admin_a.pk)
        self.assertEqual(entry.Total, Decimal("32.80"))
        self.assertEqual([detail.Cantidad for detail in details], [2, 3])
        self.assertEqual(
            {detail.Miscelanea_id for detail in details}, {self.tenant_a.pk}
        )
        self.assertEqual(units.count(), 5)
        self.assertEqual(set(units.values_list("Estado", flat=True)), {"Disponible"})
        self.assertEqual(
            set(units.values_list("Miscelanea_id", flat=True)), {self.tenant_a.pk}
        )

    def test_foreign_provider_and_product_are_rejected_without_partial_writes(self):
        before_a = self._snapshot(self.entry_a)
        before_b = self._snapshot(self.entry_b)
        counts = (
            EntradaInventario.objects.count(),
            DetalleEntradaInventario.objects.count(),
            Inventario.objects.count(),
        )
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        payloads = (
            {
                "proveedorId": self.provider_b.pk,
                "detalles": [{
                    "productoId": self.product_a.pk,
                    "tipoCompra": "unidad",
                    "cantidad": 1,
                    "precioCompraUnitario": "1.00",
                }],
            },
            {
                "proveedorId": self.provider_a.pk,
                "detalles": [{
                    "productoId": self.product_b.pk,
                    "tipoCompra": "unidad",
                    "cantidad": 1,
                    "precioCompraUnitario": "1.00",
                }],
            },
        )
        for payload in payloads:
            response = client.post(
                "/api/inventario/entradas/crear-completa/", payload, format="json"
            )
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(self._context(), "")

        self._leave_restricted_role()
        self.assertEqual(
            (
                EntradaInventario.objects.count(),
                DetalleEntradaInventario.objects.count(),
                Inventario.objects.count(),
            ),
            counts,
        )
        self.assertEqual(self._snapshot(self.entry_a), before_a)
        self.assertEqual(self._snapshot(self.entry_b), before_b)

    def test_edit_complete_updates_own_purchase_and_preserves_identity(self):
        original_unit_ids = list(
            self.detail_a.inventarios.order_by("IdInventario").values_list(
                "IdInventario", flat=True
            )
        )
        self._enter_restricted_role()
        response = self._client(self.admin_a).put(
            f"/api/inventario/entradas/{self.entry_a.pk}/editar-completa/",
            {
                "proveedorId": self.provider_a2.pk,
                "detalles": [
                    self._detail_payload(
                        self.detail_a,
                        tipoCompra="caja",
                        cantidad=3,
                        precioCompraUnitario="6.25",
                    )
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._context(), "")
        self._set_context(self.tenant_a.pk)
        self.entry_a.refresh_from_db()
        self.detail_a.refresh_from_db()
        unit_ids = list(
            self.detail_a.inventarios.order_by("IdInventario").values_list(
                "IdInventario", flat=True
            )
        )
        self.assertEqual(self.entry_a.IdUsuario_id, self.admin_a.pk)
        self.assertEqual(self.entry_a.IdProveedor_id, self.provider_a2.pk)
        self.assertEqual(self.entry_a.Total, Decimal("18.75"))
        self.assertEqual(self.detail_a.TipoCompra, "caja")
        self.assertEqual(self.detail_a.Cantidad, 3)
        self.assertEqual(self.detail_a.PrecioCompraUnitario, Decimal("6.25"))
        self.assertEqual(unit_ids[:2], original_unit_ids)
        self.assertEqual(len(unit_ids), 3)

    def test_foreign_edit_and_delete_return_404_without_changes(self):
        before = self._snapshot(self.entry_b)
        self._enter_restricted_role()
        client = self._client(self.admin_a)

        edit = client.put(
            f"/api/inventario/entradas/{self.entry_b.pk}/editar-completa/",
            {
                "proveedorId": self.provider_a.pk,
                "detalles": [{
                    "id": self.detail_b.pk,
                    "productoId": self.product_a.pk,
                    "tipoCompra": "unidad",
                    "cantidad": 2,
                    "precioCompraUnitario": "5.00",
                }],
            },
            format="json",
        )
        delete = client.delete(
            f"/api/inventario/entradas/{self.entry_b.pk}/delete_completo/"
        )

        self.assertEqual(edit.status_code, 404, edit.data)
        self.assertEqual(delete.status_code, 404, delete.data)
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertEqual(self._snapshot(self.entry_b), before)

    def test_states_and_history_still_block_edit_and_delete(self):
        cases = []
        for state in ("Vendido", "Perdido", "Devuelto"):
            entry, detail = self._entry(
                self.tenant_a, self.admin_a, self.provider_a, self.product_a
            )
            unit = detail.inventarios.first()
            unit.Estado = state
            unit.save(update_fields=["Estado"])
            cases.append((state, entry, detail))

        sale_entry, sale_detail = self._entry(
            self.tenant_a, self.admin_a, self.provider_a, self.product_a,
            quantity=1,
        )
        sale_unit = sale_detail.inventarios.get()
        sale = Venta.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.admin_a,
            Fecha=timezone.now(),
            Total=Decimal("6.00"),
            Estado="Anulada",
        )
        DetalleVenta.objects.create(
            Miscelanea=self.tenant_a,
            IdVenta=sale,
            IdInventario=sale_unit,
            PrecioVentaUnitario=Decimal("6.00"),
        )
        cases.append(("venta anulada", sale_entry, sale_detail))

        loss_entry, loss_detail = self._entry(
            self.tenant_a, self.admin_a, self.provider_a, self.product_a,
            quantity=1,
        )
        loss_unit = loss_detail.inventarios.get()
        loss = Perdida.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.admin_a,
            TipoPerdida="Otra",
            Fecha=timezone.now(),
            Total=Decimal("5.00"),
        )
        DetallePerdida.objects.create(
            Miscelanea=self.tenant_a,
            IdPerdida=loss,
            IdInventario=loss_unit,
            PrecioCompraUnitario=Decimal("5.00"),
        )
        cases.append(("pérdida", loss_entry, loss_detail))

        return_entry, return_detail = self._entry(
            self.tenant_a, self.admin_a, self.provider_a, self.product_a,
            quantity=1,
        )
        return_unit = return_detail.inventarios.get()
        request_record = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=return_entry,
            IdUsuario=self.admin_a,
            Estado="Pendiente",
            Fecha=timezone.now(),
        )
        DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdSolicitudDevolucion=request_record,
            IdInventario=return_unit,
            PrecioCompraUnitario=Decimal("5.00"),
            EstadoItem="Pendiente",
        )
        cases.append(("devolución", return_entry, return_detail))

        snapshots = {entry.pk: self._snapshot(entry) for _, entry, _ in cases}
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        for label, entry, detail in cases:
            with self.subTest(label=label, operation="edit"):
                edit = client.put(
                    f"/api/inventario/entradas/{entry.pk}/editar-completa/",
                    {
                        "proveedorId": self.provider_a.pk,
                        "detalles": [self._detail_payload(
                            detail, precioCompraUnitario="6.00"
                        )],
                    },
                    format="json",
                )
                self.assertEqual(edit.status_code, 400, edit.data)
                self.assertEqual(self._context(), "")
            with self.subTest(label=label, operation="delete"):
                delete = client.delete(
                    f"/api/inventario/entradas/{entry.pk}/delete_completo/"
                )
                self.assertEqual(delete.status_code, 400, delete.data)
                self.assertEqual(self._context(), "")

        self._leave_restricted_role()
        for _, entry, _ in cases:
            self.assertEqual(self._snapshot(entry), snapshots[entry.pk])

    def test_delete_complete_removes_only_eligible_own_purchase(self):
        own = self._snapshot(self.entry_a)
        foreign = self._snapshot(self.entry_b)
        self._enter_restricted_role()

        response = self._client(self.admin_a).delete(
            f"/api/inventario/entradas/{self.entry_a.pk}/delete_completo/"
        )

        self.assertEqual(response.status_code, 204, response.data)
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertFalse(EntradaInventario.objects.filter(pk=self.entry_a.pk).exists())
        self.assertFalse(
            DetalleEntradaInventario.objects.filter(
                IdEntradaInventario_id=self.entry_a.pk
            ).exists()
        )
        self.assertFalse(
            Inventario.objects.filter(
                IdDetalleEntrada_id__in=[row[0] for row in own[1]]
            ).exists()
        )
        self.assertEqual(self._snapshot(self.entry_b), foreign)

    def test_controlled_failures_roll_back_edit_and_delete_writes(self):
        edit_before = self._snapshot(self.entry_a)
        save_calls = 0

        def fail_header_save(instance, *args, **kwargs):
            nonlocal save_calls
            save_calls += 1
            raise RuntimeError("fallo controlado RLS edición")

        self._enter_restricted_role()
        with patch(
            "inventario.views.EntradaInventario.save",
            autospec=True,
            side_effect=fail_header_save,
        ):
            edit = self._client(self.admin_a).put(
                f"/api/inventario/entradas/{self.entry_a.pk}/editar-completa/",
                {
                    "proveedorId": self.provider_a.pk,
                    "detalles": [self._detail_payload(
                        self.detail_a,
                        cantidad=3,
                        precioCompraUnitario="6.00",
                    )],
                },
                format="json",
            )

        self.assertEqual(edit.status_code, 400, edit.data)
        self.assertEqual(save_calls, 1)
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertEqual(self._snapshot(self.entry_a), edit_before)

        delete_entry, _ = self._entry(
            self.tenant_a, self.admin_a, self.provider_a, self.product_a
        )
        delete_before = self._snapshot(delete_entry)
        delete_calls = 0

        def fail_header_delete(instance, *args, **kwargs):
            nonlocal delete_calls
            delete_calls += 1
            raise RuntimeError("fallo controlado RLS eliminación")

        self._enter_restricted_role()
        with patch.object(
            EntradaInventario,
            "delete",
            autospec=True,
            side_effect=fail_header_delete,
        ):
            delete = self._client(self.admin_a).delete(
                f"/api/inventario/entradas/{delete_entry.pk}/delete_completo/"
            )

        self.assertEqual(delete.status_code, 400, delete.data)
        self.assertEqual(delete_calls, 1)
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertEqual(self._snapshot(delete_entry), delete_before)
