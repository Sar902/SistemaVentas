from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from auditoria.models import LogAuditoria
from catalogo.models import Categoria, Producto, Proveedor
from inventario.models import DetalleEntradaInventario, EntradaInventario, Inventario
from ventas.models import DetalleVenta, Venta

from .test_t1_isolation import ApplicationIsolationTestCase


class TenantlessEndpointGuardTests(ApplicationIsolationTestCase):
    """Application guards must hold even when the database role bypasses RLS."""

    def setUp(self):
        super().setUp()
        self.no_tenant = self._user(
            "tenantless-guard@example.test", "Tenantless guard", None, "admin"
        )
        self.null_category = Categoria.objects.create(
            Miscelanea=None,
            Nombre="Historical NULL category",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.null_provider = Proveedor.objects.create(
            Miscelanea=None,
            Nombre="Historical NULL provider",
        )
        self.null_product = Producto.objects.create(
            Miscelanea=None,
            IdCategoria=self.null_category,
            IdProveedor=self.null_provider,
            Nombre="Historical NULL product",
        )
        self.null_entry = EntradaInventario.objects.create(
            Miscelanea=None,
            IdProveedor=self.null_provider,
            IdUsuario=self.no_tenant,
            FechaEntrada=timezone.now(),
            Total=Decimal("20.00"),
        )
        self.null_purchase_detail = DetalleEntradaInventario.objects.create(
            Miscelanea=None,
            IdEntradaInventario=self.null_entry,
            IdProducto=self.null_product,
            Cantidad=2,
            PrecioCompraUnitario=Decimal("10.00"),
        )
        self.null_units = list(
            Inventario.objects.filter(IdDetalleEntrada=self.null_purchase_detail)
            .order_by("IdInventario")
        )
        self.null_units[0].Estado = "Vendido"
        self.null_units[0].save(update_fields=["Estado"])
        self.null_sale = Venta.objects.create(
            Miscelanea=None,
            IdUsuario=self.no_tenant,
            Fecha=timezone.now(),
            Total=Decimal("12.00"),
            Estado="Completada",
        )
        self.null_sale_detail = DetalleVenta.objects.create(
            Miscelanea=None,
            IdVenta=self.null_sale,
            IdInventario=self.null_units[0],
            PrecioVentaUnitario=Decimal("12.00"),
        )
        self.null_log = self._log(None, self.no_tenant, "HISTORICAL_NULL_LOG")

    def authenticate_with_jwt(self, user):
        token = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_tenantless_user_cannot_process_sale_or_query_business_models(self):
        before = (Venta.objects.count(), DetalleVenta.objects.count())
        original_state = self.null_units[1].Estado
        self.authenticate_with_jwt(self.no_tenant)

        with (
            patch("ventas.views.Venta.objects.create") as create_sale,
            patch("ventas.views.Inventario.objects.select_for_update") as lock_stock,
        ):
            response = self.client.post(
                "/api/ventas/procesar/",
                {
                    "fecha": timezone.now().isoformat(),
                    "detalles": [{"inventarioId": self.null_units[1].pk}],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            {"detail": "La cuenta no tiene una miscelánea asignada."},
        )
        create_sale.assert_not_called()
        lock_stock.assert_not_called()
        self.assertEqual((Venta.objects.count(), DetalleVenta.objects.count()), before)
        self.null_units[1].refresh_from_db()
        self.assertEqual(self.null_units[1].Estado, original_state)

    def test_tenantless_user_cannot_cancel_sale_or_query_business_models(self):
        before = (self.null_sale.Estado, self.null_units[0].Estado)
        self.authenticate_with_jwt(self.no_tenant)

        with patch("ventas.views.Venta.objects.select_for_update") as lock_sale:
            response = self.client.post(
                f"/api/ventas/{self.null_sale.pk}/anular/", {}, format="json"
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            {"detail": "La cuenta no tiene una miscelánea asignada."},
        )
        lock_sale.assert_not_called()
        self.null_sale.refresh_from_db()
        self.null_units[0].refresh_from_db()
        self.assertEqual((self.null_sale.Estado, self.null_units[0].Estado), before)

    def test_tenantless_dashboard_is_empty_without_business_queries(self):
        self.authenticate_with_jwt(self.no_tenant)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/ventas/dashboard/stats/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "totalProducts": 0,
                "weeklySales": 0.0,
                "totalProveedores": 0,
                "lowStockItems": [],
                "recentSales": [],
                "weeklyLosses": 0.0,
                "chartData": [{"date": "Hoy", "total": 0}],
            },
        )
        sql = "\n".join(query["sql"] for query in queries.captured_queries)
        for table in ("Producto", "Proveedor", "Venta", "Perdida", "Inventario"):
            self.assertNotIn(f'"{table}"', sql)

    def test_tenantless_audit_list_is_empty_without_log_query(self):
        self.authenticate_with_jwt(self.no_tenant)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/auditoria/logs/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"count": 0, "next": None, "previous": None, "results": []},
        )
        sql = "\n".join(query["sql"] for query in queries.captured_queries)
        self.assertNotIn('"LogAuditoria"', sql)

    def test_real_jwt_keeps_tenant_a_and_b_dashboard_and_logs_isolated(self):
        self.authenticate_with_jwt(self.admin_a)
        dashboard_a = self.client.get("/api/ventas/dashboard/stats/")
        logs_a = self.client.get("/api/auditoria/logs/")

        self.assertEqual(dashboard_a.status_code, 200)
        self.assertEqual(dashboard_a.json()["totalProducts"], 1)
        self.assertEqual(Decimal(str(dashboard_a.json()["weeklySales"])), Decimal("12"))
        self.assertIn("LOGIN_A", str(logs_a.json()))
        self.assertNotIn("LOGIN_B", str(logs_a.json()))
        self.assertNotIn("HISTORICAL_NULL_LOG", str(logs_a.json()))

        self.authenticate_with_jwt(self.admin_b)
        dashboard_b = self.client.get("/api/ventas/dashboard/stats/")
        logs_b = self.client.get("/api/auditoria/logs/")

        self.assertEqual(dashboard_b.status_code, 200)
        self.assertEqual(dashboard_b.json()["totalProducts"], 1)
        self.assertEqual(Decimal(str(dashboard_b.json()["weeklySales"])), Decimal("999"))
        self.assertIn("LOGIN_B", str(logs_b.json()))
        self.assertNotIn("LOGIN_A", str(logs_b.json()))
        self.assertNotIn("HISTORICAL_NULL_LOG", str(logs_b.json()))

    @override_settings(DJANGO_ADMIN_EMAIL="tenantless-guard@example.test")
    def test_technical_identity_keeps_its_global_route_without_business_access(self):
        self.no_tenant.is_staff = True
        self.no_tenant.save(update_fields=["is_staff"])
        self.authenticate_with_jwt(self.no_tenant)

        self.assertEqual(self.client.get("/api/auditoria/estado-bd/").status_code, 200)
        self.assertEqual(
            self.client.get("/api/auditoria/logs/").json()["results"], []
        )
        self.assertEqual(
            self.client.post(
                "/api/ventas/procesar/",
                {"detalles": [{"inventarioId": self.null_units[1].pk}]},
                format="json",
            ).status_code,
            403,
        )
