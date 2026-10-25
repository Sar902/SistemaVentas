from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.apps import apps
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase
from rest_framework_simplejwt.tokens import RefreshToken

from auditoria.models import LogAuditoria
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
from usuarios.models import DelegacionAdmin, Miscelanea, Usuario
from ventas.models import DetalleVenta, Venta
from usuarios.test_utils import RLS_TABLES, TemporaryRLSNeutralizationMixin


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ApplicationIsolationTestCase(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    """Exercise application filters with RLS neutralized only in Django's test DB."""

    def setUp(self):
        super().setUp()
        self._create_fixture()

    def _create_fixture(self):
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Tenant A", Codigo="TENANT-A", TextoRecibo="Recibo A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Tenant B", Codigo="TENANT-B", TextoRecibo="Recibo B"
        )
        self.admin_a = self._user("admin-a@example.test", "Admin A", self.tenant_a, "admin")
        self.cashier_a = self._user("cashier-a@example.test", "Cashier A", self.tenant_a, "cajero")
        self.admin_b = self._user("admin-b@example.test", "Admin B", self.tenant_b, "admin")
        self.cashier_b = self._user("cashier-b@example.test", "Cashier B", self.tenant_b, "cajero")

        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a, Nombre="Categoria A", PorcentajeGanancia=Decimal("20")
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b, Nombre="Categoria B", PorcentajeGanancia=Decimal("20")
        )
        self.provider_a = Proveedor.objects.create(Miscelanea=self.tenant_a, Nombre="Proveedor A")
        self.provider_b = Proveedor.objects.create(Miscelanea=self.tenant_b, Nombre="Proveedor B")
        self.product_a = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Producto A",
        )
        self.product_b = Producto.objects.create(
            Miscelanea=self.tenant_b,
            IdCategoria=self.category_b,
            IdProveedor=self.provider_b,
            Nombre="Producto B",
        )
        now = timezone.now()
        self.entry_a = EntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdProveedor=self.provider_a,
            IdUsuario=self.admin_a,
            FechaEntrada=now,
            Total=Decimal("40"),
        )
        self.entry_b = EntradaInventario.objects.create(
            Miscelanea=self.tenant_b,
            IdProveedor=self.provider_b,
            IdUsuario=self.admin_b,
            FechaEntrada=now,
            Total=Decimal("400"),
        )
        self.entry_detail_a = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=self.entry_a,
            IdProducto=self.product_a,
            Cantidad=4,
            PrecioCompraUnitario=Decimal("10"),
        )
        self.entry_detail_b = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_b,
            IdEntradaInventario=self.entry_b,
            IdProducto=self.product_b,
            Cantidad=4,
            PrecioCompraUnitario=Decimal("100"),
        )
        self.inventory_a = list(
            Inventario.objects.filter(IdDetalleEntrada=self.entry_detail_a).order_by("IdInventario")
        )
        self.inventory_b = list(
            Inventario.objects.filter(IdDetalleEntrada=self.entry_detail_b).order_by("IdInventario")
        )
        self.sale_a = Venta.objects.create(
            Miscelanea=self.tenant_a, IdUsuario=self.cashier_a, Fecha=now, Total=Decimal("12")
        )
        self.sale_b = Venta.objects.create(
            Miscelanea=self.tenant_b, IdUsuario=self.cashier_b, Fecha=now, Total=Decimal("999")
        )
        self.sale_detail_a = DetalleVenta.objects.create(
            Miscelanea=self.tenant_a,
            IdVenta=self.sale_a,
            IdInventario=self.inventory_a[0],
            PrecioVentaUnitario=Decimal("12"),
        )
        self.sale_detail_b = DetalleVenta.objects.create(
            Miscelanea=self.tenant_b,
            IdVenta=self.sale_b,
            IdInventario=self.inventory_b[0],
            PrecioVentaUnitario=Decimal("999"),
        )
        self.loss_a = Perdida.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.admin_a,
            TipoPerdida="Otra",
            Fecha=now,
            Total=Decimal("10"),
        )
        self.loss_b = Perdida.objects.create(
            Miscelanea=self.tenant_b,
            IdUsuario=self.admin_b,
            TipoPerdida="Otra",
            Fecha=now,
            Total=Decimal("100"),
        )
        self.loss_detail_a = DetallePerdida.objects.create(
            Miscelanea=self.tenant_a,
            IdPerdida=self.loss_a,
            IdInventario=self.inventory_a[1],
            PrecioCompraUnitario=Decimal("10"),
        )
        self.loss_detail_b = DetallePerdida.objects.create(
            Miscelanea=self.tenant_b,
            IdPerdida=self.loss_b,
            IdInventario=self.inventory_b[1],
            PrecioCompraUnitario=Decimal("100"),
        )
        self.request_a = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=self.entry_a,
            IdUsuario=self.admin_a,
            Estado="Aceptada",
            Fecha=now,
        )
        self.request_b = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_b,
            IdEntradaInventario=self.entry_b,
            IdUsuario=self.admin_b,
            Estado="Aceptada",
            Fecha=now,
        )
        self.refund_detail_a = DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdSolicitudDevolucion=self.request_a,
            IdInventario=self.inventory_a[2],
            PrecioCompraUnitario=Decimal("10"),
            EstadoItem="Pendiente",
        )
        self.refund_detail_b = DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_b,
            IdSolicitudDevolucion=self.request_b,
            IdInventario=self.inventory_b[2],
            PrecioCompraUnitario=Decimal("100"),
            EstadoItem="Pendiente",
        )
        for item in (self.inventory_a[2], self.inventory_b[2]):
            item.Estado = "Devuelto"
            item.save(update_fields=["Estado"])

        self.log_a = self._log(self.tenant_a, self.admin_a, "LOGIN_A")
        self.log_b = self._log(self.tenant_b, self.admin_b, "LOGIN_B")
        self.delegation_a = self._delegation(self.tenant_a, self.admin_a, self.cashier_a)
        self.delegation_b = self._delegation(self.tenant_b, self.admin_b, self.cashier_b)

    def _user(self, email, name, tenant, role):
        return Usuario.objects.create_user(
            Email=email,
            Password="A-strong-test-password-2026!",
            Nombre=name,
            Miscelanea=tenant,
            Rol=role,
            Estado="Activo",
        )

    def _log(self, tenant, user, marker):
        return LogAuditoria.objects.create(
            Miscelanea=tenant,
            usuario=user,
            nombre_usuario=marker,
            accion="LOGIN",
            modulo="SISTEMA",
            descripcion=marker,
            resultado="EXITOSO",
        )

    def _delegation(self, tenant, admin_user, cashier):
        return DelegacionAdmin.objects.create(
            Miscelanea=tenant,
            CreadoPor=admin_user,
            Empleado=cashier,
            CodigoHash="not-a-real-code",
            CodigoPrefijo="ADM",
            Modulos=["AUDITORIA"],
            DuracionMinutos=60,
            CodigoExpiraEn=timezone.now() + timedelta(minutes=10),
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)


