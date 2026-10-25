from datetime import datetime
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase
from rest_framework_simplejwt.tokens import RefreshToken

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
from usuarios.test_utils import (
    RLS_TABLES,
    RLS_TEST_ROLE,
    RestrictedRLSRoleMixin,
    TemporaryRLSNeutralizationMixin,
)
from ventas.models import DetalleVenta, Venta


class DashboardReportFixtureMixin:
    """Datos deterministas A/B, incluidos cruces tenant deliberados."""

    def _create_report_fixture(self):
        self.tenant_a = Miscelanea.objects.create(
            Nombre="RLS reportes A", Codigo="RLS-REP-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="RLS reportes B", Codigo="RLS-REP-B"
        )
        self.admin_a = self._user("rls-reports-a@example.test", self.tenant_a)
        self.admin_b = self._user("rls-reports-b@example.test", self.tenant_b)
        self.tenantless_admin = self._user("rls-reports-none@example.test", None)

        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoría reportes A",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Categoría reportes B",
            PorcentajeGanancia=Decimal("80.00"),
        )
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor reportes A"
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor reportes B"
        )
        self.product_a = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Producto reportes A",
        )
        self.product_b = Producto.objects.create(
            Miscelanea=self.tenant_b,
            IdCategoria=self.category_b,
            IdProveedor=self.provider_b,
            Nombre="Producto reportes B",
        )
        self.stagnant_a = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Sin movimiento A",
        )
        self.optional_provider_a = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=None,
            Nombre="Sin proveedor A",
        )
        self.cross_category_product_a = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_b,
            IdProveedor=self.provider_a,
            Nombre="Categoría cruzada A",
        )
        Producto.objects.create(
            Miscelanea=self.tenant_b,
            IdCategoria=self.category_b,
            IdProveedor=self.provider_b,
            Nombre="Sin movimiento B",
        )

        self.now = timezone.now()
        self.today = timezone.localdate(self.now)
        self.old_date = timezone.make_aware(datetime(2025, 1, 15, 12, 0))
        self.entry_a, self.detail_a, self.inventory_a = self._purchase(
            self.tenant_a,
            self.admin_a,
            self.provider_a,
            self.product_a,
            Decimal("10.00"),
            Decimal("150.00"),
        )
        self.entry_b, self.detail_b, self.inventory_b = self._purchase(
            self.tenant_b,
            self.admin_b,
            self.provider_b,
            self.product_b,
            Decimal("100.00"),
            Decimal("1500.00"),
        )

        self.sale_a = self._sale(
            self.tenant_a,
            self.admin_a,
            self.now,
            Decimal("12.00"),
            "Completada",
            self.inventory_a[0],
            Decimal("12.00"),
        )
        self.annulled_a = self._sale(
            self.tenant_a,
            self.admin_a,
            self.now,
            Decimal("30.00"),
            "Anulada",
            self.inventory_a[1],
            Decimal("30.00"),
        )
        self.old_sale_a = self._sale(
            self.tenant_a,
            self.admin_a,
            self.old_date,
            Decimal("7.00"),
            "Completada",
            self.inventory_a[4],
            Decimal("7.00"),
        )
        self._sale(
            self.tenant_b,
            self.admin_b,
            self.now,
            Decimal("999.00"),
            "Completada",
            self.inventory_b[0],
            Decimal("999.00"),
        )

        self.loss_a = Perdida.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.admin_a,
            TipoPerdida="Otra",
            Fecha=self.now,
            Total=Decimal("10.00"),
        )
        DetallePerdida.objects.create(
            Miscelanea=self.tenant_a,
            IdPerdida=self.loss_a,
            IdInventario=self.inventory_a[2],
            PrecioCompraUnitario=Decimal("10.00"),
        )
        loss_b = Perdida.objects.create(
            Miscelanea=self.tenant_b,
            IdUsuario=self.admin_b,
            TipoPerdida="Otra",
            Fecha=self.now,
            Total=Decimal("900.00"),
        )
        DetallePerdida.objects.create(
            Miscelanea=self.tenant_b,
            IdPerdida=loss_b,
            IdInventario=self.inventory_b[1],
            PrecioCompraUnitario=Decimal("900.00"),
        )

        self.return_a = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=self.entry_a,
            IdUsuario=self.admin_b,
            Estado="Aceptada",
            Fecha=self.now,
        )
        DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdSolicitudDevolucion=self.return_a,
            IdInventario=self.inventory_a[3],
            PrecioCompraUnitario=Decimal("10.00"),
            EstadoItem="Aceptado",
        )
        return_b = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_b,
            IdEntradaInventario=self.entry_b,
            IdUsuario=self.admin_b,
            Estado="Aceptada",
            Fecha=self.now,
        )
        DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_b,
            IdSolicitudDevolucion=return_b,
            IdInventario=self.inventory_b[2],
            PrecioCompraUnitario=Decimal("900.00"),
            EstadoItem="Aceptado",
        )

        for unit, state in (
            (self.inventory_a[0], "Vendido"),
            (self.inventory_a[2], "Perdido"),
            (self.inventory_a[3], "Devuelto"),
            (self.inventory_a[4], "Vendido"),
        ):
            unit.Estado = state
            unit.save(update_fields=["Estado"])

        # Cruces deliberados: RLS debe ocultarlos; no se consideran coherentes.
        self.cross_sale_detail = DetalleVenta.objects.create(
            Miscelanea=self.tenant_b,
            IdVenta=self.sale_a,
            IdInventario=self.inventory_a[5],
            PrecioVentaUnitario=Decimal("500.00"),
        )
        DetallePerdida.objects.create(
            Miscelanea=self.tenant_b,
            IdPerdida=self.loss_a,
            IdInventario=self.inventory_a[6],
            PrecioCompraUnitario=Decimal("500.00"),
        )
        DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_b,
            IdSolicitudDevolucion=self.return_a,
            IdInventario=self.inventory_a[7],
            PrecioCompraUnitario=Decimal("500.00"),
            EstadoItem="Aceptado",
        )
        self.cross_entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdProveedor=self.provider_b,
            IdUsuario=self.admin_a,
            FechaEntrada=self.now,
            Total=Decimal("777.00"),
        )
        self.cross_entry_detail_a = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=self.cross_entry,
            IdProducto=self.cross_category_product_a,
            TipoCompra="unidad",
            Cantidad=10,
            PrecioCompraUnitario=Decimal("77.70"),
        )
        self.assertEqual(
            Inventario.objects.filter(
                IdDetalleEntrada=self.cross_entry_detail_a
            ).count(),
            10,
        )
        DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_b,
            IdEntradaInventario=self.entry_a,
            IdProducto=self.optional_provider_a,
            TipoCompra="unidad",
            Cantidad=1,
            PrecioCompraUnitario=Decimal("321.00"),
        )
        foreign_stagnant_detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_b,
            IdEntradaInventario=self.entry_b,
            IdProducto=self.stagnant_a,
            TipoCompra="unidad",
            Cantidad=4,
            PrecioCompraUnitario=Decimal("654.00"),
        )
        for foreign_stagnant_unit in Inventario.objects.filter(
            IdDetalleEntrada=foreign_stagnant_detail
        ):
            DetalleVenta.objects.create(
                Miscelanea=self.tenant_b,
                IdVenta=self.sale_a,
                IdInventario=foreign_stagnant_unit,
                PrecioVentaUnitario=Decimal("600.00"),
            )

    @staticmethod
    def _user(email, tenant):
        return Usuario.objects.create_user(
            Email=email,
            Password="A-strong-test-password-2026!",
            Nombre=email,
            Miscelanea=tenant,
            Rol="admin",
            Estado="Activo",
        )

    def _purchase(self, tenant, user, provider, product, cost, total):
        entry = EntradaInventario.objects.create(
            Miscelanea=tenant,
            IdProveedor=provider,
            IdUsuario=user,
            FechaEntrada=self.now,
            Total=total,
        )
        detail = DetalleEntradaInventario.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=15,
            PrecioCompraUnitario=cost,
        )
        units = list(
            Inventario.objects.filter(IdDetalleEntrada=detail).order_by(
                "IdInventario"
            )
        )
        return entry, detail, units

    @staticmethod
    def _sale(tenant, user, date, total, state, unit, price):
        sale = Venta.objects.create(
            Miscelanea=tenant,
            IdUsuario=user,
            Fecha=date,
            Total=total,
            Estado=state,
        )
        DetalleVenta.objects.create(
            Miscelanea=tenant,
            IdVenta=sale,
            IdInventario=unit,
            PrecioVentaUnitario=price,
        )
        return sale

    def _report_urls(self):
        today = self.today.isoformat()
        old = self.old_date.date().isoformat()
        return {
            "compras": (
                f"/api/inventario/reporte-compras-filtradas/?inicio={today}&fin={today}"
            ),
            "sin_movimiento": "/api/inventario/reporte-productos-sin-movimiento/?dias=0",
            "gerencial": f"/api/ventas/reporte-gerencial/?inicio={today}&fin={today}",
            "pivot": (
                f"/api/ventas/reporte-pivot/?anio={self.today.year}"
                f"&productoId={self.product_a.pk}"
            ),
            "proveedor": (
                f"/api/ventas/productos-proveedor/?proveedorId={self.provider_a.pk}"
            ),
            "devoluciones": (
                f"/api/ventas/reporte-devoluciones/?inicio={today}&fin={today}"
            ),
            "perdidas": f"/api/ventas/reporte-perdidas/?inicio={today}&fin={today}",
            "ventas": (
                f"/api/ventas/reporte-ventas-filtradas/?inicio={today}&fin={today}"
            ),
            "top": f"/api/ventas/reporte-top-productos/?inicio={today}&fin={today}",
            "ganancia": (
                f"/api/ventas/reporte-ganancia-producto/?inicio={today}&fin={today}"
            ),
            "comparacion": (
                "/api/ventas/reporte-comparacion-ventas/"
                f"?inicioA={today}&finA={today}&inicioB={old}&finB={old}"
            ),
        }


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DashboardReportRLSRealRoleTests(
    DashboardReportFixtureMixin,
    RestrictedRLSRoleMixin,
    APITransactionTestCase,
):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self._create_report_fixture()

    def _get(self, client, url):
        response = client.get(url)
        self.assertEqual(self._context(), "")
        return response

    def test_direct_aggregates_prove_rls_for_report_tables(self):
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
                  AND c.relname = 'Venta'
                """
            )
            self.assertEqual(
                cursor.fetchone(),
                (RLS_TEST_ROLE, False, False, False, True, True),
            )
            cursor.execute(
                """
                SELECT COUNT(*) FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relname = ANY(%s)
                  AND c.relrowsecurity AND c.relforcerowsecurity
                """,
                [list(RLS_TABLES)],
            )
            self.assertEqual(cursor.fetchone()[0], len(RLS_TABLES))

        for empty_context in (None, ""):
            if empty_context == "":
                self._set_context("")
            with connection.cursor() as cursor:
                cursor.execute('SELECT COUNT(*), COALESCE(SUM("Total"), 0) FROM "Venta"')
                self.assertEqual(cursor.fetchone(), (0, Decimal("0")))

        self._set_context(self.tenant_a.pk)
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*), SUM("Total") FROM "Venta"')
            self.assertEqual(cursor.fetchone(), (3, Decimal("49.00")))
            cursor.execute('SELECT COUNT(*) FROM "DetalleVenta"')
            self.assertEqual(cursor.fetchone()[0], 3)
        self._set_context("")

    def test_dashboard_has_exact_own_tenant_metrics(self):
        self._enter_restricted_role()
        response = self._get(
            self._client(self.admin_a), "/api/ventas/dashboard/stats/"
        )
        self.assertEqual(response.status_code, 200, response.data)
        data = response.json()
        self.assertEqual(data["totalProducts"], 4)
        self.assertEqual(Decimal(str(data["weeklySales"])), Decimal("42"))
        self.assertEqual(data["totalProveedores"], 1)
        self.assertEqual(Decimal(str(data["weeklyLosses"])), Decimal("10"))
        self.assertEqual(
            [(row["name"], row["stock"]) for row in data["lowStockItems"]],
            [(self.product_a.Nombre, 11)],
        )
        recent = {row["id"]: row for row in data["recentSales"]}
        self.assertEqual(
            set(recent), {self.sale_a.pk, self.annulled_a.pk, self.old_sale_a.pk}
        )
        self.assertEqual(recent[self.sale_a.pk]["items"], 1)
        self.assertEqual(
            sum(Decimal(str(row["total"])) for row in data["chartData"]),
            Decimal("42"),
        )

    def test_inventory_and_simple_reports_have_exact_own_results(self):
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        urls = self._report_urls()

        compras = self._get(client, urls["compras"])
        self.assertEqual(compras.status_code, 200, compras.data)
        self.assertEqual(
            [(row["nombre_proveedor"], Decimal(str(row["total_compra"]))) for row in compras.json()],
            [(self.provider_a.Nombre, Decimal("150"))],
        )
        sin_movimiento = self._get(client, urls["sin_movimiento"])
        self.assertEqual(sin_movimiento.status_code, 200, sin_movimiento.data)
        self.assertEqual(
            {
                (row["nombre_producto"], row["stock_actual"])
                for row in sin_movimiento.json()
            },
            {(self.stagnant_a.Nombre, 0), (self.optional_provider_a.Nombre, 0)},
        )
        gerencial = self._get(client, urls["gerencial"])
        self.assertEqual(gerencial.status_code, 200, gerencial.data)
        self.assertEqual(Decimal(str(gerencial.json()["total_ventas"])), Decimal("42"))
        self.assertEqual(Decimal(str(gerencial.json()["promedio_venta"])), Decimal("21"))
        self.assertEqual(gerencial.json()["producto_mas_vendido"], self.product_a.Nombre)

        pivot = self._get(client, urls["pivot"])
        self.assertEqual(pivot.status_code, 200, pivot.data)
        month_keys = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
        self.assertEqual(pivot.json()["producto"], self.product_a.Nombre)
        self.assertEqual(
            sum(Decimal(str(pivot.json()[key])) for key in month_keys),
            Decimal("42"),
        )
        proveedor = self._get(client, urls["proveedor"])
        self.assertEqual(proveedor.status_code, 200, proveedor.data)
        self.assertEqual(
            [(row["producto"], row["unidades_disponibles"]) for row in proveedor.json()],
            [(self.product_a.Nombre, 11)],
        )
        devoluciones = self._get(client, urls["devoluciones"])
        self.assertEqual(devoluciones.status_code, 200, devoluciones.data)
        self.assertEqual(
            [(row["producto"], row["cantidad"]) for row in devoluciones.json()],
            [(self.product_a.Nombre, 1)],
        )
        self.assertIsNone(devoluciones.json()[0]["usuario"])
        perdidas = self._get(client, urls["perdidas"])
        self.assertEqual(perdidas.status_code, 200, perdidas.data)
        self.assertEqual(
            [(row["producto"], row["cantidad"], Decimal(str(row["total"]))) for row in perdidas.json()],
            [(self.product_a.Nombre, 1, Decimal("10"))],
        )

    def test_advanced_reports_preserve_current_state_rules_and_filters(self):
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        urls = self._report_urls()

        ventas = self._get(client, urls["ventas"])
        self.assertEqual(ventas.status_code, 200, ventas.data)
        self.assertEqual(
            {(row["estado"], Decimal(str(row["total"]))) for row in ventas.json()},
            {("Completada", Decimal("12")), ("Anulada", Decimal("30"))},
        )
        completed = self._get(client, urls["ventas"] + "&estado=Completada")
        self.assertEqual(
            [(row["estado"], Decimal(str(row["total"]))) for row in completed.json()],
            [("Completada", Decimal("12"))],
        )
        top = self._get(client, urls["top"] + "&limite=1")
        self.assertEqual(top.status_code, 200, top.data)
        self.assertEqual(
            [(row["nombre_producto"], row["cantidad_vendida"], Decimal(str(row["ingreso_bruto"]))) for row in top.json()],
            [(self.product_a.Nombre, 2, Decimal("42"))],
        )
        ganancia = self._get(
            client, urls["ganancia"] + f"&productoId={self.product_a.pk}"
        )
        self.assertEqual(ganancia.status_code, 200, ganancia.data)
        self.assertEqual(
            [(Decimal(str(row["total_ventas"])), Decimal(str(row["costo_total"])), Decimal(str(row["ganancia_bruta"]))) for row in ganancia.json()],
            [(Decimal("42"), Decimal("20"), Decimal("22"))],
        )
        comparacion = self._get(client, urls["comparacion"])
        self.assertEqual(comparacion.status_code, 200, comparacion.data)
        self.assertEqual(
            comparacion.json(),
            [{"ventas_a": 12.0, "productos_a": 1, "ventas_b": 7.0, "productos_b": 1}],
        )

    def test_foreign_filters_and_validation_errors_reveal_no_data(self):
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        today = self.today.isoformat()
        empty_cases = (
            f"/api/inventario/reporte-compras-filtradas/?inicio={today}&fin={today}&proveedorId={self.provider_b.pk}",
            f"/api/ventas/productos-proveedor/?proveedorId={self.provider_b.pk}",
            f"/api/ventas/reporte-ganancia-producto/?inicio={today}&fin={today}&productoId={self.product_b.pk}",
        )
        for url in empty_cases:
            with self.subTest(url=url):
                response = self._get(client, url)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.json(), [])

        pivot = self._get(
            client,
            f"/api/ventas/reporte-pivot/?anio={self.today.year}&productoId={self.product_b.pk}",
        )
        self.assertEqual(pivot.status_code, 200, pivot.data)
        self.assertEqual(pivot.json()["producto"], "Sin datos")
        self.assertEqual(
            sum(Decimal(str(value)) for key, value in pivot.json().items() if key != "producto"),
            Decimal("0"),
        )

        invalid_cases = (
            "/api/inventario/reporte-compras-filtradas/",
            "/api/inventario/reporte-productos-sin-movimiento/?dias=no-es-entero",
            "/api/ventas/reporte-pivot/",
            "/api/ventas/productos-proveedor/",
            "/api/ventas/reporte-devoluciones/",
            "/api/ventas/reporte-perdidas/",
            "/api/ventas/reporte-ventas-filtradas/",
            f"/api/ventas/reporte-top-productos/?inicio={today}&fin={today}&limite=no-es-entero",
            "/api/ventas/reporte-ganancia-producto/",
            "/api/ventas/reporte-comparacion-ventas/",
        )
        for url in invalid_cases:
            with self.subTest(url=url):
                response = self._get(client, url)
                self.assertEqual(response.status_code, 400, response.data)

    def test_tenantless_admin_receives_safe_empty_contracts(self):
        self._enter_restricted_role()
        client = self._client(self.tenantless_admin)
        dashboard = self._get(client, "/api/ventas/dashboard/stats/")
        self.assertEqual(dashboard.status_code, 200, dashboard.data)
        self.assertEqual(dashboard.json()["totalProducts"], 0)
        self.assertEqual(dashboard.json()["weeklySales"], 0.0)
        self.assertEqual(dashboard.json()["recentSales"], [])

        for name, url in self._report_urls().items():
            with self.subTest(report=name):
                response = self._get(client, url)
                self.assertEqual(response.status_code, 200, response.data)
                if name == "gerencial":
                    self.assertEqual(response.json()["total_ventas"], 0)
                elif name == "pivot":
                    self.assertEqual(response.json()["producto"], "Sin datos")
                elif name == "comparacion":
                    self.assertEqual(
                        response.json(),
                        [{"ventas_a": 0.0, "productos_a": 0, "ventas_b": 0.0, "productos_b": 0}],
                    )
                else:
                    self.assertEqual(response.json(), [])


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DashboardReportApplicationFilterTests(
    DashboardReportFixtureMixin,
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    """Comprueba por separado los filtros de aplicación sin protección RLS."""

    reset_sequences = True
    maxDiff = None

    def setUp(self):
        super().setUp()
        self._create_report_fixture()

    def test_cross_tenant_relations_do_not_contaminate_application_only_results(self):
        client = APIClient()
        token = str(RefreshToken.for_user(self.admin_a).access_token)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        urls = self._report_urls()

        dashboard = client.get("/api/ventas/dashboard/stats/")
        top = client.get(urls["top"])
        losses = client.get(urls["perdidas"])
        returns = client.get(urls["devoluciones"])
        purchases = client.get(urls["compras"])
        providers = client.get(urls["proveedor"])
        no_movement = client.get(urls["sin_movimiento"])
        gain = client.get(
            urls["ganancia"] + f"&productoId={self.product_a.pk}"
        )
        comparison = client.get(urls["comparacion"])
        management = client.get(urls["gerencial"])
        recent = {row["id"]: row for row in dashboard.json()["recentSales"]}
        top_by_product = {
            row["nombre_producto"]: row for row in top.json()
        }

        actual = {
            "dashboard_items": recent[self.sale_a.pk]["items"],
            "low_stock_names": {
                row["name"] for row in dashboard.json()["lowStockItems"]
            },
            "top_products": {
                name: (
                    row["cantidad_vendida"],
                    Decimal(str(row["ingreso_bruto"])),
                )
                for name, row in top_by_product.items()
            },
            "gain": [
                (
                    Decimal(str(row["total_ventas"])),
                    Decimal(str(row["costo_total"])),
                    Decimal(str(row["ganancia_bruta"])),
                )
                for row in gain.json()
            ],
            "comparison_products": comparison.json()[0]["productos_a"],
            "management_top": management.json()["producto_mas_vendido"],
            "loss_quantity": losses.json()[0]["cantidad"],
            "return_quantity": returns.json()[0]["cantidad"],
            "return_user": returns.json()[0]["usuario"],
            "purchase_providers": {
                row["nombre_proveedor"] for row in purchases.json()
            },
            "provider_products": {
                row["producto"] for row in providers.json()
            },
            "no_movement": {
                row["nombre_producto"] for row in no_movement.json()
            },
        }
        self.assertEqual(
            actual,
            {
                "dashboard_items": 1,
                "low_stock_names": {self.product_a.Nombre},
                "top_products": {
                    self.product_a.Nombre: (2, Decimal("42")),
                },
                "gain": [(Decimal("42"), Decimal("20"), Decimal("22"))],
                "comparison_products": 1,
                "management_top": self.product_a.Nombre,
                "loss_quantity": 1,
                "return_quantity": 1,
                "return_user": None,
                "purchase_providers": {self.provider_a.Nombre},
                "provider_products": {self.product_a.Nombre},
                "no_movement": {
                    self.stagnant_a.Nombre,
                    self.optional_provider_a.Nombre,
                },
            },
        )
