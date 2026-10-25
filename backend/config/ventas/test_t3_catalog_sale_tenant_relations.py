from decimal import Decimal

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase
from rest_framework_simplejwt.tokens import RefreshToken

from auditoria.models import LogAuditoria
from catalogo.models import Categoria, Producto, Proveedor
from inventario.models import (
    DetalleEntradaInventario,
    EntradaInventario,
    Inventario,
)
from usuarios.models import Miscelanea, Usuario
from usuarios.test_utils import (
    RestrictedRLSRoleMixin,
    TemporaryRLSNeutralizationMixin,
)

from .models import DetalleVenta, Venta


class CatalogSaleTenantRelationFixtureMixin:
    def _create_relation_fixture(self):
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Relaciones venta A", Codigo="REL-VTA-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Relaciones venta B", Codigo="REL-VTA-B"
        )
        self.admin_a = self._user("relations-a@example.test", self.tenant_a)
        self.admin_b = self._user("relations-b@example.test", self.tenant_b)
        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoría relaciones A",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Categoría relaciones B",
            PorcentajeGanancia=Decimal("80.00"),
        )
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor relaciones A"
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor relaciones B"
        )
        self.isolated_provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor aislado A"
        )
        self.product_a = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Producto relaciones A",
        )
        self.product_b = Producto.objects.create(
            Miscelanea=self.tenant_b,
            IdCategoria=self.category_b,
            IdProveedor=self.provider_b,
            Nombre="Producto relaciones B",
        )
        self.cross_category_product_a = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_b,
            IdProveedor=self.provider_a,
            Nombre="Producto A con categoría B",
        )
        Producto.objects.create(
            Miscelanea=self.tenant_b,
            IdCategoria=self.category_a,
            IdProveedor=self.isolated_provider_a,
            Nombre="Producto B con relaciones A",
        )

        self.entry_a = EntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdProveedor=self.provider_a,
            IdUsuario=self.admin_a,
            FechaEntrada=timezone.now(),
            Total=Decimal("20.00"),
        )
        self.entry_b = EntradaInventario.objects.create(
            Miscelanea=self.tenant_b,
            IdProveedor=self.provider_b,
            IdUsuario=self.admin_b,
            FechaEntrada=timezone.now(),
            Total=Decimal("300.00"),
        )
        self.baseline_detail = self._detail(
            self.tenant_a, self.entry_a, self.product_a, 2, "10.00"
        )
        self.foreign_detail = self._detail(
            self.tenant_b, self.entry_b, self.product_b, 1, "100.00"
        )
        self._detail(
            self.tenant_b, self.entry_b, self.product_a, 3, "99.00"
        )
        self.foreign_product_detail = self._detail(
            self.tenant_a, self.entry_a, self.product_b, 1, "55.00"
        )
        self.foreign_entry_detail = self._detail(
            self.tenant_a, self.entry_b, self.product_a, 1, "77.00"
        )
        foreign_provider_entry_a = EntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdProveedor=self.provider_b,
            IdUsuario=self.admin_a,
            FechaEntrada=timezone.now(),
            Total=Decimal("66.00"),
        )
        self.foreign_provider_detail = self._detail(
            self.tenant_a,
            foreign_provider_entry_a,
            self.product_a,
            1,
            "66.00",
        )
        self.cross_category_detail = self._detail(
            self.tenant_a,
            self.entry_a,
            self.cross_category_product_a,
            1,
            "10.00",
        )
        cross_provider_entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant_b,
            IdProveedor=self.isolated_provider_a,
            IdUsuario=self.admin_b,
            FechaEntrada=timezone.now(),
            Total=Decimal("1.00"),
        )
        self._detail(
            self.tenant_b, cross_provider_entry, self.product_b, 1, "1.00"
        )
        self.foreign_detail_unit_a = Inventario.objects.create(
            Miscelanea=self.tenant_a,
            IdDetalleEntrada=self.foreign_detail,
            Estado="Disponible",
        )

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
    def _detail(tenant, entry, product, quantity, cost):
        return DetalleEntradaInventario.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=quantity,
            PrecioCompraUnitario=Decimal(cost),
        )

    @staticmethod
    def _token(user):
        return str(RefreshToken.for_user(user).access_token)

    def _client_for(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._token(user)}")
        return client

    @staticmethod
    def _unit(detail):
        return detail.inventarios.order_by("IdInventario").first()

    def _assert_sale_rejected_without_changes(self, unit, client):
        before = (Venta.objects.count(), DetalleVenta.objects.count())
        success_logs = LogAuditoria.objects.filter(
            accion="PROCESAR", modulo="VENTAS", resultado="EXITOSO"
        ).count()

        response = client.post(
            "/api/ventas/procesar/",
            {
                "fecha": timezone.now().isoformat(),
                "detalles": [{"inventarioId": unit.pk}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            response.json(),
            {"error": "La unidad seleccionada tiene relaciones inconsistentes."},
        )
        self.assertEqual(
            (Venta.objects.count(), DetalleVenta.objects.count()), before
        )
        unit.refresh_from_db()
        self.assertEqual(unit.Estado, "Disponible")
        self.assertEqual(
            LogAuditoria.objects.filter(
                accion="PROCESAR", modulo="VENTAS", resultado="EXITOSO"
            ).count(),
            success_logs,
        )


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CatalogSaleApplicationRelationTests(
    CatalogSaleTenantRelationFixtureMixin,
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self._create_relation_fixture()
        self.client = self._client_for(self.admin_a)

    def test_category_count_excludes_foreign_products_without_rls(self):
        category = self.client.get(f"/api/catalogo/categorias/{self.category_a.pk}/")
        self.assertEqual(category.status_code, 200, category.data)
        self.assertEqual(category.json()["productCount"], 1)

    def test_provider_calculations_exclude_foreign_relations_without_rls(self):
        provider = self.client.get(
            f"/api/catalogo/proveedores/{self.isolated_provider_a.pk}/"
        )
        self.assertEqual(provider.status_code, 200, provider.data)
        self.assertEqual(provider.json()["pedidos_recientes"], 0)
        self.assertIs(provider.json()["activo"], False)
        self.assertIs(provider.json()["tiene_productos"], False)

    def test_product_stock_and_price_exclude_foreign_relations_without_rls(self):
        product = self.client.get(f"/api/catalogo/productos/{self.product_a.pk}/")
        self.assertEqual(product.status_code, 200, product.data)
        self.assertEqual(product.json()["stock"], 2)
        self.assertEqual(product.json()["salePrice"], 12)

    def test_product_with_foreign_category_has_no_calculated_price_without_rls(self):
        cross_category = self.client.get(
            f"/api/catalogo/productos/{self.cross_category_product_a.pk}/"
        )
        self.assertEqual(cross_category.status_code, 200, cross_category.data)
        self.assertEqual(cross_category.json()["stock"], 1)
        self.assertEqual(cross_category.json()["salePrice"], 0)

    def test_sale_keeps_coherent_price_and_stock_behavior_without_rls(self):
        unit = self._unit(self.baseline_detail)
        response = self.client.post(
            "/api/ventas/procesar/",
            {
                "fecha": timezone.now().isoformat(),
                "detalles": [{"inventarioId": unit.pk}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        sale = Venta.objects.get(pk=response.json()["id"])
        self.assertEqual(sale.Total, Decimal("12.00"))
        self.assertEqual(
            sale.detalles.get().PrecioVentaUnitario, Decimal("12.00")
        )
        unit.refresh_from_db()
        self.assertEqual(unit.Estado, "Vendido")

    def test_sale_rejects_unit_linked_to_foreign_detail_without_rls(self):
        self._assert_sale_rejected_without_changes(
            self.foreign_detail_unit_a, self.client
        )

    def test_sale_rejects_detail_linked_to_foreign_product_without_rls(self):
        self._assert_sale_rejected_without_changes(
            self._unit(self.foreign_product_detail), self.client
        )

    def test_sale_rejects_detail_linked_to_foreign_entry_without_rls(self):
        self._assert_sale_rejected_without_changes(
            self._unit(self.foreign_entry_detail), self.client
        )

    def test_sale_rejects_entry_linked_to_foreign_provider_without_rls(self):
        self._assert_sale_rejected_without_changes(
            self._unit(self.foreign_provider_detail), self.client
        )

    def test_sale_rejects_product_linked_to_foreign_category_without_rls(self):
        self._assert_sale_rejected_without_changes(
            self._unit(self.cross_category_detail), self.client
        )


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CatalogSaleRestrictedRoleRelationTests(
    CatalogSaleTenantRelationFixtureMixin,
    RestrictedRLSRoleMixin,
    APITransactionTestCase,
):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self._create_relation_fixture()
        self._assert_role_is_prepared()

    def test_product_calculations_validate_relations_under_restricted_role(self):
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        product = client.get(f"/api/catalogo/productos/{self.product_a.pk}/")
        self.assertEqual(product.status_code, 200, product.data)
        self.assertEqual(product.json()["stock"], 2)
        self.assertEqual(product.json()["salePrice"], 12)
        self.assertEqual(self._context(), "")

    def test_foreign_category_is_safe_under_restricted_role(self):
        self._enter_restricted_role()
        response = self._client(self.admin_a).get(
            f"/api/catalogo/productos/{self.cross_category_product_a.pk}/"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json()["salePrice"], 0)
        self.assertEqual(self._context(), "")

    def test_sale_rejects_visible_units_with_foreign_purchase_relations_under_rls(self):
        units = (
            self._unit(self.foreign_entry_detail),
            self._unit(self.foreign_provider_detail),
        )
        before = (Venta.objects.count(), DetalleVenta.objects.count())
        success_logs = LogAuditoria.objects.filter(
            accion="PROCESAR", modulo="VENTAS", resultado="EXITOSO"
        ).count()
        self._enter_restricted_role()

        client = self._client(self.admin_a)
        for unit in units:
            response = client.post(
                "/api/ventas/procesar/",
                {
                    "fecha": timezone.now().isoformat(),
                    "detalles": [{"inventarioId": unit.pk}],
                },
                format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(
                response.json(),
                {"error": "La unidad seleccionada tiene relaciones inconsistentes."},
            )
            self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertEqual(
            (Venta.objects.count(), DetalleVenta.objects.count()), before
        )
        for unit in units:
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Disponible")
        self.assertEqual(
            LogAuditoria.objects.filter(
                accion="PROCESAR", modulo="VENTAS", resultado="EXITOSO"
            ).count(),
            success_logs,
        )