class ViewSetIsolationTests(ApplicationIsolationTestCase):
    CLOSED_WRITE_MODELS = (
        EntradaInventario,
        DetalleEntradaInventario,
        Venta,
        DetalleVenta,
        Inventario,
        Perdida,
        DetallePerdida,
        SolicitudDevolucion,
        DetalleSolicitudDevolucion,
    )

    def domain_write_snapshot(self):
        snapshot = []
        for model in self.CLOSED_WRITE_MODELS:
            fields = [field.attname for field in model._meta.concrete_fields]
            rows = tuple(
                model.objects.order_by(model._meta.pk.attname).values_list(*fields)
            )
            snapshot.append((model._meta.label, rows))
        return tuple(snapshot)

    def assert_generic_write_closed(self, method, url, payload=None):
        before = self.domain_write_snapshot()
        response = getattr(self.client, method)(url, payload, format="json")
        self.assertEqual(
            (response.status_code, self.domain_write_snapshot()),
            (405, before),
        )

    def foreign_resources(self):
        return (
            ("/api/catalogo/categorias/", self.category_b, {"name": "Cruce"}, "Nombre"),
            ("/api/catalogo/proveedores/", self.provider_b, {"name": "Cruce"}, "Nombre"),
            ("/api/catalogo/productos/", self.product_b, {"name": "Cruce"}, "Nombre"),
            ("/api/inventario/entradas/", self.entry_b, {"total": "777.00"}, "Total"),
            (
                "/api/inventario/detalles-entrada/",
                self.entry_detail_b,
                {"cantidad": 77},
                "Cantidad",
            ),
            ("/api/inventario/inventarios/", self.inventory_b[0], {"estado": "Perdido"}, "Estado"),
            ("/api/inventario/perdidas/", self.loss_b, {"tipoPerdida": "Robo"}, "TipoPerdida"),
            (
                "/api/inventario/detalles-perdida/",
                self.loss_detail_b,
                {"precioCompraUnitario": "777.00"},
                "PrecioCompraUnitario",
            ),
            (
                "/api/inventario/solicitudes-devolucion/",
                self.request_b,
                {"observaciones": "Cruce"},
                "Observaciones",
            ),
            (
                "/api/inventario/detalles-solicitud/",
                self.refund_detail_b,
                {"motivoRechazo": "Cruce"},
                "MotivoRechazo",
            ),
            ("/api/ventas/ventas/", self.sale_b, {"total": "777.00"}, "Total"),
            (
                "/api/ventas/detalles/",
                self.sale_detail_b,
                {"precioVentaUnitario": "777.00"},
                "PrecioVentaUnitario",
            ),
            ("/api/usuarios/usuarios/", self.cashier_b, {"nombre": "Cruce"}, "Nombre"),
        )

    def test_all_viewset_lists_exclude_foreign_tenant_rows(self):
        self.authenticate(self.admin_a)
        cases = (
            ("/api/catalogo/categorias/", self.category_a.pk, self.category_b.pk),
            ("/api/catalogo/proveedores/", self.provider_a.pk, self.provider_b.pk),
            ("/api/catalogo/productos/", self.product_a.pk, self.product_b.pk),
            ("/api/inventario/entradas/", self.entry_a.pk, self.entry_b.pk),
            ("/api/inventario/inventarios/", self.inventory_a[0].pk, self.inventory_b[0].pk),
            ("/api/inventario/perdidas/", self.loss_a.pk, self.loss_b.pk),
            ("/api/inventario/detalles-perdida/", self.loss_detail_a.pk, self.loss_detail_b.pk),
            ("/api/inventario/solicitudes-devolucion/", self.request_a.pk, self.request_b.pk),
            ("/api/inventario/detalles-solicitud/", self.refund_detail_a.pk, self.refund_detail_b.pk),
            ("/api/ventas/ventas/", self.sale_a.pk, self.sale_b.pk),
            ("/api/ventas/detalles/", self.sale_detail_a.pk, self.sale_detail_b.pk),
        )
        for url, own_id, foreign_id in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                rows = payload.get("results", payload) if isinstance(payload, dict) else payload
                ids = [row["id"] for row in rows]
                self.assertIn(own_id, ids)
                self.assertNotIn(foreign_id, ids)

    def test_entry_detail_list_is_filtered_without_query_parameter(self):
        self.authenticate(self.admin_a)
        response = self.client.get("/api/inventario/detalles-entrada/")
        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.json()]
        self.assertEqual(ids, [self.entry_detail_a.pk])

    def test_entry_detail_filter_cannot_select_foreign_entry(self):
        self.authenticate(self.admin_a)
        response = self.client.get(
            "/api/inventario/detalles-entrada/",
            {"entradaInventarioId": self.entry_b.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_cross_tenant_entry_detail_update_is_405_and_unchanged(self):
        self.authenticate(self.admin_a)
        response = self.client.patch(
            f"/api/inventario/detalles-entrada/{self.entry_detail_b.pk}/",
            {"cantidad": 77},
            format="json",
        )
        self.assertEqual(response.status_code, 405)
        self.entry_detail_b.refresh_from_db()
        self.assertEqual(self.entry_detail_b.Cantidad, 4)

    def test_cross_tenant_entry_detail_delete_is_405_and_unchanged(self):
        self.authenticate(self.admin_a)
        DetalleVenta.objects.filter(IdInventario__IdDetalleEntrada=self.entry_detail_b).delete()
        DetallePerdida.objects.filter(IdInventario__IdDetalleEntrada=self.entry_detail_b).delete()
        DetalleSolicitudDevolucion.objects.filter(
            IdInventario__IdDetalleEntrada=self.entry_detail_b
        ).delete()
        Inventario.objects.filter(IdDetalleEntrada=self.entry_detail_b).delete()
        response = self.client.delete(
            f"/api/inventario/detalles-entrada/{self.entry_detail_b.pk}/"
        )
        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            DetalleEntradaInventario.objects.filter(pk=self.entry_detail_b.pk).exists()
        )

    def test_cross_tenant_references_are_rejected_without_creating_rows(self):
        self.authenticate(self.admin_a)
        now = timezone.now().isoformat()
        cases = (
            (
                "/api/catalogo/productos/", Producto,
                {"name": "Cruce", "categoryId": self.category_b.pk,
                 "proveedorId": self.provider_b.pk, "status": "Activo"},
            ),
            (
                "/api/inventario/entradas/", EntradaInventario,
                {"proveedorId": self.provider_b.pk, "usuarioId": self.admin_b.pk,
                 "fechaEntrada": now, "total": "1.00"},
            ),
            (
                "/api/inventario/detalles-entrada/", DetalleEntradaInventario,
                {"entradaInventarioId": self.entry_b.pk, "productoId": self.product_b.pk,
                 "cantidad": 1, "precioCompraUnitario": "1.00"},
            ),
            (
                "/api/inventario/inventarios/", Inventario,
                {"detalleEntradaId": self.entry_detail_b.pk, "estado": "Disponible",
                 "fechaMovimiento": now},
            ),
            (
                "/api/inventario/perdidas/", Perdida,
                {"usuarioId": self.admin_b.pk, "tipoPerdida": "Otra",
                 "fecha": now, "total": "1.00"},
            ),
            (
                "/api/inventario/detalles-perdida/", DetallePerdida,
                {"perdidaId": self.loss_b.pk, "inventarioId": self.inventory_b[3].pk,
                 "precioCompraUnitario": "1.00"},
            ),
            (
                "/api/inventario/solicitudes-devolucion/", SolicitudDevolucion,
                {"entradaInventarioId": self.entry_b.pk, "usuarioId": self.admin_b.pk,
                 "fecha": now, "estado": "Pendiente"},
            ),
            (
                "/api/inventario/detalles-solicitud/", DetalleSolicitudDevolucion,
                {"solicitudDevolucionId": self.request_b.pk,
                 "inventarioId": self.inventory_b[3].pk,
                 "precioCompraUnitario": "1.00", "estadoItem": "Pendiente"},
            ),
            (
                "/api/ventas/ventas/", Venta,
                {"usuarioId": self.cashier_b.pk, "fecha": now, "total": "1.00"},
            ),
            (
                "/api/ventas/detalles/", DetalleVenta,
                {"ventaId": self.sale_b.pk, "inventarioId": self.inventory_b[3].pk,
                 "precioVentaUnitario": "1.00"},
            ),
        )
        closed_urls = {
            "/api/inventario/entradas/",
            "/api/inventario/detalles-entrada/",
            "/api/inventario/inventarios/",
            "/api/inventario/perdidas/",
            "/api/inventario/detalles-perdida/",
            "/api/inventario/solicitudes-devolucion/",
            "/api/inventario/detalles-solicitud/",
            "/api/ventas/ventas/",
            "/api/ventas/detalles/",
        }
        for url, model, payload in cases:
            with self.subTest(url=url):
                before = model.objects.count()
                response = self.client.post(url, payload, format="json")
                self.assertEqual(response.status_code, 405 if url in closed_urls else 400)
                self.assertEqual(model.objects.count(), before)

    def test_create_without_writable_tenant_is_forced_to_authenticated_tenant(self):
        self.authenticate(self.admin_a)
        cases = (
            ("/api/catalogo/categorias/", {"name": "Nueva", "profitPercentage": "10",
                                            "status": "Activo"}, Categoria),
            ("/api/catalogo/proveedores/", {"name": "Nuevo", "status": "Activo"},
             Proveedor),
            ("/api/usuarios/usuarios/", {"nombre": "Nuevo", "email": "nuevo@example.test",
                                          "password": "Strong-test-2026!", "estado": "Activo",
                                          "rol": "cajero", "miscelaneaId": self.tenant_b.pk}, Usuario),
        )
        for url, payload, model in cases:
            with self.subTest(url=url):
                response = self.client.post(url, payload, format="json")
                self.assertEqual(response.status_code, 201, response.json())
                created = model.objects.get(pk=response.json()["id"])
                self.assertEqual(created.Miscelanea_id, self.tenant_a.pk)

    def test_foreign_retrieve_update_delete_are_404_and_unchanged_for_every_viewset(self):
        self.authenticate(self.admin_a)
        read_only_urls = {
            "/api/inventario/entradas/",
            "/api/inventario/detalles-entrada/",
            "/api/inventario/inventarios/",
            "/api/inventario/perdidas/",
            "/api/inventario/detalles-perdida/",
            "/api/inventario/detalles-solicitud/",
            "/api/ventas/ventas/",
            "/api/ventas/detalles/",
        }
        for base_url, obj, patch_payload, tracked_field in self.foreign_resources():
            with self.subTest(endpoint=base_url, method="GET"):
                self.assertEqual(self.client.get(f"{base_url}{obj.pk}/").status_code, 404)
            original = getattr(obj, tracked_field)
            with self.subTest(endpoint=base_url, method="PATCH"):
                response = self.client.patch(
                    f"{base_url}{obj.pk}/", patch_payload, format="json"
                )
                self.assertEqual(response.status_code, 405 if base_url in read_only_urls else 404)
                obj.refresh_from_db()
                self.assertEqual(getattr(obj, tracked_field), original)
            with self.subTest(endpoint=base_url, method="PUT"):
                response = self.client.put(
                    f"{base_url}{obj.pk}/", patch_payload, format="json"
                )
                expected = 405 if base_url in read_only_urls or base_url == "/api/inventario/solicitudes-devolucion/" else 404
                self.assertEqual(response.status_code, expected)
                obj.refresh_from_db()
                self.assertEqual(getattr(obj, tracked_field), original)
            with self.subTest(endpoint=base_url, method="DELETE"):
                response = self.client.delete(f"{base_url}{obj.pk}/")
                expected = 405 if base_url in read_only_urls or base_url == "/api/inventario/solicitudes-devolucion/" else 404
                self.assertEqual(response.status_code, expected)
                self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())

    def test_generic_sale_writes_are_closed_without_domain_changes(self):
        self.authenticate(self.admin_a)
        now = timezone.now()
        delete_target = Venta.objects.create(
            Miscelanea=self.tenant_a, IdUsuario=self.cashier_a, Fecha=now, Total=Decimal("1")
        )
        put_target = Venta.objects.create(
            Miscelanea=self.tenant_a, IdUsuario=self.cashier_a, Fecha=now, Total=Decimal("2")
        )
        cases = (
            ("post", "/api/ventas/ventas/", {
                "usuarioId": self.cashier_a.pk, "fecha": now.isoformat(), "total": "3.00",
            }),
            ("patch", f"/api/ventas/ventas/{self.sale_a.pk}/", {"total": "4.00"}),
            ("put", f"/api/ventas/ventas/{put_target.pk}/", {
                "usuarioId": self.cashier_a.pk, "fecha": now.isoformat(), "total": "5.00",
            }),
            ("delete", f"/api/ventas/ventas/{delete_target.pk}/", None),
        )
        for method, url, payload in cases:
            with self.subTest(method=method):
                self.assert_generic_write_closed(method, url, payload)

    def test_generic_sale_detail_writes_are_closed_without_domain_changes(self):
        self.authenticate(self.admin_a)
        delete_target = DetalleVenta.objects.create(
            Miscelanea=self.tenant_a, IdVenta=self.sale_a,
            IdInventario=self.inventory_a[3], PrecioVentaUnitario=Decimal("13"),
        )
        put_target = DetalleVenta.objects.create(
            Miscelanea=self.tenant_a, IdVenta=self.sale_a,
            IdInventario=self.inventory_a[3], PrecioVentaUnitario=Decimal("14"),
        )
        cases = (
            ("post", "/api/ventas/detalles/", {
                "ventaId": self.sale_a.pk, "inventarioId": self.inventory_a[3].pk,
                "precioVentaUnitario": "15.00",
            }),
            ("patch", f"/api/ventas/detalles/{self.sale_detail_a.pk}/", {
                "precioVentaUnitario": "16.00",
            }),
            ("put", f"/api/ventas/detalles/{put_target.pk}/", {
                "ventaId": self.sale_a.pk, "inventarioId": self.inventory_a[3].pk,
                "precioVentaUnitario": "17.00",
            }),
            ("delete", f"/api/ventas/detalles/{delete_target.pk}/", None),
        )
        for method, url, payload in cases:
            with self.subTest(method=method):
                self.assert_generic_write_closed(method, url, payload)

    def test_generic_inventory_writes_are_closed_without_domain_changes(self):
        self.authenticate(self.admin_a)
        now = timezone.now()
        delete_target = Inventario.objects.create(
            Miscelanea=self.tenant_a, IdDetalleEntrada=self.entry_detail_a,
            Estado="Disponible", FechaMovimiento=now,
        )
        put_target = Inventario.objects.create(
            Miscelanea=self.tenant_a, IdDetalleEntrada=self.entry_detail_a,
            Estado="Disponible", FechaMovimiento=now,
        )
        cases = (
            ("post", "/api/inventario/inventarios/", {
                "detalleEntradaId": self.entry_detail_a.pk, "estado": "Disponible",
                "fechaMovimiento": now.isoformat(),
            }),
            ("patch", f"/api/inventario/inventarios/{self.inventory_a[3].pk}/", {
                "estado": "Perdido",
            }),
            ("put", f"/api/inventario/inventarios/{put_target.pk}/", {
                "detalleEntradaId": self.entry_detail_a.pk, "estado": "Vendido",
                "fechaMovimiento": now.isoformat(),
            }),
            ("delete", f"/api/inventario/inventarios/{delete_target.pk}/", None),
        )
        for method, url, payload in cases:
            with self.subTest(method=method):
                self.assert_generic_write_closed(method, url, payload)

    def test_generic_loss_writes_are_closed_without_domain_changes(self):
        self.authenticate(self.admin_a)
        now = timezone.now()
        delete_target = Perdida.objects.create(
            Miscelanea=self.tenant_a, IdUsuario=self.admin_a, TipoPerdida="Otra",
            Fecha=now, Total=Decimal("1"),
        )
        put_target = Perdida.objects.create(
            Miscelanea=self.tenant_a, IdUsuario=self.admin_a, TipoPerdida="Otra",
            Fecha=now, Total=Decimal("2"),
        )
        cases = (
            ("post", "/api/inventario/perdidas/", {
                "usuarioId": self.admin_a.pk, "tipoPerdida": "Otra",
                "fecha": now.isoformat(), "total": "3.00",
            }),
            ("patch", f"/api/inventario/perdidas/{self.loss_a.pk}/", {"total": "4.00"}),
            ("put", f"/api/inventario/perdidas/{put_target.pk}/", {
                "usuarioId": self.admin_a.pk, "tipoPerdida": "Otra",
                "fecha": now.isoformat(), "total": "5.00",
            }),
            ("delete", f"/api/inventario/perdidas/{delete_target.pk}/", None),
        )
        for method, url, payload in cases:
            with self.subTest(method=method):
                self.assert_generic_write_closed(method, url, payload)

    def test_generic_loss_detail_writes_are_closed_without_domain_changes(self):
        self.authenticate(self.admin_a)
        delete_target = DetallePerdida.objects.create(
            Miscelanea=self.tenant_a, IdPerdida=self.loss_a,
            IdInventario=self.inventory_a[3], PrecioCompraUnitario=Decimal("11"),
        )
        put_target = DetallePerdida.objects.create(
            Miscelanea=self.tenant_a, IdPerdida=self.loss_a,
            IdInventario=self.inventory_a[3], PrecioCompraUnitario=Decimal("12"),
        )
        cases = (
            ("post", "/api/inventario/detalles-perdida/", {
                "perdidaId": self.loss_a.pk, "inventarioId": self.inventory_a[3].pk,
                "precioCompraUnitario": "13.00",
            }),
            ("patch", f"/api/inventario/detalles-perdida/{self.loss_detail_a.pk}/", {
                "precioCompraUnitario": "14.00",
            }),
            ("put", f"/api/inventario/detalles-perdida/{put_target.pk}/", {
                "perdidaId": self.loss_a.pk, "inventarioId": self.inventory_a[3].pk,
                "precioCompraUnitario": "15.00",
            }),
            ("delete", f"/api/inventario/detalles-perdida/{delete_target.pk}/", None),
        )
        for method, url, payload in cases:
            with self.subTest(method=method):
                self.assert_generic_write_closed(method, url, payload)

    def test_generic_refund_detail_writes_are_closed_without_domain_changes(self):
        self.authenticate(self.admin_a)
        delete_target = DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a, IdSolicitudDevolucion=self.request_a,
            IdInventario=self.inventory_a[3], PrecioCompraUnitario=Decimal("11"),
        )
        put_target = DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a, IdSolicitudDevolucion=self.request_a,
            IdInventario=self.inventory_a[3], PrecioCompraUnitario=Decimal("12"),
        )
        cases = (
            ("post", "/api/inventario/detalles-solicitud/", {
                "solicitudDevolucionId": self.request_a.pk,
                "inventarioId": self.inventory_a[3].pk,
                "precioCompraUnitario": "13.00", "estadoItem": "Pendiente",
            }),
            ("patch", f"/api/inventario/detalles-solicitud/{self.refund_detail_a.pk}/", {
                "estadoItem": "Aceptado",
            }),
            ("put", f"/api/inventario/detalles-solicitud/{put_target.pk}/", {
                "solicitudDevolucionId": self.request_a.pk,
                "inventarioId": self.inventory_a[3].pk,
                "precioCompraUnitario": "15.00", "estadoItem": "Aceptado",
            }),
            ("delete", f"/api/inventario/detalles-solicitud/{delete_target.pk}/", None),
        )
        for method, url, payload in cases:
            with self.subTest(method=method):
                self.assert_generic_write_closed(method, url, payload)

    def test_refund_request_closes_post_put_delete_but_preserves_observation_patch(self):
        self.authenticate(self.admin_a)
        now = timezone.now()
        pending = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a, IdEntradaInventario=self.entry_a,
            IdUsuario=self.admin_a, Estado="Pendiente", Fecha=now,
        )
        delete_target = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a, IdEntradaInventario=self.entry_a,
            IdUsuario=self.admin_a, Estado="Pendiente", Fecha=now,
        )
        closed = (
            ("post", "/api/inventario/solicitudes-devolucion/", {
                "entradaInventarioId": self.entry_a.pk, "usuarioId": self.admin_a.pk,
                "estado": "Pendiente", "fecha": now.isoformat(),
            }),
            ("put", f"/api/inventario/solicitudes-devolucion/{pending.pk}/", {
                "entradaInventarioId": self.entry_a.pk, "usuarioId": self.admin_a.pk,
                "estado": "Pendiente", "fecha": now.isoformat(),
                "observaciones": "PUT no autorizado",
            }),
            ("delete", f"/api/inventario/solicitudes-devolucion/{delete_target.pk}/", None),
        )
        for method, url, payload in closed:
            with self.subTest(method=method):
                self.assert_generic_write_closed(method, url, payload)

        self.assertEqual(
            self.client.head("/api/inventario/solicitudes-devolucion/").status_code,
            200,
        )
        options_response = self.client.options(
            f"/api/inventario/solicitudes-devolucion/{pending.pk}/"
        )
        self.assertEqual(options_response.status_code, 200)
        allowed_methods = {
            method.strip() for method in options_response["Allow"].split(",")
        }
        self.assertTrue({"GET", "PATCH", "HEAD", "OPTIONS"} <= allowed_methods)
        self.assertTrue({"POST", "PUT", "DELETE"}.isdisjoint(allowed_methods))

        response = self.client.patch(
            f"/api/inventario/solicitudes-devolucion/{pending.pk}/",
            {"observaciones": "PATCH de observaciones vigente"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.json())
        pending.refresh_from_db()
        self.assertEqual(
            (pending.Estado, pending.Observaciones),
            ("Pendiente", "PATCH de observaciones vigente"),
        )

    def test_closed_viewsets_preserve_own_list_and_retrieve_reads(self):
        self.authenticate(self.admin_a)
        cases = (
            ("/api/ventas/ventas/", self.sale_a.pk),
            ("/api/ventas/detalles/", self.sale_detail_a.pk),
            ("/api/inventario/inventarios/", self.inventory_a[0].pk),
            ("/api/inventario/perdidas/", self.loss_a.pk),
            ("/api/inventario/detalles-perdida/", self.loss_detail_a.pk),
            ("/api/inventario/solicitudes-devolucion/", self.request_a.pk),
            ("/api/inventario/detalles-solicitud/", self.refund_detail_a.pk),
        )
        for base_url, own_id in cases:
            with self.subTest(endpoint=base_url, action="list"):
                self.assertEqual(self.client.get(base_url).status_code, 200)
            with self.subTest(endpoint=base_url, action="retrieve"):
                self.assertEqual(self.client.get(f"{base_url}{own_id}/").status_code, 200)

    def test_cashier_and_delegate_keep_existing_permission_order(self):
        now = timezone.now()
        self.authenticate(self.cashier_a)
        self.assertEqual(self.client.get("/api/ventas/ventas/").status_code, 200)
        self.assertEqual(self.client.get("/api/inventario/inventarios/").status_code, 200)
        self.assertEqual(self.client.get("/api/ventas/detalles/").status_code, 403)
        self.assertEqual(self.client.get("/api/inventario/perdidas/").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/ventas/ventas/",
                {"usuarioId": self.cashier_a.pk, "fecha": now.isoformat(), "total": "1.00"},
                format="json",
            ).status_code,
            403,
        )

        self.delegation_a.Modulos = ["VENTAS", "INVENTARIO", "PERDIDAS", "DEVOLUCIONES"]
        self.delegation_a.UsadoEn = now
        self.delegation_a.ConcedidoHasta = now + timedelta(minutes=30)
        self.delegation_a.save(update_fields=["Modulos", "UsadoEn", "ConcedidoHasta"])
        delegated_cases = (
            ("/api/ventas/ventas/", {
                "usuarioId": self.cashier_a.pk, "fecha": now.isoformat(), "total": "1.00",
            }),
            ("/api/inventario/inventarios/", {
                "detalleEntradaId": self.entry_detail_a.pk, "estado": "Disponible",
                "fechaMovimiento": now.isoformat(),
            }),
            ("/api/inventario/perdidas/", {
                "usuarioId": self.cashier_a.pk, "tipoPerdida": "Otra",
                "fecha": now.isoformat(), "total": "1.00",
            }),
            ("/api/inventario/solicitudes-devolucion/", {
                "entradaInventarioId": self.entry_a.pk, "usuarioId": self.cashier_a.pk,
                "fecha": now.isoformat(), "estado": "Pendiente",
            }),
        )
        for url, payload in delegated_cases:
            with self.subTest(delegated_endpoint=url):
                self.assertEqual(self.client.get(url).status_code, 200)
                self.assertEqual(self.client.post(url, payload, format="json").status_code, 405)

    def test_resolver_replaces_patch_then_devolver_stock(self):
        self.authenticate(self.admin_a)
        unit = self.inventory_a[3]
        unit.Estado = "Devuelto"
        unit.save(update_fields=["Estado"])
        request_obj = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=self.entry_a,
            IdUsuario=self.admin_a,
            Estado="Pendiente",
            Fecha=timezone.now(),
        )
        detail = DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdSolicitudDevolucion=request_obj,
            IdInventario=unit,
            PrecioCompraUnitario=Decimal("10"),
            EstadoItem="Pendiente",
        )
        response = self.client.post(
            f"/api/inventario/solicitudes-devolucion/{request_obj.pk}/resolver/",
            {"estado": "Aceptada"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.json())
        response = self.client.post(
            "/api/inventario/devolver-stock/",
            {"solicitud_id": request_obj.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.json())
        unit.refresh_from_db()
        detail.refresh_from_db()
        self.assertEqual((unit.Estado, detail.EstadoItem), ("Devuelto", "Aceptado"))

    def test_frontend_purchase_routes_remain_available(self):
        self.authenticate(self.admin_a)
        now = timezone.now().isoformat()
        response = self.client.post(
            "/api/inventario/entradas/crear-completa/",
            {
                "proveedorId": self.provider_a.pk,
                "fechaEntrada": now,
                "detalles": [
                    {
                        "productoId": self.product_a.pk,
                        "tipoCompra": "unidad",
                        "cantidad": 1,
                        "precioCompraUnitario": "10.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.json())
        entry_id = response.json()["id"]
        detail_id = response.json()["detalles"][0]["id"]
        response = self.client.put(
            f"/api/inventario/entradas/{entry_id}/editar-completa/",
            {
                "proveedorId": self.provider_a.pk,
                "fechaEntrada": now,
                "detalles": [
                    {
                        "id": detail_id,
                        "productoId": self.product_a.pk,
                        "tipoCompra": "unidad",
                        "cantidad": 1,
                        "precioCompraUnitario": "10.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()["detalles"][0]["id"], detail_id)
        self.assertEqual(
            self.client.get(
                "/api/inventario/detalles-entrada/",
                {"entradaInventarioId": entry_id},
            ).status_code,
            200,
        )
        response = self.client.delete(
            f"/api/inventario/entradas/{entry_id}/delete_completo/"
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(EntradaInventario.objects.filter(pk=entry_id).exists())

    def test_cajero_get_is_allowed_but_cross_tenant_write_is_403(self):
        self.authenticate(self.cashier_a)
        self.assertEqual(self.client.get("/api/catalogo/productos/").status_code, 200)
        response = self.client.patch(
            f"/api/catalogo/productos/{self.product_b.pk}/",
            {"name": "No debe cambiar"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.product_b.refresh_from_db()
        self.assertEqual(self.product_b.Nombre, "Producto B")

    def test_admin_cross_tenant_catalog_update_and_delete_are_404(self):
        self.authenticate(self.admin_a)
        response = self.client.patch(
            f"/api/catalogo/categorias/{self.category_b.pk}/",
            {"name": "No debe cambiar"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.delete(f"/api/catalogo/proveedores/{self.provider_b.pk}/")
        self.assertEqual(response.status_code, 404)
        self.category_b.refresh_from_db()
        self.assertEqual(self.category_b.Nombre, "Categoria B")
        self.assertTrue(Proveedor.objects.filter(pk=self.provider_b.pk).exists())


class CustomViewIsolationTests(ApplicationIsolationTestCase):
    def loss_payload(self, inventory_ids):
        return {
            "usuarioId": self.admin_a.pk,
            "tipoPerdida": "Otra",
            "fecha": timezone.now().isoformat(),
            "total": str(len(inventory_ids)),
            "detalles": [
                {"inventarioId": inventory_id, "precioCompraUnitario": "1.00"}
                for inventory_id in inventory_ids
            ],
        }

    def refund_payload(self, inventory_ids):
        return {
            "usuarioId": self.admin_a.pk,
            "fechaSolicitud": timezone.now().isoformat(),
            "IdEntradaInventario": self.entry_a.pk,
            "Estado": "Pendiente",
            "Observaciones": "Prueba transaccional",
            "detalles": [
                {
                    "inventarioId": inventory_id,
                    "PrecioCompraUnitario": "1.00",
                    "EstadoItem": "Pendiente",
                }
                for inventory_id in inventory_ids
            ],
        }

    def test_processing_views_reject_foreign_leading_references_without_writes(self):
        now = timezone.now().isoformat()
        cases = (
            (
                self.admin_a,
                "/api/inventario/procesar-devolucion/",
                {"usuarioId": self.admin_a.pk, "fechaSolicitud": now,
                 "IdEntradaInventario": self.entry_b.pk, "detalles": []},
                (SolicitudDevolucion, DetalleSolicitudDevolucion),
            ),
        )
        for user, url, payload, models in cases:
            with self.subTest(url=url, usuario=payload["usuarioId"],
                              entrada=payload.get("IdEntradaInventario")):
                self.authenticate(user)
                before = tuple(model.objects.count() for model in models)
                response = self.client.post(url, payload, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(tuple(model.objects.count() for model in models), before)

    def test_procesar_perdida_ignores_foreign_user_and_uses_request_user(self):
        self.authenticate(self.admin_a)
        response = self.client.post(
            "/api/inventario/procesar-perdida/",
            {
                "usuarioId": self.admin_b.pk,
                "tipoPerdida": "Otra",
                "fecha": (timezone.now() - timedelta(days=10)).isoformat(),
                "total": "999.00",
                "detalles": [
                    {
                        "inventarioId": self.inventory_a[3].pk,
                        "precioCompraUnitario": "999.00",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.json())
        loss = Perdida.objects.latest("IdPerdida")
        self.assertEqual(loss.IdUsuario_id, self.admin_a.pk)
        self.assertNotEqual(loss.IdUsuario_id, self.admin_b.pk)

    def test_procesar_venta_ignores_foreign_user_and_uses_request_user(self):
        self.authenticate(self.cashier_a)

        response = self.client.post(
            "/api/ventas/procesar/",
            {
                "usuarioId": self.cashier_b.pk,
                "fecha": timezone.now().isoformat(),
                "total": "1.00",
                "detalles": [{
                    "inventarioId": self.inventory_a[3].pk,
                    "precioVentaUnitario": "1.00",
                }],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        sale = Venta.objects.get(pk=response.json()["id"])
        self.assertEqual(sale.IdUsuario_id, self.cashier_a.pk)
        self.assertNotEqual(sale.IdUsuario_id, self.cashier_b.pk)

    def test_procesar_venta_rejects_foreign_inventory_and_rolls_back(self):
        self.authenticate(self.cashier_a)
        before = (Venta.objects.count(), DetalleVenta.objects.count())
        response = self.client.post(
            "/api/ventas/procesar/",
            {
                "usuarioId": self.cashier_a.pk,
                "fecha": timezone.now().isoformat(),
                "total": "1.00",
                "detalles": [{"inventarioId": self.inventory_b[3].pk,
                               "precioVentaUnitario": "1.00"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual((Venta.objects.count(), DetalleVenta.objects.count()), before)
        self.inventory_b[3].refresh_from_db()
        self.assertEqual(self.inventory_b[3].Estado, "Disponible")

    def test_procesar_perdida_rejects_foreign_inventory_without_partial_write(self):
        self.authenticate(self.admin_a)
        before = (Perdida.objects.count(), DetallePerdida.objects.count())
        response = self.client.post(
            "/api/inventario/procesar-perdida/",
            {
                "usuarioId": self.admin_a.pk,
                "tipoPerdida": "Otra",
                "fecha": timezone.now().isoformat(),
                "total": "1.00",
                "detalles": [{"inventarioId": self.inventory_b[3].pk,
                               "precioCompraUnitario": "1.00"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual((Perdida.objects.count(), DetallePerdida.objects.count()), before)
        self.inventory_b[3].refresh_from_db()
        self.assertEqual(self.inventory_b[3].Estado, "Disponible")

    def test_procesar_perdida_rolls_back_valid_unit_before_foreign_unit(self):
        self.authenticate(self.admin_a)
        before = (Perdida.objects.count(), DetallePerdida.objects.count())
        response = self.client.post(
            "/api/inventario/procesar-perdida/",
            self.loss_payload([self.inventory_a[3].pk, self.inventory_b[3].pk]),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual((Perdida.objects.count(), DetallePerdida.objects.count()), before)
        self.inventory_a[3].refresh_from_db()
        self.inventory_b[3].refresh_from_db()
        self.assertEqual(self.inventory_a[3].Estado, "Disponible")
        self.assertEqual(self.inventory_b[3].Estado, "Disponible")

    def test_procesar_perdida_rolls_back_controlled_failure_after_valid_write(self):
        self.authenticate(self.admin_a)
        before = (Perdida.objects.count(), DetallePerdida.objects.count())
        real_save = Inventario.save
        failing_id = self.inventory_a[3].pk

        def save_with_controlled_failure(instance, *args, **kwargs):
            if instance.pk == failing_id:
                raise RuntimeError("fallo controlado de inventario")
            return real_save(instance, *args, **kwargs)

        with patch.object(Inventario, "save", new=save_with_controlled_failure):
            response = self.client.post(
                "/api/inventario/procesar-perdida/",
                self.loss_payload([self.inventory_a[0].pk, failing_id]),
                format="json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual((Perdida.objects.count(), DetallePerdida.objects.count()), before)
        for item in (self.inventory_a[0], self.inventory_a[3]):
            item.refresh_from_db()
            self.assertEqual(item.Estado, "Disponible")

    def test_procesar_perdida_valid_operation_still_succeeds(self):
        self.authenticate(self.admin_a)
        before = (Perdida.objects.count(), DetallePerdida.objects.count())
        response = self.client.post(
            "/api/inventario/procesar-perdida/",
            self.loss_payload([self.inventory_a[3].pk]),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            (Perdida.objects.count(), DetallePerdida.objects.count()),
            (before[0] + 1, before[1] + 1),
        )
        self.inventory_a[3].refresh_from_db()
        self.assertEqual(self.inventory_a[3].Estado, "Perdido")
        created = Perdida.objects.latest("IdPerdida")
        self.assertEqual(created.Miscelanea_id, self.tenant_a.pk)

    def test_procesar_devolucion_rejects_foreign_inventory_without_partial_write(self):
        self.authenticate(self.admin_a)
        before = (
            SolicitudDevolucion.objects.count(),
            DetalleSolicitudDevolucion.objects.count(),
        )
        response = self.client.post(
            "/api/inventario/procesar-devolucion/",
            {
                "usuarioId": self.admin_a.pk,
                "fechaSolicitud": timezone.now().isoformat(),
                "IdEntradaInventario": self.entry_a.pk,
                "Estado": "Pendiente",
                "Observaciones": "Cruce",
                "detalles": [{"inventarioId": self.inventory_b[3].pk,
                               "PrecioCompraUnitario": "1.00",
                               "EstadoItem": "Pendiente"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            (SolicitudDevolucion.objects.count(), DetalleSolicitudDevolucion.objects.count()),
            before,
        )
        self.inventory_b[3].refresh_from_db()
        self.assertEqual(self.inventory_b[3].Estado, "Disponible")

    def test_procesar_devolucion_rolls_back_valid_unit_before_foreign_unit(self):
        self.authenticate(self.admin_a)
        before = (
            SolicitudDevolucion.objects.count(),
            DetalleSolicitudDevolucion.objects.count(),
        )
        response = self.client.post(
            "/api/inventario/procesar-devolucion/",
            self.refund_payload([self.inventory_a[3].pk, self.inventory_b[3].pk]),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            (SolicitudDevolucion.objects.count(), DetalleSolicitudDevolucion.objects.count()),
            before,
        )
        self.inventory_a[3].refresh_from_db()
        self.inventory_b[3].refresh_from_db()
        self.assertEqual(self.inventory_a[3].Estado, "Disponible")
        self.assertEqual(self.inventory_b[3].Estado, "Disponible")

    def test_procesar_devolucion_rolls_back_controlled_failure_after_valid_write(self):
        self.authenticate(self.admin_a)
        before = (
            SolicitudDevolucion.objects.count(),
            DetalleSolicitudDevolucion.objects.count(),
        )
        real_save = Inventario.save
        failing_id = self.inventory_a[3].pk

        def save_with_controlled_failure(instance, *args, **kwargs):
            if instance.pk == failing_id:
                raise RuntimeError("fallo controlado de inventario")
            return real_save(instance, *args, **kwargs)

        with patch.object(Inventario, "save", new=save_with_controlled_failure):
            response = self.client.post(
                "/api/inventario/procesar-devolucion/",
                self.refund_payload([self.inventory_a[0].pk, failing_id]),
                format="json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            (SolicitudDevolucion.objects.count(), DetalleSolicitudDevolucion.objects.count()),
            before,
        )
        for item in (self.inventory_a[0], self.inventory_a[3]):
            item.refresh_from_db()
            self.assertEqual(item.Estado, "Disponible")

    def test_procesar_devolucion_valid_operation_still_succeeds(self):
        self.authenticate(self.admin_a)
        before = (
            SolicitudDevolucion.objects.count(),
            DetalleSolicitudDevolucion.objects.count(),
        )
        response = self.client.post(
            "/api/inventario/procesar-devolucion/",
            self.refund_payload([self.inventory_a[3].pk]),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            (SolicitudDevolucion.objects.count(), DetalleSolicitudDevolucion.objects.count()),
            (before[0] + 1, before[1] + 1),
        )
        self.inventory_a[3].refresh_from_db()
        self.assertEqual(self.inventory_a[3].Estado, "Devuelto")
        created = SolicitudDevolucion.objects.latest("IdSolicitudDevolucion")
        self.assertEqual(created.Miscelanea_id, self.tenant_a.pk)

    def test_devolver_stock_rejects_foreign_request_and_changes_nothing(self):
        self.authenticate(self.admin_a)
        response = self.client.post(
            "/api/inventario/devolver-stock/",
            {"solicitud_id": self.request_b.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.inventory_b[2].refresh_from_db()
        self.refund_detail_b.refresh_from_db()
        self.assertEqual(self.inventory_b[2].Estado, "Devuelto")
        self.assertEqual(self.refund_detail_b.EstadoItem, "Pendiente")

    def test_anular_foreign_sale_is_404_and_unchanged(self):
        self.authenticate(self.admin_a)
        response = self.client.post(f"/api/ventas/{self.sale_b.pk}/anular/", {}, format="json")
        self.assertEqual(response.status_code, 404)
        self.sale_b.refresh_from_db()
        self.assertEqual(self.sale_b.Estado, "Completada")

    def test_dashboard_contains_only_authenticated_tenant_totals(self):
        self.authenticate(self.admin_a)
        response = self.client.get("/api/ventas/dashboard/stats/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["totalProducts"], 1)
        self.assertEqual(Decimal(str(data["weeklySales"])), Decimal("12"))
        self.assertEqual(data["totalProveedores"], 1)
        self.assertNotIn(self.sale_b.pk, [row["id"] for row in data["recentSales"]])

    def test_all_reports_return_exact_tenant_totals_with_distinct_foreign_values(self):
        self.authenticate(self.admin_a)
        today = timezone.localdate().isoformat()
        stagnant_a = Producto.objects.create(
            Miscelanea=self.tenant_a, IdCategoria=self.category_a,
            IdProveedor=self.provider_a, Nombre="Sin movimiento A",
        )
        Producto.objects.create(
            Miscelanea=self.tenant_b, IdCategoria=self.category_b,
            IdProveedor=self.provider_b, Nombre="Sin movimiento B",
        )
        annulled = Venta.objects.create(
            Miscelanea=self.tenant_a, IdUsuario=self.cashier_a,
            Fecha=timezone.now(), Total=Decimal("30"), Estado="Anulada",
        )
        DetalleVenta.objects.create(
            Miscelanea=self.tenant_a, IdVenta=annulled,
            IdInventario=self.inventory_a[3], PrecioVentaUnitario=Decimal("30"),
        )

        response = self.client.get(
            f"/api/inventario/reporte-compras-filtradas/?inicio={today}&fin={today}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(Decimal(str(response.json()[0]["total_compra"])), Decimal("40"))
        self.assertEqual(response.json()[0]["nombre_proveedor"], "Proveedor A")

        response = self.client.get("/api/inventario/reporte-productos-sin-movimiento/?dias=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(row["nombre_producto"], row["stock_actual"]) for row in response.json()],
            [(stagnant_a.Nombre, 0)],
        )

        response = self.client.get(
            f"/api/ventas/reporte-gerencial/?inicio={today}&fin={today}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.json()["total_ventas"])), Decimal("42"))
        self.assertEqual(Decimal(str(response.json()["promedio_venta"])), Decimal("21"))
        self.assertEqual(response.json()["producto_mas_vendido"], "Producto A")

        response = self.client.get(
            f"/api/ventas/reporte-pivot/?anio={timezone.localdate().year}"
            f"&productoId={self.product_a.pk}"
        )
        self.assertEqual(response.status_code, 200)
        month_key = ("ene", "feb", "mar", "abr", "may", "jun",
                     "jul", "ago", "sep", "oct", "nov", "dic")[timezone.localdate().month - 1]
        self.assertEqual(response.json()["producto"], "Producto A")
        self.assertEqual(Decimal(str(response.json()[month_key])), Decimal("42"))
        self.assertEqual(
            sum(Decimal(str(response.json()[key])) for key in
                ("ene", "feb", "mar", "abr", "may", "jun",
                 "jul", "ago", "sep", "oct", "nov", "dic")),
            Decimal("42"),
        )

        response = self.client.get(
            f"/api/ventas/productos-proveedor/?proveedorId={self.provider_a.pk}"
        )
        self.assertEqual(response.status_code, 200)
        products = {row["producto"]: row["unidades_disponibles"] for row in response.json()}
        self.assertEqual(products, {"Producto A": 3})

        response = self.client.get(
            f"/api/ventas/reporte-devoluciones/?inicio={today}&fin={today}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["producto"], "Producto A")
        self.assertEqual(response.json()[0]["cantidad"], 1)

        response = self.client.get(
            f"/api/ventas/reporte-perdidas/?inicio={today}&fin={today}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(Decimal(str(response.json()[0]["total"])), Decimal("10"))

        response = self.client.get(
            f"/api/ventas/reporte-ventas-filtradas/?inicio={today}&fin={today}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)
        self.assertEqual(
            sum(Decimal(str(row["total"])) for row in response.json()), Decimal("42")
        )

        response = self.client.get(
            f"/api/ventas/reporte-top-productos/?inicio={today}&fin={today}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["cantidad_vendida"], 2)
        self.assertEqual(Decimal(str(response.json()[0]["ingreso_bruto"])), Decimal("42"))

        response = self.client.get(
            f"/api/ventas/reporte-ganancia-producto/?inicio={today}&fin={today}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(Decimal(str(response.json()[0]["total_ventas"])), Decimal("42"))
        self.assertEqual(Decimal(str(response.json()[0]["costo_total"])), Decimal("20"))
        self.assertEqual(Decimal(str(response.json()[0]["ganancia_bruta"])), Decimal("22"))

        response = self.client.get(
            "/api/ventas/reporte-comparacion-ventas/"
            f"?inicioA={today}&finA={today}&inicioB={today}&finB={today}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        row = response.json()[0]
        self.assertEqual(Decimal(str(row["ventas_a"])), Decimal("12"))
        self.assertEqual(row["productos_a"], 1)
        self.assertEqual(Decimal(str(row["ventas_b"])), Decimal("12"))
        self.assertEqual(row["productos_b"], 1)


class UserBrandingDelegationIsolationTests(ApplicationIsolationTestCase):
    def test_user_list_and_cross_tenant_mutations_are_isolated(self):
        self.authenticate(self.admin_a)
        response = self.client.get("/api/usuarios/usuarios/")
        self.assertEqual(response.status_code, 200)
        rows = response.json().get("results", response.json())
        ids = [row["id"] for row in rows]
        self.assertIn(self.cashier_a.pk, ids)
        self.assertNotIn(self.cashier_b.pk, ids)
        response = self.client.patch(
            f"/api/usuarios/usuarios/{self.cashier_b.pk}/",
            {"nombre": "No debe cambiar"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.delete(f"/api/usuarios/usuarios/{self.cashier_b.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_branding_reads_and_updates_only_own_tenant(self):
        self.authenticate(self.admin_a)
        response = self.client.get("/api/usuarios/miscelanea/branding/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["codigo"], "TENANT-A")
        response = self.client.patch(
            "/api/usuarios/miscelanea/branding/",
            {"nombre": "Tenant A actualizado"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.tenant_b.refresh_from_db()
        self.assertEqual(self.tenant_b.Nombre, "Tenant B")

    def test_delegation_list_create_and_revoke_are_tenant_scoped(self):
        self.authenticate(self.admin_a)
        response = self.client.get("/api/usuarios/delegacion-admin/")
        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.json()]
        self.assertIn(self.delegation_a.pk, ids)
        self.assertNotIn(self.delegation_b.pk, ids)
        response = self.client.post(
            "/api/usuarios/delegacion-admin/crear/",
            {"empleadoId": self.cashier_b.pk, "modulos": ["AUDITORIA"], "duracionHoras": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.post(
            f"/api/usuarios/delegacion-admin/{self.delegation_b.pk}/revocar/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.delegation_b.refresh_from_db()
        self.assertFalse(self.delegation_b.Revocado)

    def test_cross_tenant_delegation_code_cannot_be_activated(self):
        code = "ADM-CROSS-TENANT"
        self.delegation_b.CodigoHash = make_password(code)
        self.delegation_b.save(update_fields=["CodigoHash"])
        original = (
            self.delegation_b.UsadoEn,
            self.delegation_b.ConcedidoHasta,
            self.delegation_b.Empleado_id,
        )
        self.authenticate(self.cashier_a)
        response = self.client.post(
            "/api/usuarios/delegacion-admin/activar/",
            {"codigo": code},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.delegation_b.refresh_from_db()
        self.assertEqual(
            (self.delegation_b.UsadoEn, self.delegation_b.ConcedidoHasta,
             self.delegation_b.Empleado_id),
            original,
        )


class AuditIsolationTests(ApplicationIsolationTestCase):
    def test_audit_log_list_is_tenant_scoped(self):
        self.authenticate(self.admin_a)
        response = self.client.get("/api/auditoria/logs/")
        self.assertEqual(response.status_code, 200)
        body = str(response.json())
        self.assertIn("LOGIN_A", body)
        self.assertNotIn("LOGIN_B", body)

    def test_sessions_are_tenant_scoped(self):
        self.authenticate(self.admin_a)
        response = self.client.get("/api/auditoria/sesiones/")
        self.assertEqual(response.status_code, 200)
        body = str(response.json())
        self.assertEqual(response.json()["total_intentos"], 1)
        self.assertIn("LOGIN_A", body)
        self.assertNotIn("LOGIN_B", body)

    def test_user_without_tenant_gets_no_global_sessions(self):
        no_tenant = self._user("no-tenant@example.test", "No tenant", None, "admin")
        self._log(None, no_tenant, "GLOBAL_LOGIN")
        self.authenticate(no_tenant)
        response = self.client.get("/api/auditoria/sesiones/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_intentos"], 0)
        self.assertEqual(response.json()["sesiones"], [])

    @override_settings(DJANGO_ADMIN_EMAIL="technical@example.test")
    def test_database_status_allows_only_exact_django_admin_identity(self):
        technical = self._user("technical@example.test", "Technical", None, "cajero")
        technical.is_staff = True
        technical.save(update_fields=["is_staff"])
        access_token = str(RefreshToken.for_user(technical).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        self.assertEqual(self.client.get("/api/auditoria/estado-bd/").status_code, 200)
        self.client.credentials()

        technical.is_active = False
        self.authenticate(technical)
        self.assertEqual(self.client.get("/api/auditoria/estado-bd/").status_code, 403)
        technical.is_active = True
        technical.is_staff = False
        technical.save(update_fields=["is_staff"])
        self.authenticate(technical)
        self.assertEqual(self.client.get("/api/auditoria/estado-bd/").status_code, 403)

        denied_users = [self.admin_a, self.cashier_a]
        wrong_email = self._user("staff@example.test", "Staff", None, "admin")
        wrong_email.is_staff = True
        wrong_email.save(update_fields=["is_staff"])
        denied_users.append(wrong_email)
        for user in denied_users:
            with self.subTest(user=user.Email):
                self.authenticate(user)
                self.assertEqual(self.client.get("/api/auditoria/estado-bd/").status_code, 403)
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get("/api/auditoria/estado-bd/").status_code, (401, 403))


class LoginAuditTests(ApplicationIsolationTestCase):
    def login(self, email, password, code=None):
        payload = {"Email": email, "password": password}
        if code is not None:
            payload["codigo"] = code
        return self.client.post("/api/token/", payload, format="json")

    def latest_login_log(self):
        return LogAuditoria.objects.filter(accion="LOGIN").latest("IdLog")

    def assert_malformed_login_matches_auth_flow(self, payload):
        with (
            patch("auditoria.mixins.registrar_evento_manual"),
            patch.object(Miscelanea.objects, "filter") as tenant_filter,
            patch.object(Usuario.objects, "filter") as user_filter,
        ):
            tenant_filter.return_value.first.return_value = None
            user_filter.return_value.first.return_value = None
            expected = self.client.post("/api/token/", payload, format="json")

        self.client.raise_request_exception = False
        actual = self.client.post("/api/token/", payload, format="json")
        self.client.raise_request_exception = True
        self.assertLess(actual.status_code, 500)
        self.assertEqual(actual.status_code, expected.status_code)
        self.assertEqual(actual.json(), expected.json())

    def test_malformed_email_types_do_not_change_authentication_response(self):
        for value in (["admin-a@example.test"], {"email": "admin-a@example.test"}, None, 7):
            with self.subTest(value=value):
                self.assert_malformed_login_matches_auth_flow({
                    "Email": value,
                    "password": "wrong",
                    "codigo": self.tenant_a.Codigo,
                })

    def test_malformed_tenant_code_types_do_not_change_authentication_response(self):
        for value in ([self.tenant_a.Codigo], {"codigo": self.tenant_a.Codigo}, None, 7):
            with self.subTest(value=value):
                self.assert_malformed_login_matches_auth_flow({
                    "Email": self.admin_a.Email,
                    "password": "wrong",
                    "codigo": value,
                })

    def test_successful_login_records_authenticated_user_and_tenant(self):
        before = LogAuditoria.objects.filter(accion="LOGIN").count()
        response = self.login(
            self.admin_a.Email, "A-strong-test-password-2026!", self.tenant_a.Codigo
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(LogAuditoria.objects.filter(accion="LOGIN").count(), before + 1)
        log = self.latest_login_log()
        self.assertEqual(log.usuario_id, self.admin_a.pk)
        self.assertEqual(log.Miscelanea_id, self.tenant_a.pk)

    def test_failed_login_with_valid_code_records_same_tenant_user(self):
        before = LogAuditoria.objects.filter(accion="LOGIN").count()
        response = self.login(self.admin_a.Email, "wrong", self.tenant_a.Codigo)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(LogAuditoria.objects.filter(accion="LOGIN").count(), before + 1)
        log = self.latest_login_log()
        self.assertEqual(log.usuario_id, self.admin_a.pk)
        self.assertEqual(log.Miscelanea_id, self.tenant_a.pk)

    def test_failed_login_with_valid_code_and_unknown_email_records_only_tenant(self):
        response = self.login("unknown@example.test", "wrong", self.tenant_a.Codigo)
        self.assertEqual(response.status_code, 401)
        log = self.latest_login_log()
        self.assertIsNone(log.usuario_id)
        self.assertEqual(log.Miscelanea_id, self.tenant_a.pk)

    def test_cross_tenant_email_is_never_attributed_to_user(self):
        response = self.login(self.admin_b.Email, "wrong", self.tenant_a.Codigo)
        self.assertEqual(response.status_code, 401)
        log = self.latest_login_log()
        self.assertIsNone(log.usuario_id)
        self.assertEqual(log.Miscelanea_id, self.tenant_a.pk)

    def test_missing_or_invalid_code_records_no_tenant_or_user(self):
        for code in (None, "INVALID"):
            with self.subTest(code=code):
                response = self.login(self.admin_a.Email, "wrong", code)
                self.assertEqual(response.status_code, 400 if code is None else 401)
                log = self.latest_login_log()
                self.assertIsNone(log.usuario_id)
                self.assertIsNone(log.Miscelanea_id)
                self.assertEqual(log.datos_nuevos, {"tenant_identificado": False})

    def test_login_log_never_stores_secret_or_request_body(self):
        secret = "A-strong-test-password-2026!"
        response = self.login(self.admin_a.Email, secret, self.tenant_a.Codigo)
        self.assertEqual(response.status_code, 200)
        log = self.latest_login_log()
        serialized = " ".join(
            str(value)
            for value in (
                log.descripcion,
                log.datos_anteriores,
                log.datos_nuevos,
                log.user_agent,
            )
        )
        self.assertNotIn(secret, serialized)
        self.assertNotIn("access", serialized.lower())
        self.assertNotIn("refresh", serialized.lower())

    def test_audit_write_failure_preserves_failed_authentication_response(self):
        payload = {
            "Email": "unknown@example.test",
            "password": "wrong",
            "codigo": self.tenant_a.Codigo,
        }
        with patch("auditoria.mixins.registrar_evento_manual"):
            expected = self.client.post("/api/token/", payload, format="json")
        before = LogAuditoria.objects.count()
        with patch.object(LogAuditoria.objects, "create", side_effect=RuntimeError("audit-only")):
            actual = self.client.post("/api/token/", payload, format="json")
        self.assertEqual(actual.status_code, expected.status_code)
        self.assertEqual(actual.json(), expected.json())
        self.assertNotIn("audit-only", str(actual.json()))
        self.assertEqual(LogAuditoria.objects.count(), before)

    def test_audit_write_failure_preserves_successful_authentication_response(self):
        before = LogAuditoria.objects.count()
        with patch.object(LogAuditoria.objects, "create", side_effect=RuntimeError("audit-only")):
            response = self.login(
                self.admin_a.Email,
                "A-strong-test-password-2026!",
                self.tenant_a.Codigo,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"access", "refresh"})
        self.assertNotIn("audit-only", str(response.json()))
        self.assertEqual(LogAuditoria.objects.count(), before)


class DiagnoseTenantsCommandTests(ApplicationIsolationTestCase):
    @override_settings(DJANGO_ADMIN_EMAIL="technical-diagnostic@example.test")
    def test_command_reports_counts_and_classifies_only_evidenced_nulls(self):
        technical = self._user(settings.DJANGO_ADMIN_EMAIL, "Technical", None, "admin")
        technical.is_staff = True
        technical.save(update_fields=["is_staff"])
        LogAuditoria.objects.create(
            usuario=None,
            Miscelanea=None,
            nombre_usuario="Anonimo",
            accion="LOGIN",
            modulo="SISTEMA",
            descripcion="Intento sin tenant identificable.",
            datos_nuevos={"tenant_identificado": False},
            resultado="FALLIDO",
        )
        LogAuditoria.objects.create(
            usuario=None,
            Miscelanea=None,
            nombre_usuario="Persona sensible",
            accion="ERROR_ACCESS",
            modulo="SISTEMA",
            descripcion="Dato personal que nunca debe imprimirse",
            resultado="FALLIDO",
        )
        tenant_models = {
            model._meta.db_table: model
            for model in apps.get_models()
            if any(field.name == "Miscelanea" for field in model._meta.fields)
        }
        before = {table: model.objects.count() for table, model in tenant_models.items()}
        output = StringIO()
        call_command("diagnosticar_miscelaneas", stdout=output)
        text = output.getvalue()
        rows = {}
        for line in text.splitlines()[1:]:
            table, total, nulls, classification = line.split("\t")
            rows[table] = (int(total), int(nulls), classification)
        expected_counts = {
            "Categoria": (2, 0), "Proveedor": (2, 0), "Producto": (2, 0),
            "EntradaInventario": (2, 0), "DetalleEntradaInventario": (2, 0),
            "Inventario": (8, 0), "Perdida": (2, 0), "DetallePerdida": (2, 0),
            "SolicitudDevolucion": (2, 0), "DetalleSolicitudDevolucion": (2, 0),
            "Venta": (2, 0), "DetalleVenta": (2, 0), "DelegacionAdmin": (2, 0),
            "Usuario": (5, 1), "LogAuditoria": (4, 2),
        }
        self.assertEqual(set(rows), set(expected_counts))
        for table, counts in expected_counts.items():
            with self.subTest(table=table):
                self.assertEqual(rows[table][:2], counts)
        for table in expected_counts.keys() - {"Usuario", "LogAuditoria"}:
            self.assertEqual(rows[table][2], "sin nulos")
        self.assertTrue(rows["Usuario"][2].startswith("intencional:"))
        self.assertTrue(rows["LogAuditoria"][2].startswith("mixto:"))
        self.assertIn("resto no evidenciado", rows["LogAuditoria"][2])
        for sensitive in (
            technical.Email, technical.Nombre, self.admin_a.Email, self.admin_a.Nombre,
            "Persona sensible", "Dato personal que nunca debe imprimirse",
        ):
            self.assertNotIn(sensitive, text)
        self.assertEqual(
            before,
            {table: model.objects.count() for table, model in tenant_models.items()},
        )

    def test_command_reports_counts_as_not_verifiable_when_rls_blocks_global_view(self):
        """This mocked branch does not replace an execution under an actual RLS-limited role."""
        before = Producto.objects.count()
        output = StringIO()
        with patch(
            "usuarios.management.commands.diagnosticar_miscelaneas.Command."
            "_global_count_blocked_by_rls",
            return_value=True,
        ):
            call_command("diagnosticar_miscelaneas", stdout=output)
        self.assertIn("no verificable por RLS", output.getvalue())
        self.assertEqual(Producto.objects.count(), before)


class RLSHarnessRestorationTests(ApplicationIsolationTestCase):
    def test_restore_returns_enable_and_force_flags_to_their_exact_original_values(self):
        original = dict(self._rls_state)
        self._restore_rls()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema() AND c.relname = ANY(%s)
                """,
                [list(RLS_TABLES)],
            )
            restored = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
        self.assertEqual(restored, original)
        self._rls_state = {}
