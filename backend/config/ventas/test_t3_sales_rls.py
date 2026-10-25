from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from catalogo.models import Categoria, Producto, Proveedor
from inventario.models import DetalleEntradaInventario, EntradaInventario, Inventario
from usuarios.models import Miscelanea, Usuario
from usuarios.test_utils import RLS_TEST_ROLE, RestrictedRLSRoleMixin

from .models import DetalleVenta, Venta


RLS_SALE_TABLES = ("Venta", "DetalleVenta")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class VentasRLSRealRoleTests(
    RestrictedRLSRoleMixin,
    APITransactionTestCase,
):
    """Valida ventas bajo un rol no propietario y realmente sujeto a RLS."""

    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="RLS ventas A", Codigo="RLS-VTA-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="RLS ventas B", Codigo="RLS-VTA-B"
        )
        self.admin_a = self._user("rls-sales-admin-a@example.test", self.tenant_a)
        self.admin_b = self._user("rls-sales-admin-b@example.test", self.tenant_b)
        self.no_tenant = self._user("rls-sales-no-tenant@example.test", None)
        self.data_a = self._inventory_domain(self.tenant_a, self.admin_a, "A", ("10.00", "20.00"))
        self.data_b = self._inventory_domain(self.tenant_b, self.admin_b, "B", ("100.00",))
        self.sale_a = self._sale(
            self.tenant_a, self.admin_a, self.data_a["units"][0], Decimal("12.00")
        )
        self.sale_b = self._sale(
            self.tenant_b, self.admin_b, self.data_b["units"][0], Decimal("120.00")
        )
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
    def _inventory_domain(tenant, user, suffix, costs):
        category = Categoria.objects.create(
            Miscelanea=tenant,
            Nombre=f"Categoria RLS ventas {suffix}",
            PorcentajeGanancia=Decimal("20.00"),
        )
        provider = Proveedor.objects.create(
            Miscelanea=tenant, Nombre=f"Proveedor RLS ventas {suffix}"
        )
        entry = EntradaInventario.objects.create(
            Miscelanea=tenant,
            IdProveedor=provider,
            IdUsuario=user,
            FechaEntrada=timezone.now(),
            Total=sum((Decimal(cost) * 3 for cost in costs), Decimal("0.00")),
        )
        products = []
        details = []
        units = []
        for index, cost in enumerate(costs, start=1):
            product = Producto.objects.create(
                Miscelanea=tenant,
                IdCategoria=category,
                IdProveedor=provider,
                Nombre=f"Producto RLS ventas {suffix}-{index}",
            )
            detail = DetalleEntradaInventario.objects.create(
                Miscelanea=tenant,
                IdEntradaInventario=entry,
                IdProducto=product,
                Cantidad=3,
                PrecioCompraUnitario=Decimal(cost),
            )
            products.append(product)
            details.append(detail)
            units.extend(detail.inventarios.order_by("IdInventario"))
        return {
            "category": category,
            "entry": entry,
            "products": products,
            "details": details,
            "units": units,
        }

    @staticmethod
    def _sale(tenant, user, unit, price):
        unit.Estado = "Vendido"
        unit.save(update_fields=["Estado"])
        sale = Venta.objects.create(
            Miscelanea=tenant,
            IdUsuario=user,
            Fecha=timezone.now(),
            Total=price,
            Estado="Completada",
        )
        detail = DetalleVenta.objects.create(
            Miscelanea=tenant,
            IdVenta=sale,
            IdInventario=unit,
            PrecioVentaUnitario=price,
        )
        return sale, detail, unit

    def test_direct_queries_prove_sale_rls_for_restricted_role(self):
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
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema() AND c.relname = ANY(%s)
                ORDER BY c.relname
                """,
                [list(RLS_SALE_TABLES)],
            )
            self.assertEqual(
                cursor.fetchall(),
                sorted((table, True, True) for table in RLS_SALE_TABLES),
            )

        self.assertIsNone(self._raw_context())
        with connection.cursor() as cursor:
            for table in RLS_SALE_TABLES:
                cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                self.assertEqual(cursor.fetchone()[0], 0, table)

        self._set_context("")
        with connection.cursor() as cursor:
            for table in RLS_SALE_TABLES:
                cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                self.assertEqual(cursor.fetchone()[0], 0, table)

        self._set_context(self.tenant_a.pk)
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT "IdVenta" FROM "Venta" ORDER BY "IdVenta"'
            )
            self.assertEqual(cursor.fetchall(), [(self.sale_a[0].pk,)])
            cursor.execute(
                'SELECT "IdDetalleVenta" FROM "DetalleVenta" '
                'ORDER BY "IdDetalleVenta"'
            )
            self.assertEqual(cursor.fetchall(), [(self.sale_a[1].pk,)])
            cursor.execute(
                'SELECT COUNT(*) FROM "Venta" WHERE "IdVenta" = %s',
                [self.sale_b[0].pk],
            )
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_http_reads_are_tenant_scoped_and_clear_context(self):
        self._enter_restricted_role()
        client = self._client(self.admin_a)

        own_sale = client.get(f"/api/ventas/ventas/{self.sale_a[0].pk}/")
        foreign_sale = client.get(f"/api/ventas/ventas/{self.sale_b[0].pk}/")
        own_detail = client.get(f"/api/ventas/detalles/{self.sale_a[1].pk}/")
        foreign_detail = client.get(
            f"/api/ventas/detalles/{self.sale_b[1].pk}/"
        )

        self.assertEqual(own_sale.status_code, 200, own_sale.data)
        self.assertEqual(own_detail.status_code, 200, own_detail.data)
        self.assertEqual(foreign_sale.status_code, 404, foreign_sale.data)
        self.assertEqual(foreign_detail.status_code, 404, foreign_detail.data)
        self.assertEqual(self._context(), "")

    def test_valid_sale_uses_authenticated_identity_and_server_amounts(self):
        unit_10 = self.data_a["units"][1]
        unit_20 = self.data_a["units"][3]
        self._enter_restricted_role()

        response = self._client(self.admin_a).post(
            "/api/ventas/procesar/",
            {
                "usuarioId": self.admin_b.pk,
                "fecha": timezone.now().isoformat(),
                "total": "999.99",
                "detalles": [
                    {"inventarioId": unit_10.pk, "precioVentaUnitario": "0.01"},
                    {"inventarioId": unit_20.pk, "precioVentaUnitario": "0.01"},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(str(response.json()["total"])), Decimal("36.00"))
        self.assertEqual(self._context(), "")
        self._set_context(self.tenant_a.pk)
        sale = Venta.objects.get(pk=response.json()["id"])
        self.assertEqual(sale.IdUsuario_id, self.admin_a.pk)
        self.assertEqual(sale.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(sale.Total, Decimal("36.00"))
        self.assertEqual(
            list(
                sale.detalles.order_by("IdDetalleVenta").values_list(
                    "Miscelanea_id", "PrecioVentaUnitario"
                )
            ),
            [
                (self.tenant_a.pk, Decimal("12.00")),
                (self.tenant_a.pk, Decimal("24.00")),
            ],
        )
        unit_10.refresh_from_db()
        unit_20.refresh_from_db()
        self.assertEqual((unit_10.Estado, unit_20.Estado), ("Vendido", "Vendido"))

    def test_foreign_inventory_is_rejected_without_writes_or_stock_change(self):
        foreign_unit = self.data_b["units"][1]
        before = (Venta.objects.count(), DetalleVenta.objects.count(), foreign_unit.Estado)
        self._enter_restricted_role()

        response = self._client(self.admin_a).post(
            "/api/ventas/procesar/",
            {
                "fecha": timezone.now().isoformat(),
                "detalles": [{"inventarioId": foreign_unit.pk}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        foreign_unit.refresh_from_db()
        self.assertEqual(
            (Venta.objects.count(), DetalleVenta.objects.count(), foreign_unit.Estado),
            before,
        )

    def test_foreign_sale_read_and_cancellation_are_not_visible(self):
        before = (self.sale_b[0].Estado, self.sale_b[2].Estado)
        self._enter_restricted_role()
        client = self._client(self.admin_a)

        read = client.get(f"/api/ventas/ventas/{self.sale_b[0].pk}/")
        cancel = client.post(
            f"/api/ventas/{self.sale_b[0].pk}/anular/", {}, format="json"
        )

        self.assertEqual(read.status_code, 404, read.data)
        self.assertEqual(cancel.status_code, 404, cancel.data)
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.sale_b[0].refresh_from_db()
        self.sale_b[2].refresh_from_db()
        self.assertEqual((self.sale_b[0].Estado, self.sale_b[2].Estado), before)

    def test_own_cancellation_restores_once_and_second_attempt_has_no_effect(self):
        self._enter_restricted_role()
        client = self._client(self.admin_a)

        first = client.post(
            f"/api/ventas/{self.sale_a[0].pk}/anular/", {}, format="json"
        )
        second = client.post(
            f"/api/ventas/{self.sale_a[0].pk}/anular/", {}, format="json"
        )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 400, second.data)
        self.assertEqual(self._context(), "")
        self._set_context(self.tenant_a.pk)
        self.sale_a[0].refresh_from_db()
        self.sale_a[2].refresh_from_db()
        self.assertEqual(self.sale_a[0].Estado, "Anulada")
        self.assertEqual(self.sale_a[2].Estado, "Disponible")

    def test_sale_failure_after_first_detail_rolls_back_completely(self):
        units = [self.data_a["units"][1], self.data_a["units"][2]]
        before = (Venta.objects.count(), DetalleVenta.objects.count())
        real_create = DetalleVenta.objects.create
        calls = 0

        def fail_on_second_detail(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("fallo controlado RLS venta")
            return real_create(**kwargs)

        self._enter_restricted_role()
        with patch(
            "ventas.views.DetalleVenta.objects.create",
            side_effect=fail_on_second_detail,
        ):
            response = self._client(self.admin_a).post(
                "/api/ventas/procesar/",
                {
                    "fecha": timezone.now().isoformat(),
                    "detalles": [{"inventarioId": unit.pk} for unit in units],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(calls, 2)
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertEqual((Venta.objects.count(), DetalleVenta.objects.count()), before)
        for unit in units:
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Disponible")

    def test_cancellation_failure_after_stock_write_rolls_back_completely(self):
        extra_unit = self.data_a["units"][1]
        extra_unit.Estado = "Vendido"
        extra_unit.save(update_fields=["Estado"])
        DetalleVenta.objects.create(
            Miscelanea=self.tenant_a,
            IdVenta=self.sale_a[0],
            IdInventario=extra_unit,
            PrecioVentaUnitario=Decimal("12.00"),
        )
        real_save = Inventario.save
        calls = 0

        def fail_on_second_stock_write(instance, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("fallo controlado RLS anulación")
            return real_save(instance, *args, **kwargs)

        self._enter_restricted_role()
        with patch(
            "ventas.views.Inventario.save",
            autospec=True,
            side_effect=fail_on_second_stock_write,
        ):
            response = self._client(self.admin_a).post(
                f"/api/ventas/{self.sale_a[0].pk}/anular/", {}, format="json"
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(calls, 2)
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.sale_a[0].refresh_from_db()
        self.sale_a[2].refresh_from_db()
        extra_unit.refresh_from_db()
        self.assertEqual(self.sale_a[0].Estado, "Completada")
        self.assertEqual((self.sale_a[2].Estado, extra_unit.Estado), ("Vendido", "Vendido"))

    def test_tenantless_guards_hold_under_restricted_role_and_clear_context(self):
        self._enter_restricted_role()
        client = self._client(self.no_tenant)

        sales = client.get("/api/ventas/ventas/")
        details = client.get("/api/ventas/detalles/")
        process = client.post(
            "/api/ventas/procesar/",
            {"detalles": [{"inventarioId": self.data_a["units"][1].pk}]},
            format="json",
        )
        cancel = client.post(
            f"/api/ventas/{self.sale_a[0].pk}/anular/", {}, format="json"
        )

        self.assertEqual(sales.status_code, 200, sales.data)
        self.assertEqual(sales.json(), [])
        self.assertEqual(details.status_code, 200, details.data)
        self.assertEqual(details.json()["results"], [])
        self.assertEqual(process.status_code, 403, process.data)
        self.assertEqual(cancel.status_code, 403, cancel.data)
        self.assertEqual(self._context(), "")
