from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone

from catalogo.models import Categoria, Producto
from inventario.models import DetalleEntradaInventario, EntradaInventario, Inventario
from usuarios.test_t1_isolation import ApplicationIsolationTestCase

from .models import DetalleVenta, Venta


class SalePriceAndTransactionT2Tests(ApplicationIsolationTestCase):
    """T2: current regressions plus two explicit T3 business requirements."""

    def sale_payload(self, inventory_ids, *, user_id=None, total="0.01", price="0.01"):
        return {
            "usuarioId": user_id or self.cashier_a.pk,
            "fecha": timezone.now().isoformat(),
            "total": total,
            "detalles": [
                {"inventarioId": inventory_id, "precioVentaUnitario": price}
                for inventory_id in inventory_ids
            ],
        }

    def fresh_inventory(self, quantity=1, cost="10.00", product=None):
        product = product or self.product_a
        detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=self.entry_a,
            IdProducto=product,
            Cantidad=quantity,
            PrecioCompraUnitario=Decimal(cost),
        )
        return list(
            Inventario.objects.filter(IdDetalleEntrada=detail).order_by("IdInventario")
        )

    def assert_sale_rollback(self, before_counts, units, original_states):
        self.assertEqual(
            (Venta.objects.count(), DetalleVenta.objects.count()),
            before_counts,
        )
        for unit in units:
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, original_states[unit.pk])

    # A: regression of the current catalog contract.
    def test_product_without_purchases_exposes_integer_zero_sale_price(self):
        product = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Producto sin compras T2",
        )
        self.authenticate(self.admin_a)

        response = self.client.get("/api/catalogo/productos/")

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json() if item["id"] == product.pk)
        self.assertEqual(row["salePrice"], 0)
        self.assertIs(type(row["salePrice"]), int)

    # A: higher detail ID wins even when its purchase date is older; preserve round().
    def test_sale_price_uses_highest_detail_id_and_current_rounding(self):
        category = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoria precio T2",
            PorcentajeGanancia=Decimal("25.00"),
        )
        by_id = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=category,
            IdProveedor=self.provider_a,
            Nombre="Precio por ID T2",
        )
        rounding_up = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=category,
            IdProveedor=self.provider_a,
            Nombre="Redondeo superior T2",
        )
        recent_entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdProveedor=self.provider_a,
            IdUsuario=self.admin_a,
            FechaEntrada=timezone.now(),
            Total=Decimal("100.00"),
        )
        recent_detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=recent_entry,
            IdProducto=by_id,
            Cantidad=1,
            PrecioCompraUnitario=Decimal("100.00"),
        )
        older_entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdProveedor=self.provider_a,
            IdUsuario=self.admin_a,
            FechaEntrada=timezone.now() - timedelta(days=30),
            Total=Decimal("24.00"),
        )
        higher_id_detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=older_entry,
            IdProducto=by_id,
            Cantidad=1,
            PrecioCompraUnitario=Decimal("10.00"),
        )
        DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdEntradaInventario=older_entry,
            IdProducto=rounding_up,
            Cantidad=1,
            PrecioCompraUnitario=Decimal("14.00"),
        )
        self.assertGreater(higher_id_detail.pk, recent_detail.pk)
        self.assertLess(older_entry.FechaEntrada, recent_entry.FechaEntrada)
        self.authenticate(self.admin_a)

        response = self.client.get("/api/catalogo/productos/")

        self.assertEqual(response.status_code, 200)
        rows = {item["id"]: item for item in response.json()}
        self.assertEqual(rows[by_id.pk]["salePrice"], 12)
        self.assertEqual(rows[rounding_up.pk]["salePrice"], 18)
        self.assertIs(type(rows[by_id.pk]["salePrice"]), int)
        self.assertIs(type(rows[rounding_up.pk]["salePrice"]), int)

    # A: existing availability guard must roll back earlier writes.
    def test_unavailable_unit_rejects_and_rolls_back_complete_sale(self):
        units = self.fresh_inventory(quantity=2)
        units[1].Estado = "Perdido"
        units[1].save(update_fields=["Estado"])
        original = {unit.pk: unit.Estado for unit in units}
        before = (Venta.objects.count(), DetalleVenta.objects.count())
        self.authenticate(self.cashier_a)

        response = self.client.post(
            "/api/ventas/procesar/",
            self.sale_payload([unit.pk for unit in units]),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assert_sale_rollback(before, units, original)

    # A: a missing ID after one valid unit must not leave partial writes.
    def test_missing_inventory_after_valid_unit_rolls_back_complete_sale(self):
        units = self.fresh_inventory(quantity=1)
        missing_id = Inventario.objects.order_by("-IdInventario").first().pk + 1000
        original = {units[0].pk: units[0].Estado}
        before = (Venta.objects.count(), DetalleVenta.objects.count())
        self.authenticate(self.cashier_a)

        response = self.client.post(
            "/api/ventas/procesar/",
            self.sale_payload([units[0].pk, missing_id]),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assert_sale_rollback(before, units, original)

    # A: repeated physical units are rejected by the current state guard.
    def test_repeated_inventory_id_rejects_without_changes(self):
        units = self.fresh_inventory(quantity=1)
        original = {units[0].pk: units[0].Estado}
        before = (Venta.objects.count(), DetalleVenta.objects.count())
        self.authenticate(self.cashier_a)

        response = self.client.post(
            "/api/ventas/procesar/",
            self.sale_payload([units[0].pk, units[0].pk]),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assert_sale_rollback(before, units, original)

    # A: an internal failure after the first detail must roll back all writes.
    def test_controlled_failure_after_first_detail_rolls_back_complete_sale(self):
        units = self.fresh_inventory(quantity=2)
        original = {unit.pk: unit.Estado for unit in units}
        before = (Venta.objects.count(), DetalleVenta.objects.count())
        real_create = DetalleVenta.objects.create
        calls = 0

        def create_with_failure(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("fallo controlado de detalle de venta")
            return real_create(**kwargs)

        self.authenticate(self.cashier_a)
        with patch(
            "ventas.views.DetalleVenta.objects.create",
            side_effect=create_with_failure,
        ):
            response = self.client.post(
                "/api/ventas/procesar/",
                self.sale_payload([unit.pk for unit in units]),
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assert_sale_rollback(before, units, original)

    # B: T3 requirement. The authenticated user, not usuarioId, owns the sale.
    def test_sale_is_attributed_to_authenticated_user(self):
        unit = self.fresh_inventory(quantity=1)[0]
        self.authenticate(self.cashier_a)

        response = self.client.post(
            "/api/ventas/procesar/",
            self.sale_payload([unit.pk], user_id=self.admin_a.pk, total="12.00", price="12.00"),
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        sale = Venta.objects.get(pk=response.json()["id"])
        self.assertEqual(sale.IdUsuario_id, self.cashier_a.pk)

    def test_sale_ignores_omitted_and_nonexistent_user_id(self):
        missing_id = self.cashier_b.pk + 100000
        for label, supplied_user_id in (("omitted", None), ("nonexistent", missing_id)):
            with self.subTest(usuario_id=label):
                unit = self.fresh_inventory(quantity=1)[0]
                payload = self.sale_payload([unit.pk], user_id=supplied_user_id)
                if supplied_user_id is None:
                    payload.pop("usuarioId")
                self.authenticate(self.cashier_a)

                response = self.client.post(
                    "/api/ventas/procesar/", payload, format="json"
                )

                self.assertEqual(response.status_code, 201)
                sale = Venta.objects.get(pk=response.json()["id"])
                self.assertEqual(sale.IdUsuario_id, self.cashier_a.pk)

    def test_sale_calculates_omitted_total_and_unit_price(self):
        unit = self.fresh_inventory(quantity=1, cost="10.00")[0]
        payload = self.sale_payload([unit.pk])
        payload.pop("total")
        payload["detalles"][0].pop("precioVentaUnitario")
        self.authenticate(self.cashier_a)

        response = self.client.post(
            "/api/ventas/procesar/", payload, format="json"
        )

        self.assertEqual(response.status_code, 201)
        sale = Venta.objects.get(pk=response.json()["id"])
        detail = sale.detalles.get()
        self.assertEqual(sale.Total, Decimal("12.00"))
        self.assertEqual(detail.PrecioVentaUnitario, Decimal("12.00"))
        self.assertEqual(Decimal(str(response.json()["total"])), Decimal("12.00"))

    # B: T3 requirement. Persisted and returned amounts come from server pricing.
    def test_sale_recalculates_total_and_prices_in_backend(self):
        other_product = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Segundo producto para suma T3",
        )
        units = [
            self.fresh_inventory(quantity=1, cost="10.00")[0],
            self.fresh_inventory(
                quantity=1, cost="20.00", product=other_product
            )[0],
        ]
        self.authenticate(self.cashier_a)

        response = self.client.post(
            "/api/ventas/procesar/",
            self.sale_payload(
                [unit.pk for unit in units],
                total="999999.99",
                price="0.01",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        sale = Venta.objects.get(pk=response.json()["id"])
        actual = (
            sale.Total,
            list(
                sale.detalles.order_by("IdDetalleVenta").values_list(
                    "PrecioVentaUnitario", flat=True
                )
            ),
            Decimal(str(response.json()["total"])),
        )
        expected = (
            Decimal("36.00"),
            [Decimal("12.00"), Decimal("24.00")],
            Decimal("36.00"),
        )
        self.assertEqual(actual, expected)
