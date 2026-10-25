from datetime import timedelta
from decimal import Decimal
from threading import Event, Lock, Thread
from time import monotonic
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.db import close_old_connections, connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from auditoria.models import LogAuditoria
from auditoria.mixins import registrar_evento_manual as real_audit_event
from catalogo.models import Categoria, Producto, Proveedor
from inventario.models import (
    DetallePerdida,
    DetalleSolicitudDevolucion,
    DetalleEntradaInventario,
    EntradaInventario,
    Inventario,
    Perdida,
    SolicitudDevolucion,
)
from usuarios.models import DelegacionAdmin, Miscelanea, Usuario
from usuarios.test_utils import TemporaryRLSNeutralizationMixin
from ventas.models import DetalleVenta, Venta


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CrearCompraCompletaTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    url = "/api/inventario/entradas/crear-completa/"

    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Compras A", Codigo="COMPRAS-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Compras B", Codigo="COMPRAS-B"
        )
        self.admin_a = self._user("admin-compras-a@example.test", self.tenant_a, "admin")
        self.cashier_a = self._user(
            "cajero-compras-a@example.test", self.tenant_a, "cajero"
        )
        self.admin_b = self._user("admin-compras-b@example.test", self.tenant_b, "admin")
        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoria compras A",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Categoria compras B",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor compras A"
        )
        self.provider_a_alternative = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor alternativo compras A"
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor compras B"
        )
        self.product_a1 = self._product(
            self.tenant_a, self.category_a, self.provider_a, "Producto compras A1"
        )
        self.product_a2 = self._product(
            self.tenant_a,
            self.category_a,
            self.provider_a_alternative,
            "Producto compras A2",
        )
        self.product_b = self._product(
            self.tenant_b, self.category_b, self.provider_b, "Producto compras B"
        )

    @staticmethod
    def _user(email, tenant, role):
        return Usuario.objects.create_user(
            Email=email,
            Password="A-strong-test-password-2026!",
            Nombre=email.split("@")[0],
            Miscelanea=tenant,
            Rol=role,
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

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def payload(self, detalles=None, **extra):
        data = {
            "proveedorId": self.provider_a.pk,
            "fechaEntrada": timezone.now().isoformat(),
            "detalles": detalles
            if detalles is not None
            else [
                {
                    "productoId": self.product_a1.pk,
                    "tipoCompra": "unidad",
                    "cantidad": 2,
                    "precioCompraUnitario": "10.25",
                },
                {
                    "productoId": self.product_a2.pk,
                    "tipoCompra": "pack(3)",
                    "cantidad": 3,
                    "precioCompraUnitario": "4.10",
                },
            ],
        }
        data.update(extra)
        return data

    @staticmethod
    def domain_counts():
        return (
            EntradaInventario.objects.count(),
            DetalleEntradaInventario.objects.count(),
            Inventario.objects.count(),
        )

    def _generic_purchase_fixture(self):
        entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdProveedor=self.provider_a,
            IdUsuario=self.admin_a,
            FechaEntrada=timezone.now(),
            Total=Decimal("10.00"),
        )
        detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=entry,
            IdProducto=self.product_a1,
            TipoCompra="unidad",
            Cantidad=1,
            PrecioCompraUnitario=Decimal("10.00"),
        )
        return entry, detail

    @staticmethod
    def _generic_snapshot():
        return (
            tuple(
                EntradaInventario.objects.order_by("IdEntradaInventario").values_list()
            ),
            tuple(
                DetalleEntradaInventario.objects.order_by("IdDetalleEntrada").values_list()
            ),
            tuple(Inventario.objects.order_by("IdInventario").values_list()),
        )

    def _entry_payload(self):
        return {
            "proveedorId": self.provider_a.pk,
            "usuarioId": self.admin_a.pk,
            "fechaEntrada": timezone.now().isoformat(),
            "total": "10.00",
        }

    def _detail_payload(self, entry):
        return {
            "entradaInventarioId": entry.pk,
            "productoId": self.product_a1.pk,
            "tipoCompra": "unidad",
            "cantidad": 1,
            "precioCompraUnitario": "10.00",
        }

    def assert_generic_write_is_closed(self, method, url, payload=None):
        before = self._generic_snapshot()
        response = getattr(self.client, method)(url, payload, format="json")
        self.assertEqual(response.status_code, 405, response.data)
        self.assertEqual(self._generic_snapshot(), before)

    def test_admin_generic_purchase_posts_are_closed_without_writes(self):
        self.authenticate(self.admin_a)
        entry, _ = self._generic_purchase_fixture()
        self.assert_generic_write_is_closed(
            "post", "/api/inventario/entradas/", self._entry_payload()
        )
        self.assert_generic_write_is_closed(
            "post",
            "/api/inventario/detalles-entrada/",
            self._detail_payload(entry),
        )

    def test_admin_generic_purchase_puts_are_closed_without_writes(self):
        self.authenticate(self.admin_a)
        entry, detail = self._generic_purchase_fixture()
        self.assert_generic_write_is_closed(
            "put", f"/api/inventario/entradas/{entry.pk}/", self._entry_payload()
        )
        self.assert_generic_write_is_closed(
            "put",
            f"/api/inventario/detalles-entrada/{detail.pk}/",
            self._detail_payload(entry),
        )

    def test_admin_generic_purchase_patches_are_closed_without_writes(self):
        self.authenticate(self.admin_a)
        entry, detail = self._generic_purchase_fixture()
        self.assert_generic_write_is_closed(
            "patch", f"/api/inventario/entradas/{entry.pk}/", {"total": "20.00"}
        )
        self.assert_generic_write_is_closed(
            "patch",
            f"/api/inventario/detalles-entrada/{detail.pk}/",
            {"cantidad": 2},
        )

    def test_admin_generic_purchase_deletes_are_closed_without_writes(self):
        self.authenticate(self.admin_a)
        entry, detail = self._generic_purchase_fixture()
        Inventario.objects.filter(IdDetalleEntrada=detail).delete()
        self.assert_generic_write_is_closed(
            "delete", f"/api/inventario/detalles-entrada/{detail.pk}/"
        )
        empty_entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdProveedor=self.provider_a,
            IdUsuario=self.admin_a,
            FechaEntrada=timezone.now(),
            Total=Decimal("0.00"),
        )
        self.assert_generic_write_is_closed(
            "delete", f"/api/inventario/entradas/{empty_entry.pk}/"
        )

    def test_generic_purchase_reads_remain_tenant_scoped(self):
        own_entry, own_detail = self._generic_purchase_fixture()
        foreign_entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant_b,
            IdProveedor=self.provider_b,
            IdUsuario=self.admin_b,
            FechaEntrada=timezone.now(),
            Total=Decimal("1.00"),
        )
        foreign_detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_b,
            IdEntradaInventario=foreign_entry,
            IdProducto=self.product_b,
            TipoCompra="unidad",
            Cantidad=1,
            PrecioCompraUnitario=Decimal("1.00"),
        )
        self.authenticate(self.admin_a)

        for url in (
            "/api/inventario/entradas/",
            "/api/inventario/detalles-entrada/",
            f"/api/inventario/entradas/{own_entry.pk}/",
            f"/api/inventario/detalles-entrada/{own_detail.pk}/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        for url in (
            f"/api/inventario/entradas/{foreign_entry.pk}/",
            f"/api/inventario/detalles-entrada/{foreign_detail.pk}/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_cashier_and_inventory_delegate_keep_permission_precedence(self):
        entry, _ = self._generic_purchase_fixture()
        self.authenticate(self.cashier_a)
        self.assertEqual(self.client.get("/api/inventario/entradas/").status_code, 403)
        self.assertEqual(
            self.client.get("/api/inventario/detalles-entrada/").status_code, 200
        )
        self.assertEqual(
            self.client.post(
                "/api/inventario/entradas/", self._entry_payload(), format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/inventario/detalles-entrada/",
                self._detail_payload(entry),
                format="json",
            ).status_code,
            403,
        )

        now = timezone.now()
        DelegacionAdmin.objects.create(
            Miscelanea=self.tenant_a,
            CreadoPor=self.admin_a,
            Empleado=self.cashier_a,
            CodigoHash=make_password("generic-purchase-delegation"),
            CodigoPrefijo="GEN",
            Modulos=["INVENTARIO"],
            DuracionMinutos=30,
            CodigoExpiraEn=now + timedelta(minutes=10),
            UsadoEn=now,
            ConcedidoHasta=now + timedelta(minutes=30),
        )
        self.assertEqual(self.client.get("/api/inventario/entradas/").status_code, 200)
        self.assertEqual(
            self.client.get("/api/inventario/detalles-entrada/").status_code, 200
        )
        self.assertEqual(
            self.client.post(
                "/api/inventario/entradas/", self._entry_payload(), format="json"
            ).status_code,
            405,
        )
        self.assertEqual(
            self.client.post(
                "/api/inventario/detalles-entrada/",
                self._detail_payload(entry),
                format="json",
            ).status_code,
            405,
        )

    def test_valid_purchase_uses_server_identity_total_tenant_and_response_contract(self):
        self.authenticate(self.admin_a)
        data = self.payload()
        data.pop("fechaEntrada")
        start = timezone.now()

        response = self.client.post(self.url, data, format="json")

        self.assertEqual(response.status_code, 201, response.json())
        end = timezone.now()
        entry = EntradaInventario.objects.get()
        self.assertEqual(entry.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(entry.IdUsuario_id, self.admin_a.pk)
        self.assertEqual(entry.IdProveedor_id, self.provider_a.pk)
        self.assertEqual(entry.Total, Decimal("32.80"))
        self.assertLessEqual(start, entry.FechaEntrada)
        self.assertLessEqual(entry.FechaEntrada, end)

        details = list(
            DetalleEntradaInventario.objects.filter(IdEntradaInventario=entry)
            .order_by("IdDetalleEntrada")
        )
        self.assertEqual([detail.Cantidad for detail in details], [2, 3])
        self.assertEqual(
            [detail.PrecioCompraUnitario for detail in details],
            [Decimal("10.25"), Decimal("4.10")],
        )
        self.assertTrue(
            all(detail.Miscelanea_id == self.tenant_a.pk for detail in details)
        )
        units = Inventario.objects.filter(IdDetalleEntrada__in=details)
        self.assertEqual(units.count(), 5)
        self.assertEqual(
            set(units.values_list("Estado", flat=True)), {"Disponible"}
        )
        self.assertEqual(
            set(units.values_list("Miscelanea_id", flat=True)), {self.tenant_a.pk}
        )

        body = response.json()
        self.assertEqual(
            set(body),
            {
                "id", "proveedorId", "usuarioId", "fechaEntrada", "total",
                "proveedorNombre", "detalles",
            },
        )
        self.assertEqual(Decimal(body["total"]), Decimal("32.80"))
        self.assertEqual(len(body["detalles"]), 2)
        self.assertEqual(
            set(body["detalles"][0]),
            {
                "id", "entradaInventarioId", "productoId", "productoNombre",
                "productoPresentacion", "tipoCompra", "cantidad",
                "precioCompraUnitario", "estadoItems",
            },
        )
        self.assertEqual(
            sum(len(detail["estadoItems"]) for detail in body["detalles"]), 5
        )

        logs = LogAuditoria.objects.filter(
            Miscelanea=self.tenant_a,
            usuario=self.admin_a,
            modulo="INVENTARIO",
            accion="CREAR",
            resultado="EXITOSO",
        )
        self.assertEqual(logs.count(), 1)

    def test_zero_cost_and_repeated_product_lines_are_allowed(self):
        self.authenticate(self.admin_a)
        repeated = [
            {
                "productoId": self.product_a1.pk,
                "tipoCompra": "unidad",
                "cantidad": 1,
                "precioCompraUnitario": "0.00",
            },
            {
                "productoId": self.product_a1.pk,
                "tipoCompra": "promocional",
                "cantidad": 2,
                "precioCompraUnitario": "3.25",
            },
        ]

        response = self.client.post(
            self.url, self.payload(repeated), format="json"
        )

        self.assertEqual(response.status_code, 201, response.json())
        entry = EntradaInventario.objects.get()
        self.assertEqual(entry.Total, Decimal("6.50"))
        self.assertEqual(entry.detalleentradainventario_set.count(), 2)
        self.assertEqual(Inventario.objects.count(), 3)

    def test_product_need_not_match_header_provider_within_same_tenant(self):
        self.authenticate(self.admin_a)
        details = [
            {
                "productoId": self.product_a2.pk,
                "tipoCompra": "unidad",
                "cantidad": 1,
                "precioCompraUnitario": "2.00",
            }
        ]

        response = self.client.post(self.url, self.payload(details), format="json")

        self.assertEqual(response.status_code, 201, response.json())
        entry = EntradaInventario.objects.get()
        detail = DetalleEntradaInventario.objects.get()
        self.assertEqual(entry.IdProveedor_id, self.provider_a.pk)
        self.assertEqual(detail.IdProducto_id, self.product_a2.pk)
        self.assertEqual(self.product_a2.IdProveedor_id, self.provider_a_alternative.pk)

    def test_client_identity_total_and_tenant_fields_are_ignored(self):
        self.authenticate(self.admin_a)
        response = self.client.post(
            self.url,
            self.payload(
                usuarioId=self.admin_b.pk,
                total="0.01",
                miscelaneaId=self.tenant_b.pk,
                IdMiscelanea=self.tenant_b.pk,
                Miscelanea=self.tenant_b.pk,
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.json())
        entry = EntradaInventario.objects.get()
        self.assertEqual(entry.IdUsuario_id, self.admin_a.pk)
        self.assertEqual(entry.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(entry.Total, Decimal("32.80"))

    def test_foreign_provider_or_product_is_rejected_without_writes(self):
        self.authenticate(self.admin_a)
        cases = (
            self.payload(proveedorId=self.provider_b.pk),
            self.payload(
                [
                    {
                        "productoId": self.product_b.pk,
                        "tipoCompra": "unidad",
                        "cantidad": 1,
                        "precioCompraUnitario": "1.00",
                    }
                ]
            ),
        )
        for data in cases:
            with self.subTest(data=data):
                before = self.domain_counts()
                response = self.client.post(self.url, data, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.domain_counts(), before)

    def test_empty_and_invalid_quantities_are_rejected_without_writes(self):
        self.authenticate(self.admin_a)
        cases = [
            self.payload([]),
            *[
                self.payload(
                    [
                        {
                            "productoId": self.product_a1.pk,
                            "tipoCompra": "unidad",
                            "cantidad": quantity,
                            "precioCompraUnitario": "1.00",
                        }
                    ]
                )
                for quantity in (0, -1, True, 1.5, "1.5")
            ],
        ]
        for data in cases:
            with self.subTest(data=data):
                response = self.client.post(self.url, data, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.domain_counts(), (0, 0, 0))

    def test_invalid_costs_future_date_and_total_overflow_are_rejected(self):
        self.authenticate(self.admin_a)
        invalid_costs = (
            "-0.01", "NaN", "Infinity", "-Infinity", "1.001", "12345678901.00",
        )
        cases = [
            *[
                self.payload(
                    [
                        {
                            "productoId": self.product_a1.pk,
                            "tipoCompra": "unidad",
                            "cantidad": 1,
                            "precioCompraUnitario": cost,
                        }
                    ]
                )
                for cost in invalid_costs
            ],
            self.payload(
                fechaEntrada=(timezone.now() + timedelta(seconds=30)).isoformat()
            ),
            self.payload(
                [
                    {
                        "productoId": self.product_a1.pk,
                        "tipoCompra": "unidad",
                        "cantidad": 2,
                        "precioCompraUnitario": "9999999999.99",
                    }
                ]
            ),
        ]
        for data in cases:
            with self.subTest(data=data):
                response = self.client.post(self.url, data, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.domain_counts(), (0, 0, 0))

    def test_failure_after_first_detail_and_units_rolls_back_everything(self):
        self.authenticate(self.admin_a)
        real_create = DetalleEntradaInventario.objects.create
        calls = 0

        def fail_on_second_detail(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("controlled purchase failure")
            return real_create(*args, **kwargs)

        with patch(
            "inventario.views.DetalleEntradaInventario.objects.create",
            side_effect=fail_on_second_detail,
        ):
            response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("controlled purchase failure", str(response.data))
        self.assertEqual(self.domain_counts(), (0, 0, 0))
        self.assertEqual(LogAuditoria.objects.count(), 0)

    def test_ordinary_cashier_is_denied_and_inventory_delegate_is_allowed(self):
        self.authenticate(self.cashier_a)
        denied = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.domain_counts(), (0, 0, 0))

        now = timezone.now()
        DelegacionAdmin.objects.create(
            Miscelanea=self.tenant_a,
            CreadoPor=self.admin_a,
            Empleado=self.cashier_a,
            CodigoHash=make_password("purchase-delegation-code"),
            CodigoPrefijo="COM",
            Modulos=["INVENTARIO"],
            DuracionMinutos=30,
            CodigoExpiraEn=now + timedelta(minutes=10),
            UsadoEn=now,
            ConcedidoHasta=now + timedelta(minutes=30),
        )
        allowed = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(allowed.status_code, 201, allowed.json())
        entry = EntradaInventario.objects.get()
        self.assertEqual(entry.IdUsuario_id, self.cashier_a.pk)
        self.assertEqual(entry.Miscelanea_id, self.tenant_a.pk)

    def test_user_without_tenant_is_denied_without_writes(self):
        technical = self._user("technical-purchases@example.test", None, "admin")
        self.authenticate(technical)

        response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.domain_counts(), (0, 0, 0))

    def test_audit_database_failure_is_best_effort_and_does_not_rollback_purchase(self):
        self.authenticate(self.admin_a)
        with patch(
            "auditoria.mixins.LogAuditoria.objects.create",
            side_effect=RuntimeError("audit-only"),
        ):
            response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(self.domain_counts(), (1, 2, 5))
        self.assertEqual(LogAuditoria.objects.count(), 0)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EditarCompraCompletaTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Edicion compras A", Codigo="EDICION-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Edicion compras B", Codigo="EDICION-B"
        )
        self.owner = self._user("owner-edicion@example.test", self.tenant_a, "admin")
        self.editor = self._user("editor-edicion@example.test", self.tenant_a, "admin")
        self.cashier = self._user("cajero-edicion@example.test", self.tenant_a, "cajero")
        self.admin_b = self._user("admin-edicion-b@example.test", self.tenant_b, "admin")
        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoria edicion A",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Categoria edicion B",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor edicion A"
        )
        self.provider_a2 = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor edicion A2"
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor edicion B"
        )
        self.product_a1 = self._product(
            self.tenant_a, self.category_a, self.provider_a, "Producto edicion A1"
        )
        self.product_a2 = self._product(
            self.tenant_a, self.category_a, self.provider_a2, "Producto edicion A2"
        )
        self.product_b = self._product(
            self.tenant_b, self.category_b, self.provider_b, "Producto edicion B"
        )
        self.entry, self.detail_1, self.detail_2 = self._entry_with_two_details()
        self.authenticate(self.editor)

    @staticmethod
    def _user(email, tenant, role):
        return Usuario.objects.create_user(
            Email=email,
            Password="A-strong-test-password-2026!",
            Nombre=email.split("@")[0],
            Miscelanea=tenant,
            Rol=role,
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

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def url(self, entry=None):
        return f"/api/inventario/entradas/{(entry or self.entry).pk}/editar-completa/"

    def _entry_with_two_details(self):
        entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdProveedor=self.provider_a,
            IdUsuario=self.owner,
            FechaEntrada=timezone.now() - timedelta(days=1),
            Total=Decimal("22.00"),
        )
        detail_1 = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=entry,
            IdProducto=self.product_a1,
            TipoCompra="unidad",
            Cantidad=2,
            PrecioCompraUnitario=Decimal("5.00"),
        )
        detail_2 = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=entry,
            IdProducto=self.product_a2,
            TipoCompra="pack",
            Cantidad=3,
            PrecioCompraUnitario=Decimal("4.00"),
        )
        return entry, detail_1, detail_2

    @staticmethod
    def detail_payload(detail, **changes):
        data = {
            "id": detail.pk,
            "productoId": detail.IdProducto_id,
            "tipoCompra": detail.TipoCompra,
            "cantidad": detail.Cantidad,
            "precioCompraUnitario": str(detail.PrecioCompraUnitario),
        }
        data.update(changes)
        return data

    def payload(self, details=None, include_date=True, **extra):
        data = {
            "proveedorId": self.entry.IdProveedor_id,
            "detalles": details
            if details is not None
            else [
                self.detail_payload(self.detail_1),
                self.detail_payload(self.detail_2),
            ],
        }
        if include_date:
            data["fechaEntrada"] = self.entry.FechaEntrada.isoformat()
        data.update(extra)
        return data

    @staticmethod
    def snapshot(entry):
        entry.refresh_from_db()
        details = list(
            DetalleEntradaInventario.objects.filter(IdEntradaInventario=entry)
            .order_by("IdDetalleEntrada")
            .values_list(
                "IdDetalleEntrada",
                "IdProducto_id",
                "TipoCompra",
                "Cantidad",
                "PrecioCompraUnitario",
            )
        )
        units = list(
            Inventario.objects.filter(IdDetalleEntrada__IdEntradaInventario=entry)
            .order_by("IdInventario")
            .values_list(
                "IdInventario", "IdDetalleEntrada_id", "Estado", "FechaMovimiento"
            )
        )
        return (
            entry.IdProveedor_id,
            entry.IdUsuario_id,
            entry.FechaEntrada,
            entry.Total,
            details,
            units,
        )

    def _single_detail_entry(self):
        entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdProveedor=self.provider_a,
            IdUsuario=self.owner,
            FechaEntrada=timezone.now() - timedelta(days=2),
            Total=Decimal("3.00"),
        )
        detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=entry,
            IdProducto=self.product_a1,
            TipoCompra="unidad",
            Cantidad=1,
            PrecioCompraUnitario=Decimal("3.00"),
        )
        return entry, detail, detail.inventarios.get()

    def _payload_for_entry(self, entry, details, **extra):
        data = {
            "proveedorId": entry.IdProveedor_id,
            "fechaEntrada": entry.FechaEntrada.isoformat(),
            "detalles": details,
        }
        data.update(extra)
        return data

    def test_unchanged_historical_detail_preserves_detail_and_unit_identity(self):
        unit = self.detail_1.inventarios.order_by("IdInventario").first()
        sale = Venta.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.owner,
            Fecha=timezone.now() - timedelta(hours=1),
            Total=Decimal("8.00"),
            Estado="Anulada",
        )
        DetalleVenta.objects.create(
            Miscelanea=self.tenant_a,
            IdVenta=sale,
            IdInventario=unit,
            PrecioVentaUnitario=Decimal("8.00"),
        )
        before = self.snapshot(self.entry)

        response = self.client.put(self.url(), self.payload(), format="json")

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(self.snapshot(self.entry), before)
        logs = LogAuditoria.objects.filter(
            Miscelanea=self.tenant_a,
            usuario=self.editor,
            modulo="INVENTARIO",
            accion="MODIFICAR",
            resultado="EXITOSO",
        )
        self.assertEqual(logs.count(), 1)

    def test_safe_in_place_changes_resize_units_and_recalculate_total(self):
        units_1_before = list(
            self.detail_1.inventarios.order_by("IdInventario").values_list(
                "IdInventario", "FechaMovimiento"
            )
        )
        units_2_before = list(
            self.detail_2.inventarios.order_by("IdInventario").values_list(
                "IdInventario", "FechaMovimiento"
            )
        )
        new_date = self.entry.FechaEntrada + timedelta(hours=2)
        data = self.payload(
            [
                self.detail_payload(
                    self.detail_1,
                    tipoCompra="caja",
                    cantidad=4,
                    precioCompraUnitario="6.25",
                ),
                self.detail_payload(self.detail_2, cantidad=1),
            ],
            usuarioId=self.admin_b.pk,
            total="0.01",
            miscelaneaId=self.tenant_b.pk,
            proveedorId=self.provider_a2.pk,
            fechaEntrada=new_date.isoformat(),
        )

        response = self.client.put(self.url(), data, format="json")

        self.assertEqual(response.status_code, 200, response.json())
        self.entry.refresh_from_db()
        self.detail_1.refresh_from_db()
        self.detail_2.refresh_from_db()
        self.assertEqual(self.entry.IdUsuario_id, self.owner.pk)
        self.assertEqual(self.entry.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(self.entry.IdProveedor_id, self.provider_a2.pk)
        self.assertEqual(self.entry.FechaEntrada, new_date)
        self.assertEqual(self.entry.Total, Decimal("29.00"))
        self.assertEqual(Decimal(response.json()["total"]), Decimal("29.00"))
        self.assertEqual(self.detail_1.TipoCompra, "caja")
        self.assertEqual(self.detail_1.Cantidad, 4)
        self.assertEqual(self.detail_1.PrecioCompraUnitario, Decimal("6.25"))
        units_1_after = list(
            self.detail_1.inventarios.order_by("IdInventario").values_list(
                "IdInventario", "FechaMovimiento"
            )
        )
        self.assertEqual(units_1_after[:2], units_1_before)
        self.assertEqual(len(units_1_after), 4)
        self.assertEqual(self.detail_2.Cantidad, 1)
        units_2_after = list(
            self.detail_2.inventarios.order_by("IdInventario").values_list(
                "IdInventario", "FechaMovimiento"
            )
        )
        self.assertEqual(units_2_after, units_2_before[:1])

    def test_add_new_detail_and_omit_safe_detail(self):
        removed_detail_id = self.detail_2.pk
        removed_unit_ids = list(
            self.detail_2.inventarios.values_list("IdInventario", flat=True)
        )
        data = self.payload(
            [
                self.detail_payload(self.detail_1),
                {
                    "productoId": self.product_a1.pk,
                    "tipoCompra": "promocional",
                    "cantidad": 2,
                    "precioCompraUnitario": "0.00",
                },
            ]
        )

        response = self.client.put(self.url(), data, format="json")

        self.assertEqual(response.status_code, 200, response.json())
        self.assertFalse(
            DetalleEntradaInventario.objects.filter(pk=removed_detail_id).exists()
        )
        self.assertFalse(Inventario.objects.filter(pk__in=removed_unit_ids).exists())
        self.assertTrue(DetalleEntradaInventario.objects.filter(pk=self.detail_1.pk).exists())
        new_detail = DetalleEntradaInventario.objects.exclude(
            pk=self.detail_1.pk
        ).get(IdEntradaInventario=self.entry)
        self.assertEqual(new_detail.IdProducto_id, self.product_a1.pk)
        self.assertEqual(new_detail.inventarios.count(), 2)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.Total, Decimal("10.00"))

    def test_changing_product_of_existing_detail_is_rejected_without_writes(self):
        before = self.snapshot(self.entry)
        details = [
            self.detail_payload(self.detail_1, productoId=self.product_a2.pk),
            self.detail_payload(self.detail_2),
        ]

        response = self.client.put(
            self.url(), self.payload(details), format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(self.entry), before)
        self.assertEqual(LogAuditoria.objects.count(), 0)

    def test_changes_are_rejected_for_nonavailable_or_historical_units(self):
        cases = []
        state_entry, state_detail, state_unit = self._single_detail_entry()
        state_unit.Estado = "Perdido"
        state_unit.save(update_fields=["Estado"])
        cases.append(("estado", state_entry, state_detail))

        sale_entry, sale_detail, sale_unit = self._single_detail_entry()
        sale = Venta.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.owner,
            Fecha=timezone.now(),
            Total=Decimal("5.00"),
            Estado="Anulada",
        )
        DetalleVenta.objects.create(
            Miscelanea=self.tenant_a,
            IdVenta=sale,
            IdInventario=sale_unit,
            PrecioVentaUnitario=Decimal("5.00"),
        )
        cases.append(("venta anulada", sale_entry, sale_detail))

        loss_entry, loss_detail, loss_unit = self._single_detail_entry()
        loss = Perdida.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.owner,
            TipoPerdida="Otra",
            Fecha=timezone.now(),
            Total=Decimal("3.00"),
        )
        DetallePerdida.objects.create(
            Miscelanea=self.tenant_a,
            IdPerdida=loss,
            IdInventario=loss_unit,
            PrecioCompraUnitario=Decimal("3.00"),
        )
        cases.append(("perdida", loss_entry, loss_detail))

        refund_entry, refund_detail, refund_unit = self._single_detail_entry()
        refund = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=refund_entry,
            IdUsuario=self.owner,
            Estado="Pendiente",
            Fecha=timezone.now(),
        )
        DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdSolicitudDevolucion=refund,
            IdInventario=refund_unit,
            PrecioCompraUnitario=Decimal("3.00"),
            EstadoItem="Pendiente",
        )
        cases.append(("devolucion", refund_entry, refund_detail))

        for label, entry, detail in cases:
            with self.subTest(label=label):
                before = self.snapshot(entry)
                data = self._payload_for_entry(
                    entry,
                    [self.detail_payload(detail, precioCompraUnitario="3.50")],
                )
                response = self.client.put(self.url(entry), data, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.snapshot(entry), before)

    def test_intact_historical_detail_allows_adding_a_new_detail(self):
        unit = self.detail_1.inventarios.order_by("IdInventario").first()
        sale = Venta.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.owner,
            Fecha=timezone.now(),
            Total=Decimal("8.00"),
            Estado="Anulada",
        )
        DetalleVenta.objects.create(
            Miscelanea=self.tenant_a,
            IdVenta=sale,
            IdInventario=unit,
            PrecioVentaUnitario=Decimal("8.00"),
        )
        details = [
            self.detail_payload(self.detail_1),
            self.detail_payload(self.detail_2),
            {
                "productoId": self.product_a2.pk,
                "tipoCompra": "unidad",
                "cantidad": 1,
                "precioCompraUnitario": "2.00",
            },
        ]

        response = self.client.put(
            self.url(), self.payload(details), format="json"
        )

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(
            DetalleEntradaInventario.objects.filter(
                IdEntradaInventario=self.entry
            ).count(),
            3,
        )
        self.assertTrue(DetalleVenta.objects.filter(pk__isnull=False).exists())

    def test_omitting_historical_detail_is_rejected_without_writes(self):
        unit = self.detail_1.inventarios.order_by("IdInventario").first()
        sale = Venta.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.owner,
            Fecha=timezone.now(),
            Total=Decimal("8.00"),
            Estado="Anulada",
        )
        DetalleVenta.objects.create(
            Miscelanea=self.tenant_a,
            IdVenta=sale,
            IdInventario=unit,
            PrecioVentaUnitario=Decimal("8.00"),
        )
        before = self.snapshot(self.entry)

        response = self.client.put(
            self.url(),
            self.payload([self.detail_payload(self.detail_2)]),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(self.entry), before)

    def test_historical_detail_blocks_header_changes_and_omitted_date_is_preserved(self):
        unit = self.detail_1.inventarios.order_by("IdInventario").first()
        sale = Venta.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.owner,
            Fecha=timezone.now(),
            Total=Decimal("8.00"),
            Estado="Anulada",
        )
        DetalleVenta.objects.create(
            Miscelanea=self.tenant_a,
            IdVenta=sale,
            IdInventario=unit,
            PrecioVentaUnitario=Decimal("8.00"),
        )
        before = self.snapshot(self.entry)
        cases = (
            self.payload(proveedorId=self.provider_a2.pk),
            self.payload(
                fechaEntrada=(self.entry.FechaEntrada + timedelta(minutes=1)).isoformat()
            ),
        )
        for data in cases:
            with self.subTest(data=data):
                response = self.client.put(self.url(), data, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.snapshot(self.entry), before)

        response = self.client.put(
            self.url(), self.payload(include_date=False), format="json"
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.FechaEntrada, before[2])

        state_entry, state_detail, state_unit = self._single_detail_entry()
        state_unit.Estado = "Perdido"
        state_unit.save(update_fields=["Estado"])
        state_before = self.snapshot(state_entry)
        state_data = self._payload_for_entry(
            state_entry,
            [self.detail_payload(state_detail)],
            proveedorId=self.provider_a2.pk,
        )
        blocked = self.client.put(
            self.url(state_entry), state_data, format="json"
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(self.snapshot(state_entry), state_before)

    def test_duplicate_unknown_other_purchase_and_foreign_detail_ids_are_rejected(self):
        other_entry, other_detail, _ = self._single_detail_entry()
        foreign_entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant_b,
            IdProveedor=self.provider_b,
            IdUsuario=self.admin_b,
            FechaEntrada=timezone.now() - timedelta(days=1),
            Total=Decimal("1.00"),
        )
        foreign_detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_b,
            IdEntradaInventario=foreign_entry,
            IdProducto=self.product_b,
            TipoCompra="unidad",
            Cantidad=1,
            PrecioCompraUnitario=Decimal("1.00"),
        )
        cases = (
            [self.detail_payload(self.detail_1), self.detail_payload(self.detail_1)],
            [dict(self.detail_payload(self.detail_1), id=99999999)],
            [dict(self.detail_payload(self.detail_1), id=other_detail.pk)],
            [dict(self.detail_payload(self.detail_1), id=foreign_detail.pk)],
        )
        before = self.snapshot(self.entry)
        for details in cases:
            with self.subTest(details=details):
                response = self.client.put(
                    self.url(), self.payload(details), format="json"
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.snapshot(self.entry), before)

    def test_validation_tenant_permissions_and_delegate_access(self):
        before = self.snapshot(self.entry)
        invalid_cases = (
            self.payload([]),
            self.payload(proveedorId=self.provider_b.pk),
            self.payload(
                [dict(self.detail_payload(self.detail_1), productoId=self.product_b.pk)]
            ),
            self.payload([dict(self.detail_payload(self.detail_1), cantidad=True)]),
            self.payload(
                [dict(self.detail_payload(self.detail_1), precioCompraUnitario="NaN")]
            ),
            self.payload(fechaEntrada=(timezone.now() + timedelta(minutes=1)).isoformat()),
        )
        for data in invalid_cases:
            with self.subTest(data=data):
                response = self.client.put(self.url(), data, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.snapshot(self.entry), before)

        self.authenticate(self.cashier)
        denied = self.client.put(self.url(), self.payload(), format="json")
        self.assertEqual(denied.status_code, 403)
        self.authenticate(self.admin_b)
        hidden = self.client.put(
            self.url(),
            {
                "proveedorId": self.provider_b.pk,
                "detalles": [
                    {
                        "productoId": self.product_b.pk,
                        "tipoCompra": "unidad",
                        "cantidad": 1,
                        "precioCompraUnitario": "1.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(hidden.status_code, 404)

        now = timezone.now()
        DelegacionAdmin.objects.create(
            Miscelanea=self.tenant_a,
            CreadoPor=self.owner,
            Empleado=self.cashier,
            CodigoHash=make_password("edit-purchase-delegation"),
            CodigoPrefijo="EDI",
            Modulos=["INVENTARIO"],
            DuracionMinutos=30,
            CodigoExpiraEn=now + timedelta(minutes=10),
            UsadoEn=now,
            ConcedidoHasta=now + timedelta(minutes=30),
        )
        self.authenticate(self.cashier)
        allowed = self.client.put(self.url(), self.payload(), format="json")
        self.assertEqual(allowed.status_code, 200, allowed.json())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.IdUsuario_id, self.owner.pk)
        self.assertEqual(
            LogAuditoria.objects.filter(
                usuario=self.cashier, accion="MODIFICAR", resultado="EXITOSO"
            ).count(),
            1,
        )

    def test_inconsistent_unit_count_is_rejected_without_repair(self):
        Inventario.objects.filter(
            IdDetalleEntrada=self.detail_1
        ).order_by("-IdInventario").first().delete()
        before = self.snapshot(self.entry)

        response = self.client.put(self.url(), self.payload(), format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(self.entry), before)

    def test_controlled_failure_after_writes_rolls_back_complete_edit(self):
        before = self.snapshot(self.entry)
        data = self.payload(
            [
                self.detail_payload(
                    self.detail_1, cantidad=3, precioCompraUnitario="6.00"
                ),
                self.detail_payload(self.detail_2),
                {
                    "productoId": self.product_a1.pk,
                    "tipoCompra": "unidad",
                    "cantidad": 1,
                    "precioCompraUnitario": "1.00",
                },
            ]
        )

        with patch(
            "inventario.views.DetalleEntradaInventario.objects.create",
            side_effect=RuntimeError("controlled complete-edit failure"),
        ):
            response = self.client.put(self.url(), data, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("controlled complete-edit failure", str(response.data))
        self.assertEqual(self.snapshot(self.entry), before)
        self.assertEqual(LogAuditoria.objects.count(), 0)

    def test_audit_failure_is_best_effort_and_does_not_rollback_edit(self):
        details = [
            self.detail_payload(self.detail_1, precioCompraUnitario="6.00"),
            self.detail_payload(self.detail_2),
        ]
        with patch(
            "auditoria.mixins.LogAuditoria.objects.create",
            side_effect=RuntimeError("edit-audit-only"),
        ):
            response = self.client.put(
                self.url(), self.payload(details), format="json"
            )

        self.assertEqual(response.status_code, 200, response.json())
        self.detail_1.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.detail_1.PrecioCompraUnitario, Decimal("6.00"))
        self.assertEqual(self.entry.Total, Decimal("24.00"))
        self.assertEqual(LogAuditoria.objects.count(), 0)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EliminarCompraCompletaTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Eliminar compras A", Codigo="ELIMINAR-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Eliminar compras B", Codigo="ELIMINAR-B"
        )
        self.admin_a = self._user("admin-eliminar-a@example.test", self.tenant_a, "admin")
        self.cashier_a = self._user(
            "cajero-eliminar-a@example.test", self.tenant_a, "cajero"
        )
        self.admin_b = self._user("admin-eliminar-b@example.test", self.tenant_b, "admin")
        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoria eliminar A",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Categoria eliminar B",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor eliminar A"
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor eliminar B"
        )
        self.product_a = self._product(
            self.tenant_a, self.category_a, self.provider_a, "Producto eliminar A"
        )
        self.product_b = self._product(
            self.tenant_b, self.category_b, self.provider_b, "Producto eliminar B"
        )
        self.entry = self._entry(self.tenant_a, self.admin_a, self.provider_a, self.product_a, (2, 3))
        self.foreign_entry = self._entry(
            self.tenant_b, self.admin_b, self.provider_b, self.product_b, (1,)
        )
        self.client.force_authenticate(self.admin_a)

    @staticmethod
    def _user(email, tenant, role):
        return Usuario.objects.create_user(
            Email=email,
            Password="A-strong-test-password-2026!",
            Nombre=email.split("@")[0],
            Miscelanea=tenant,
            Rol=role,
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
    def _entry(tenant, user, provider, product, quantities):
        entry = EntradaInventario.objects.create(
            Miscelanea=tenant,
            IdProveedor=provider,
            IdUsuario=user,
            FechaEntrada=timezone.now() - timedelta(days=1),
            Total=sum(
                (Decimal(quantity) * Decimal("5.00") for quantity in quantities),
                Decimal("0.00"),
            ),
        )
        for index, quantity in enumerate(quantities):
            DetalleEntradaInventario.objects.create(
                Miscelanea=tenant,
                IdEntradaInventario=entry,
                IdProducto=product,
                TipoCompra=f"tipo-{index}",
                Cantidad=quantity,
                PrecioCompraUnitario=Decimal("5.00"),
            )
        return entry

    @staticmethod
    def url(entry):
        return f"/api/inventario/entradas/{entry.pk}/delete_completo/"

    @staticmethod
    def snapshot(entry):
        details = list(
            DetalleEntradaInventario.objects.filter(IdEntradaInventario_id=entry.pk)
            .order_by("IdDetalleEntrada")
            .values_list(
                "IdDetalleEntrada",
                "Cantidad",
                "PrecioCompraUnitario",
            )
        )
        units = list(
            Inventario.objects.filter(
                IdDetalleEntrada__IdEntradaInventario_id=entry.pk
            )
            .order_by("IdInventario")
            .values_list(
                "IdInventario", "IdDetalleEntrada_id", "Estado", "FechaMovimiento"
            )
        )
        return (
            EntradaInventario.objects.filter(pk=entry.pk).exists(),
            details,
            units,
        )

    def _single_unit_entry(self):
        entry = self._entry(
            self.tenant_a,
            self.admin_a,
            self.provider_a,
            self.product_a,
            (1,),
        )
        detail = DetalleEntradaInventario.objects.get(IdEntradaInventario=entry)
        return entry, detail, detail.inventarios.get()

    def success_logs(self):
        return LogAuditoria.objects.filter(
            Miscelanea=self.tenant_a,
            usuario=self.admin_a,
            modulo="INVENTARIO",
            accion="ELIMINAR",
            resultado="EXITOSO",
        )

    def test_valid_delete_removes_all_rows_logs_once_and_second_is_404(self):
        detail_ids = list(
            DetalleEntradaInventario.objects.filter(
                IdEntradaInventario=self.entry
            ).values_list("pk", flat=True)
        )
        unit_ids = list(
            Inventario.objects.filter(
                IdDetalleEntrada_id__in=detail_ids
            ).values_list("pk", flat=True)
        )

        first = self.client.delete(self.url(self.entry))

        self.assertEqual(first.status_code, 204)
        self.assertEqual(first.content, b"")
        self.assertFalse(EntradaInventario.objects.filter(pk=self.entry.pk).exists())
        self.assertFalse(
            DetalleEntradaInventario.objects.filter(pk__in=detail_ids).exists()
        )
        self.assertFalse(Inventario.objects.filter(pk__in=unit_ids).exists())
        self.assertEqual(self.success_logs().count(), 1)

        second = self.client.delete(self.url(self.entry))

        self.assertEqual(second.status_code, 404)
        self.assertEqual(self.success_logs().count(), 1)

    def test_foreign_purchase_is_404_without_changes(self):
        before = self.snapshot(self.foreign_entry)

        response = self.client.delete(self.url(self.foreign_entry))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.snapshot(self.foreign_entry), before)
        self.assertEqual(self.success_logs().count(), 0)

    def test_admin_without_tenant_is_denied_and_null_tenant_purchase_is_untouched(self):
        technical = self._user("technical-delete@example.test", None, "admin")
        null_entry = EntradaInventario.objects.create(
            Miscelanea=None,
            IdProveedor=self.provider_a,
            IdUsuario=technical,
            FechaEntrada=timezone.now(),
            Total=Decimal("5.00"),
        )
        null_detail = DetalleEntradaInventario.objects.create(
            Miscelanea=None,
            IdEntradaInventario=null_entry,
            IdProducto=self.product_a,
            TipoCompra="unidad",
            Cantidad=1,
            PrecioCompraUnitario=Decimal("5.00"),
        )
        before = self.snapshot(null_entry)
        self.client.force_authenticate(technical)

        response = self.client.delete(self.url(null_entry))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.snapshot(null_entry), before)
        self.assertEqual(null_detail.inventarios.count(), 1)

    def test_cashier_is_denied_and_inventory_delegate_is_allowed(self):
        before = self.snapshot(self.entry)
        self.client.force_authenticate(self.cashier_a)

        denied = self.client.delete(self.url(self.entry))

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.snapshot(self.entry), before)

        now = timezone.now()
        DelegacionAdmin.objects.create(
            Miscelanea=self.tenant_a,
            CreadoPor=self.admin_a,
            Empleado=self.cashier_a,
            CodigoHash=make_password("delete-purchase-delegation"),
            CodigoPrefijo="DEL",
            Modulos=["INVENTARIO"],
            DuracionMinutos=30,
            CodigoExpiraEn=now + timedelta(minutes=10),
            UsadoEn=now,
            ConcedidoHasta=now + timedelta(minutes=30),
        )
        allowed = self.client.delete(self.url(self.entry))
        self.assertEqual(allowed.status_code, 204)
        self.assertFalse(EntradaInventario.objects.filter(pk=self.entry.pk).exists())
        self.assertEqual(
            LogAuditoria.objects.filter(
                Miscelanea=self.tenant_a,
                usuario=self.cashier_a,
                modulo="INVENTARIO",
                accion="ELIMINAR",
                resultado="EXITOSO",
            ).count(),
            1,
        )

    def test_each_nonavailable_state_rejects_delete_without_changes(self):
        for state in ("Vendido", "Perdido", "Devuelto"):
            with self.subTest(state=state):
                entry, _, unit = self._single_unit_entry()
                unit.Estado = state
                unit.save(update_fields=["Estado"])
                before = self.snapshot(entry)

                response = self.client.delete(self.url(entry))

                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.snapshot(entry), before)

    def test_available_units_with_each_history_type_reject_delete(self):
        cases = []
        sale_entry, _, sale_unit = self._single_unit_entry()
        sale = Venta.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.cashier_a,
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
        cases.append(("venta anulada", sale_entry))

        loss_entry, _, loss_unit = self._single_unit_entry()
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
        cases.append(("perdida", loss_entry))

        refund_entry, _, refund_unit = self._single_unit_entry()
        refund = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=refund_entry,
            IdUsuario=self.admin_a,
            Estado="Pendiente",
            Fecha=timezone.now(),
        )
        DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdSolicitudDevolucion=refund,
            IdInventario=refund_unit,
            PrecioCompraUnitario=Decimal("5.00"),
            EstadoItem="Pendiente",
        )
        cases.append(("devolucion", refund_entry))

        for label, entry in cases:
            with self.subTest(label=label):
                before = self.snapshot(entry)
                response = self.client.delete(self.url(entry))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.snapshot(entry), before)

    def test_inconsistent_quantity_is_rejected_without_repair(self):
        detail = DetalleEntradaInventario.objects.filter(
            IdEntradaInventario=self.entry
        ).order_by("IdDetalleEntrada").first()
        detail.inventarios.order_by("-IdInventario").first().delete()
        before = self.snapshot(self.entry)

        response = self.client.delete(self.url(self.entry))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(self.entry), before)

    def test_failure_after_deletions_rolls_back_header_details_and_units(self):
        before = self.snapshot(self.entry)
        with patch.object(
            EntradaInventario,
            "delete",
            autospec=True,
            side_effect=RuntimeError("controlled delete failure"),
        ):
            response = self.client.delete(self.url(self.entry))

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("controlled delete failure", str(response.data))
        self.assertEqual(self.snapshot(self.entry), before)
        self.assertEqual(self.success_logs().count(), 0)

    def test_database_restriction_is_controlled_and_rolls_back(self):
        request_record = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=self.entry,
            IdUsuario=self.admin_a,
            Estado="Pendiente",
            Fecha=timezone.now(),
        )
        before = self.snapshot(self.entry)

        response = self.client.delete(self.url(self.entry))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(self.entry), before)
        self.assertTrue(
            SolicitudDevolucion.objects.filter(pk=request_record.pk).exists()
        )
        self.assertEqual(self.success_logs().count(), 0)

    def test_audit_failure_is_best_effort_after_successful_delete(self):
        with patch(
            "auditoria.mixins.LogAuditoria.objects.create",
            side_effect=RuntimeError("delete-audit-only"),
        ):
            response = self.client.delete(self.url(self.entry))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(EntradaInventario.objects.filter(pk=self.entry.pk).exists())
        self.assertEqual(LogAuditoria.objects.count(), 0)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EliminarCompraVentaConcurrencyTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant = Miscelanea.objects.create(
            Nombre="Concurrencia compra venta", Codigo="COMPRA-VENTA"
        )
        self.admin = Usuario.objects.create_user(
            Email="admin-concurrencia-compra@example.test",
            Password="A-strong-test-password-2026!",
            Nombre="Admin concurrencia compra",
            Miscelanea=self.tenant,
            Rol="admin",
            Estado="Activo",
        )
        self.cashier = Usuario.objects.create_user(
            Email="cajero-concurrencia-compra@example.test",
            Password="A-strong-test-password-2026!",
            Nombre="Cajero concurrencia compra",
            Miscelanea=self.tenant,
            Rol="cajero",
            Estado="Activo",
        )
        category = Categoria.objects.create(
            Miscelanea=self.tenant,
            Nombre="Categoria concurrencia compra",
            PorcentajeGanancia=Decimal("20.00"),
        )
        provider = Proveedor.objects.create(
            Miscelanea=self.tenant,
            Nombre="Proveedor concurrencia compra",
        )
        product = Producto.objects.create(
            Miscelanea=self.tenant,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre="Producto concurrencia compra",
        )
        self.entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant,
            IdProveedor=provider,
            IdUsuario=self.admin,
            FechaEntrada=timezone.now(),
            Total=Decimal("20.00"),
        )
        self.detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant,
            IdEntradaInventario=self.entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=2,
            PrecioCompraUnitario=Decimal("10.00"),
        )
        self.units = list(
            self.detail.inventarios.order_by("IdInventario")
        )

    def test_sale_committing_first_forces_delete_to_preserve_purchase_history(self):
        sale_at_audit = Event()
        release_sale = Event()
        delete_pid_ready = Event()
        poll_tick = Event()
        result_lock = Lock()
        results = {}
        errors = []
        pids = {}

        def blocking_sale_audit(*args, **kwargs):
            real_audit_event(*args, **kwargs)
            sale_at_audit.set()
            if not release_sale.wait(10):
                raise TimeoutError("La venta no fue liberada a tiempo")

        def sale_worker():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.cashier)
                response = client.post(
                    "/api/ventas/procesar/",
                    {
                        "fecha": timezone.now().isoformat(),
                        "detalles": [
                            {"inventarioId": unit.pk} for unit in self.units
                        ],
                    },
                    format="json",
                )
                with result_lock:
                    results["sale"] = response.status_code
            except BaseException as exc:
                with result_lock:
                    errors.append(("sale", repr(exc)))
            finally:
                close_old_connections()

        def delete_worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                with result_lock:
                    pids["delete"] = pid
                delete_pid_ready.set()
                client = APIClient()
                client.force_authenticate(self.admin)
                response = client.delete(
                    f"/api/inventario/entradas/{self.entry.pk}/delete_completo/"
                )
                with result_lock:
                    results["delete"] = response.status_code
            except BaseException as exc:
                with result_lock:
                    errors.append(("delete", repr(exc)))
            finally:
                close_old_connections()

        sale_thread = Thread(target=sale_worker, daemon=True)
        delete_thread = Thread(target=delete_worker, daemon=True)
        delete_wait_observed = False
        with patch(
            "ventas.views.registrar_evento_manual",
            side_effect=blocking_sale_audit,
        ):
            try:
                sale_thread.start()
                self.assertTrue(sale_at_audit.wait(10))
                delete_thread.start()
                self.assertTrue(delete_pid_ready.wait(10))
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                            [pids["delete"]],
                        )
                        row = cursor.fetchone()
                    if row and row[0] == "Lock":
                        delete_wait_observed = True
                        break
                    poll_tick.wait(0.02)
            finally:
                release_sale.set()
                sale_thread.join(10)
                delete_thread.join(10)

        self.assertFalse(sale_thread.is_alive())
        self.assertFalse(delete_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(delete_wait_observed)
        self.assertEqual(results, {"sale": 201, "delete": 400})
        self.assertTrue(EntradaInventario.objects.filter(pk=self.entry.pk).exists())
        self.assertTrue(
            DetalleEntradaInventario.objects.filter(pk=self.detail.pk).exists()
        )
        for unit in self.units:
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Vendido")
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(DetalleVenta.objects.count(), 2)
        sale = Venta.objects.get()
        self.assertEqual(sale.Estado, "Completada")
        self.assertEqual(sale.detalles.count(), 2)
        self.assertEqual(
            LogAuditoria.objects.filter(
                modulo="INVENTARIO",
                accion="ELIMINAR",
                resultado="EXITOSO",
            ).count(),
            0,
        )
