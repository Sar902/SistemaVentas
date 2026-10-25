from datetime import timedelta
from decimal import Decimal
from threading import Event, Lock, Thread
from time import monotonic
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from auditoria.models import LogAuditoria
from auditoria.mixins import registrar_evento_manual as real_audit_event
from catalogo.models import Categoria, Producto, Proveedor
from inventario.models import DetalleEntradaInventario, EntradaInventario, Inventario
from usuarios.models import Miscelanea, Usuario
from usuarios.test_utils import TemporaryRLSNeutralizationMixin

from .models import DetalleVenta, Venta


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SaleCancellationT3Tests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Tenant anulacion A", Codigo="ANUL-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Tenant anulacion B", Codigo="ANUL-B"
        )
        self.admin_a = self._user(
            "admin-anul-a@example.test", "Admin anulacion A", self.tenant_a, "admin"
        )
        self.cashier_a = self._user(
            "cashier-anul-a@example.test", "Cajero anulacion A", self.tenant_a, "cajero"
        )
        self.admin_b = self._user(
            "admin-anul-b@example.test", "Admin anulacion B", self.tenant_b, "admin"
        )
        self.units_a = self._inventory_for(self.tenant_a, self.admin_a, "A", 7)
        self.units_b = self._inventory_for(self.tenant_b, self.admin_b, "B", 1)

    def _user(self, email, name, tenant, role):
        return Usuario.objects.create_user(
            Email=email,
            Password="A-strong-test-password-2026!",
            Nombre=name,
            Miscelanea=tenant,
            Rol=role,
            Estado="Activo",
        )

    def _inventory_for(self, tenant, user, suffix, quantity):
        category = Categoria.objects.create(
            Miscelanea=tenant,
            Nombre=f"Categoria anulacion {suffix}",
            PorcentajeGanancia=Decimal("20.00"),
        )
        provider = Proveedor.objects.create(
            Miscelanea=tenant, Nombre=f"Proveedor anulacion {suffix}"
        )
        product = Producto.objects.create(
            Miscelanea=tenant,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre=f"Producto anulacion {suffix}",
        )
        entry = EntradaInventario.objects.create(
            Miscelanea=tenant,
            IdProveedor=provider,
            IdUsuario=user,
            FechaEntrada=timezone.now(),
            Total=Decimal("70.00"),
        )
        detail = DetalleEntradaInventario.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdProducto=product,
            Cantidad=quantity,
            PrecioCompraUnitario=Decimal("10.00"),
        )
        return list(
            Inventario.objects.filter(IdDetalleEntrada=detail).order_by("IdInventario")
        )

    def _sale(self, units, estado="Completada"):
        sale = Venta.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.cashier_a,
            Fecha=timezone.now(),
            Total=Decimal("12.00") * len(units),
            Estado=estado,
        )
        old_movement = timezone.now() - timedelta(days=2)
        for unit in {item.pk: item for item in units}.values():
            unit.Estado = "Vendido"
            unit.FechaMovimiento = old_movement
            unit.save(update_fields=["Estado", "FechaMovimiento"])
        for unit in units:
            DetalleVenta.objects.create(
                Miscelanea=self.tenant_a,
                IdVenta=sale,
                IdInventario=unit,
                PrecioVentaUnitario=Decimal("12.00"),
            )
        return sale, old_movement

    def _url(self, sale):
        return f"/api/ventas/{sale.pk}/anular/"

    def _success_logs(self):
        return LogAuditoria.objects.filter(
            Miscelanea=self.tenant_a,
            accion="ANULAR",
            modulo="VENTAS",
            resultado="EXITOSO",
        )

    def test_valid_cancellation_restores_exact_stock_and_logs_once(self):
        sale, old_movement = self._sale(self.units_a[:2])
        unrelated = self.units_a[2]
        unrelated_state = unrelated.Estado
        unrelated_movement = unrelated.FechaMovimiento
        self.client.force_authenticate(self.admin_a)

        response = self.client.post(self._url(sale), {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"message": f"Venta #{sale.pk} anulada correctamente. Stock revertido."},
        )
        sale.refresh_from_db()
        self.assertEqual(sale.Estado, "Anulada")
        for unit in self.units_a[:2]:
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Disponible")
            self.assertGreater(unit.FechaMovimiento, old_movement)
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.Estado, unrelated_state)
        self.assertEqual(unrelated.FechaMovimiento, unrelated_movement)
        self.assertEqual(self._success_logs().count(), 1)

    def test_second_cancellation_is_rejected_without_additional_changes(self):
        sale, _ = self._sale(self.units_a[:2])
        self.client.force_authenticate(self.admin_a)
        first = self.client.post(self._url(sale), {}, format="json")
        states_after_first = {}
        for unit in self.units_a[:2]:
            unit.refresh_from_db()
            states_after_first[unit.pk] = (unit.Estado, unit.FechaMovimiento)

        second = self.client.post(self._url(sale), {}, format="json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        sale.refresh_from_db()
        self.assertEqual(sale.Estado, "Anulada")
        for unit in self.units_a[:2]:
            unit.refresh_from_db()
            self.assertEqual(
                (unit.Estado, unit.FechaMovimiento), states_after_first[unit.pk]
            )
        self.assertEqual(self._success_logs().count(), 1)

    def test_sale_not_completed_is_rejected_without_changes(self):
        sale, old_movement = self._sale(self.units_a[:1], estado="Pendiente")
        self.client.force_authenticate(self.admin_a)

        response = self.client.post(self._url(sale), {}, format="json")

        self.assertEqual(response.status_code, 400)
        sale.refresh_from_db()
        self.units_a[0].refresh_from_db()
        self.assertEqual(sale.Estado, "Pendiente")
        self.assertEqual(self.units_a[0].Estado, "Vendido")
        self.assertEqual(self.units_a[0].FechaMovimiento, old_movement)
        self.assertFalse(self._success_logs().exists())

    def test_unit_not_sold_rejects_and_rolls_back_everything(self):
        sale, old_movement = self._sale(self.units_a[:2])
        self.units_a[1].Estado = "Perdido"
        self.units_a[1].save(update_fields=["Estado"])
        self.client.force_authenticate(self.admin_a)

        response = self.client.post(self._url(sale), {}, format="json")

        self.assertEqual(response.status_code, 400)
        sale.refresh_from_db()
        for unit, expected in zip(self.units_a[:2], ("Vendido", "Perdido")):
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, expected)
            self.assertEqual(unit.FechaMovimiento, old_movement)
        self.assertEqual(sale.Estado, "Completada")
        self.assertFalse(self._success_logs().exists())

    def test_failure_after_first_inventory_write_rolls_back_completely(self):
        sale, old_movement = self._sale(self.units_a[:2])
        real_save = Inventario.save
        calls = 0

        def fail_on_second_save(instance, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("fallo interno controlado de anulacion")
            return real_save(instance, *args, **kwargs)

        self.client.force_authenticate(self.admin_a)
        self.client.raise_request_exception = False
        with patch("ventas.views.Inventario.save", autospec=True, side_effect=fail_on_second_save):
            response = self.client.post(self._url(sale), {}, format="json")

        sale.refresh_from_db()
        self.assertEqual(sale.Estado, "Completada")
        for unit in self.units_a[:2]:
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Vendido")
            self.assertEqual(unit.FechaMovimiento, old_movement)
        self.assertFalse(self._success_logs().exists())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "No se pudo anular la venta."})

    def test_concurrent_requests_effectively_cancel_only_once(self):
        sale, _ = self._sale(self.units_a[:2])
        first_at_audit = Event()
        release_first = Event()
        second_pid_ready = Event()
        poll_tick = Event()
        result_lock = Lock()
        audit_lock = Lock()
        results = {}
        errors = []
        pids = {}
        first_audit = True

        def blocking_audit(*args, **kwargs):
            nonlocal first_audit
            real_audit_event(*args, **kwargs)
            with audit_lock:
                must_block = first_audit
                first_audit = False
            if must_block:
                first_at_audit.set()
                if not release_first.wait(10):
                    raise TimeoutError("La primera anulacion no fue liberada a tiempo")

        def worker(label, pid_ready=None):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                with result_lock:
                    pids[label] = pid
                if pid_ready:
                    pid_ready.set()
                client = APIClient()
                client.force_authenticate(self.admin_a)
                response = client.post(self._url(sale), {}, format="json")
                with result_lock:
                    results[label] = response.status_code
            except BaseException as exc:
                with result_lock:
                    errors.append((label, repr(exc)))
            finally:
                close_old_connections()

        first_thread = Thread(target=worker, args=("first",), daemon=True)
        second_thread = Thread(
            target=worker, args=("second", second_pid_ready), daemon=True
        )
        lock_wait_observed = False
        with patch("ventas.views.registrar_evento_manual", side_effect=blocking_audit):
            try:
                first_thread.start()
                self.assertTrue(first_at_audit.wait(10))
                second_thread.start()
                self.assertTrue(second_pid_ready.wait(10))
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                            [pids["second"]],
                        )
                        row = cursor.fetchone()
                    if row and row[0] == "Lock":
                        lock_wait_observed = True
                        break
                    poll_tick.wait(0.02)
            finally:
                release_first.set()
                first_thread.join(10)
                second_thread.join(10)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(lock_wait_observed)
        self.assertEqual(sorted(results.values()), [200, 400])
        sale.refresh_from_db()
        self.assertEqual(sale.Estado, "Anulada")
        for unit in self.units_a[:2]:
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Disponible")
        self.assertEqual(self._success_logs().count(), 1)

    def test_sale_without_details_is_rejected_without_changes(self):
        sale = Venta.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.cashier_a,
            Fecha=timezone.now(),
            Total=Decimal("0.00"),
        )
        self.client.force_authenticate(self.admin_a)

        response = self.client.post(self._url(sale), {}, format="json")

        self.assertEqual(response.status_code, 400)
        sale.refresh_from_db()
        self.assertEqual(sale.Estado, "Completada")
        self.assertFalse(self._success_logs().exists())

    def test_ordinary_cashier_is_forbidden_without_changes(self):
        sale, old_movement = self._sale(self.units_a[:1])
        self.client.force_authenticate(self.cashier_a)

        response = self.client.post(self._url(sale), {}, format="json")

        self.assertEqual(response.status_code, 403)
        sale.refresh_from_db()
        self.units_a[0].refresh_from_db()
        self.assertEqual(sale.Estado, "Completada")
        self.assertEqual(self.units_a[0].Estado, "Vendido")
        self.assertEqual(self.units_a[0].FechaMovimiento, old_movement)
        self.assertFalse(self._success_logs().exists())

    def test_own_detail_with_foreign_inventory_is_controlled_and_unchanged(self):
        foreign_unit = self.units_b[0]
        foreign_unit.Estado = "Vendido"
        foreign_unit.save(update_fields=["Estado"])
        sale = Venta.objects.create(
            Miscelanea=self.tenant_a,
            IdUsuario=self.cashier_a,
            Fecha=timezone.now(),
            Total=Decimal("12.00"),
        )
        DetalleVenta.objects.create(
            Miscelanea=self.tenant_a,
            IdVenta=sale,
            IdInventario=foreign_unit,
            PrecioVentaUnitario=Decimal("12.00"),
        )
        self.client.force_authenticate(self.admin_a)
        self.client.raise_request_exception = False

        response = self.client.post(self._url(sale), {}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("matching query", str(response.json()).lower())
        sale.refresh_from_db()
        foreign_unit.refresh_from_db()
        self.assertEqual(sale.Estado, "Completada")
        self.assertEqual(foreign_unit.Estado, "Vendido")
        self.assertFalse(self._success_logs().exists())

    def test_duplicate_inventory_reference_is_rejected_without_changes(self):
        sale, old_movement = self._sale([self.units_a[0], self.units_a[0]])
        self.client.force_authenticate(self.admin_a)

        response = self.client.post(self._url(sale), {}, format="json")

        self.assertEqual(response.status_code, 400)
        sale.refresh_from_db()
        self.units_a[0].refresh_from_db()
        self.assertEqual(sale.Estado, "Completada")
        self.assertEqual(self.units_a[0].Estado, "Vendido")
        self.assertEqual(self.units_a[0].FechaMovimiento, old_movement)
        self.assertFalse(self._success_logs().exists())
