import sys
from decimal import Decimal
from threading import Barrier, BrokenBarrierError, Event, Lock, Thread, current_thread
from time import monotonic
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.db.models.query import QuerySet
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from auditoria.mixins import registrar_evento_manual as real_audit_event
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
from usuarios.models import Miscelanea, Usuario
from usuarios.test_utils import RLS_TEST_ROLE, RestrictedRLSRoleMixin
from ventas.models import DetalleVenta, Venta
from ventas.views import _preleer_referencias_venta as real_preleer_venta


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MultiUnitLockOrderTests(RestrictedRLSRoleMixin, APITransactionTestCase):
    """Reproduce ciclos de bloqueo sin alterar el orden de las operaciones."""

    def setUp(self):
        super().setUp()
        self.tenant = Miscelanea.objects.create(
            Nombre="Concurrencia multiunidad", Codigo="CONC-MULTI"
        )
        self.admin = self._user("multi-admin@example.test", "admin")
        self.cashier_a = self._user("multi-cashier-a@example.test", "cajero")
        self.cashier_b = self._user("multi-cashier-b@example.test", "cajero")
        self.unit_low = self._single_unit_chain("A", Decimal("10.00"))
        self.unit_high = self._single_unit_chain("B", Decimal("20.00"))
        self.delete_entry, self.delete_detail, self.delete_units = (
            self._multi_unit_purchase("DELETE", Decimal("30.00"))
        )
        self._assert_role_is_prepared()

    def _user(self, email, role):
        return Usuario.objects.create_user(
            Email=email,
            Password="A-strong-test-password-2026!",
            Nombre=email.split("@")[0],
            Miscelanea=self.tenant,
            Rol=role,
            Estado="Activo",
        )

    def _single_unit_chain(self, suffix, cost):
        entry, _, units = self._purchase(suffix, cost, 1)
        self.assertIsNotNone(entry.pk)
        return units[0]

    def _multi_unit_purchase(self, suffix, cost):
        return self._purchase(suffix, cost, 2)

    def _purchase(self, suffix, cost, quantity):
        category = Categoria.objects.create(
            Miscelanea=self.tenant,
            Nombre=f"Categoria multi {suffix}",
            PorcentajeGanancia=Decimal("20.00"),
        )
        provider = Proveedor.objects.create(
            Miscelanea=self.tenant,
            Nombre=f"Proveedor multi {suffix}",
        )
        product = Producto.objects.create(
            Miscelanea=self.tenant,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre=f"Producto multi {suffix}",
        )
        entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant,
            IdProveedor=provider,
            IdUsuario=self.admin,
            FechaEntrada=timezone.now(),
            Total=cost * quantity,
        )
        detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant,
            IdEntradaInventario=entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=quantity,
            PrecioCompraUnitario=cost,
        )
        units = list(detail.inventarios.order_by("IdInventario"))
        return entry, detail, units

    @staticmethod
    def _sqlstate(exc):
        seen = set()
        while exc is not None and id(exc) not in seen:
            seen.add(id(exc))
            state = getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None)
            if state:
                return state
            exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
        return None

    @staticmethod
    def _wait_for_blocked(pid, blocker_pid, timeout=10):
        deadline = monotonic() + timeout
        poll_tick = Event()
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT wait_event_type, pg_blocking_pids(pid)
                    FROM pg_stat_activity
                    WHERE pid = %s
                    """,
                    [pid],
                )
                row = cursor.fetchone()
            if row and row[0] == "Lock" and blocker_pid in row[1]:
                return True
            poll_tick.wait(0.02)
        return False

    @staticmethod
    def _sale_payload(units):
        return {
            "fecha": timezone.now().isoformat(),
            "detalles": [{"inventarioId": unit.pk} for unit in units],
        }

    @staticmethod
    def _loss_payload(units):
        return {
            "tipoPerdida": "Robo",
            "detalles": [{"inventarioId": unit.pk} for unit in units],
        }

    def _run_worker(self, name, token, request_callable, shared):
        close_old_connections()
        try:
            connection.connect()
            with connection.cursor() as cursor:
                cursor.execute(f'SET ROLE "{RLS_TEST_ROLE}"')
                cursor.execute("SET lock_timeout = '8s'")
                cursor.execute("SET statement_timeout = '15s'")
                cursor.execute("SELECT current_user, pg_backend_pid()")
                role, pid = cursor.fetchone()
            with shared["lock"]:
                shared["roles"][name] = role
                shared["pids"][name] = pid
            shared["pid_ready"][name].set()

            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            response = request_callable(client)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_setting(%s, true)", ["app.miscelanea_id"]
                )
                context = cursor.fetchone()[0] or ""
            with shared["lock"]:
                shared["statuses"][name] = response.status_code
                shared["responses"][name] = response.data
                shared["contexts"][name] = context
        except BaseException as exc:
            with shared["lock"]:
                shared["errors"].append((name, repr(exc), self._sqlstate(exc)))
        finally:
            if connection.connection is not None:
                try:
                    if connection.needs_rollback:
                        connection.rollback()
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT set_config(%s, %s, false)",
                            ["app.miscelanea_id", ""],
                        )
                        cursor.execute("RESET ROLE")
                except Exception:
                    pass
                finally:
                    connection.close()

    def _shared(self, names):
        return {
            "lock": Lock(),
            "roles": {},
            "pids": {},
            "pid_ready": {name: Event() for name in names},
            "statuses": {},
            "responses": {},
            "contexts": {},
            "errors": [],
            "sqlstates": {},
        }

    def _capture_exception(self, shared):
        exc = sys.exc_info()[1]
        state = self._sqlstate(exc)
        if state:
            with shared["lock"]:
                shared["sqlstates"][current_thread().name] = state

    def _audit_probe(self, shared):
        def probe(*args, **kwargs):
            self._capture_exception(shared)
            return real_audit_event(*args, **kwargs)

        return probe

    def _logger_probe(self, shared):
        def probe(*args, **kwargs):
            self._capture_exception(shared)

        return probe

    def _assert_workers_closed(self, threads, shared):
        for thread in threads:
            thread.join(20)
            self.assertFalse(thread.is_alive(), thread.name)
        self.assertEqual(shared["errors"], [])
        self.assertEqual(set(shared["roles"].values()), {RLS_TEST_ROLE})
        self.assertEqual(len(set(shared["pids"].values())), len(threads))
        self.assertEqual(set(shared["contexts"].values()), {""})

    def _assert_no_deadlock(self, shared):
        self.assertNotIn(
            "40P01",
            shared["sqlstates"].values(),
            {
                "sqlstates": shared["sqlstates"],
                "statuses": shared["statuses"],
            },
        )

    def test_two_sales_with_inverse_multiunit_order_do_not_deadlock(self):
        units = [self.unit_low, self.unit_high]
        shared = self._shared(("sale-forward", "sale-reverse"))
        pre_read_barrier = Barrier(2)
        winner_ready = Event()
        release_winner = Event()
        winner = {}

        def coordinate_pre_read(*args, **kwargs):
            result = real_preleer_venta(*args, **kwargs)
            try:
                pre_read_barrier.wait(10)
            except BrokenBarrierError as exc:
                raise TimeoutError("No coincidieron ambas prelecturas") from exc
            return result

        def pause_winner_before_audit(*args, **kwargs):
            self._capture_exception(shared)
            if kwargs.get("resultado", "EXITOSO") == "EXITOSO":
                winner["name"] = current_thread().name
                winner_ready.set()
                if not release_winner.wait(10):
                    raise TimeoutError("No se liberó la venta ganadora")
            return real_audit_event(*args, **kwargs)

        token_a = self._token(self.cashier_a)
        token_b = self._token(self.cashier_b)
        threads = (
            Thread(
                name="sale-forward",
                target=self._run_worker,
                args=(
                    "sale-forward",
                    token_a,
                    lambda client: client.post(
                        "/api/ventas/procesar/",
                        self._sale_payload(units),
                        format="json",
                    ),
                    shared,
                ),
                daemon=True,
            ),
            Thread(
                name="sale-reverse",
                target=self._run_worker,
                args=(
                    "sale-reverse",
                    token_b,
                    lambda client: client.post(
                        "/api/ventas/procesar/",
                        self._sale_payload(list(reversed(units))),
                        format="json",
                    ),
                    shared,
                ),
                daemon=True,
            ),
        )

        wait_observed = False
        with patch(
            "ventas.views._preleer_referencias_venta",
            side_effect=coordinate_pre_read,
        ), patch(
            "ventas.views.registrar_evento_manual",
            side_effect=pause_winner_before_audit,
        ):
            try:
                for thread in threads:
                    thread.start()
                self.assertTrue(winner_ready.wait(10))
                loser = (
                    "sale-reverse"
                    if winner["name"] == "sale-forward"
                    else "sale-forward"
                )
                wait_observed = self._wait_for_blocked(
                    shared["pids"][loser],
                    shared["pids"][winner["name"]],
                )
            finally:
                release_winner.set()
                self._assert_workers_closed(threads, shared)

        self.assertTrue(wait_observed)
        self.assertEqual(sorted(shared["statuses"].values()), [201, 400])
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(DetalleVenta.objects.count(), 2)
        sale = Venta.objects.get()
        self.assertEqual(sale.detalles.count(), 2)
        self.assertEqual(sale.Total, Decimal("36.00"))
        for unit in units:
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Vendido")
        self.assertEqual(
            LogAuditoria.objects.filter(
                modulo="VENTAS", accion="PROCESAR", resultado="EXITOSO"
            ).count(),
            1,
        )
        self._assert_no_deadlock(shared)

    def test_sale_and_loss_with_inverse_multiunit_order_do_not_deadlock(self):
        units = [self.unit_low, self.unit_high]
        shared = self._shared(("sale", "loss"))
        sale_holds_high = Event()
        release_sale = Event()
        real_save = Inventario.save
        sale_save_count = 0

        def pause_sale_after_high_lock(instance, *args, **kwargs):
            nonlocal sale_save_count
            if current_thread().name == "sale" and instance.pk == self.unit_high.pk:
                sale_save_count += 1
                if sale_save_count == 1:
                    sale_holds_high.set()
                    if not release_sale.wait(10):
                        raise TimeoutError("No se liberó la venta")
            return real_save(instance, *args, **kwargs)

        threads = (
            Thread(
                name="sale",
                target=self._run_worker,
                args=(
                    "sale",
                    self._token(self.cashier_a),
                    lambda client: client.post(
                        "/api/ventas/procesar/",
                        self._sale_payload([self.unit_high, self.unit_low]),
                        format="json",
                    ),
                    shared,
                ),
                daemon=True,
            ),
            Thread(
                name="loss",
                target=self._run_worker,
                args=(
                    "loss",
                    self._token(self.admin),
                    lambda client: client.post(
                        "/api/inventario/procesar-perdida/",
                        self._loss_payload(units),
                        format="json",
                    ),
                    shared,
                ),
                daemon=True,
            ),
        )
        wait_observed = False
        with patch.object(Inventario, "save", new=pause_sale_after_high_lock), patch(
            "ventas.views.registrar_evento_manual",
            side_effect=self._audit_probe(shared),
        ), patch(
            "inventario.views.registrar_evento_manual",
            side_effect=self._audit_probe(shared),
        ), patch(
            "inventario.views.logger.exception",
            side_effect=self._logger_probe(shared),
        ):
            try:
                threads[0].start()
                self.assertTrue(sale_holds_high.wait(10))
                threads[1].start()
                self.assertTrue(shared["pid_ready"]["loss"].wait(10))
                wait_observed = self._wait_for_blocked(
                    shared["pids"]["loss"], shared["pids"]["sale"]
                )
            finally:
                release_sale.set()
                self._assert_workers_closed(threads, shared)

        self.assertTrue(wait_observed)
        self.assertEqual(sorted(shared["statuses"].values()), [201, 400])
        sale_count = Venta.objects.count()
        loss_count = Perdida.objects.count()
        self.assertEqual((sale_count, loss_count) in ((1, 0), (0, 1)), True)
        self.assertEqual(DetalleVenta.objects.count(), sale_count * 2)
        self.assertEqual(DetallePerdida.objects.count(), loss_count * 2)
        expected_state = "Vendido" if sale_count else "Perdido"
        for unit in units:
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, expected_state)
        self.assertEqual(
            LogAuditoria.objects.filter(
                modulo__in=("VENTAS", "PERDIDAS"), resultado="EXITOSO"
            ).count(),
            1,
        )
        self._assert_no_deadlock(shared)

    def test_sale_and_purchase_delete_inverse_order_do_not_deadlock(self):
        units = self.delete_units
        shared = self._shared(("sale", "delete"))
        delete_holds_purchase = Event()
        release_delete = Event()
        real_delete = QuerySet.delete
        paused = False

        def pause_before_inventory_delete(queryset, *args, **kwargs):
            nonlocal paused
            if (
                current_thread().name == "delete"
                and queryset.model is Inventario
                and not paused
            ):
                paused = True
                delete_holds_purchase.set()
                if not release_delete.wait(10):
                    raise TimeoutError("No se liberó la eliminación")
            return real_delete(queryset, *args, **kwargs)

        threads = (
            Thread(
                name="sale",
                target=self._run_worker,
                args=(
                    "sale",
                    self._token(self.cashier_a),
                    lambda client: client.post(
                        "/api/ventas/procesar/",
                        self._sale_payload(list(reversed(units))),
                        format="json",
                    ),
                    shared,
                ),
                daemon=True,
            ),
            Thread(
                name="delete",
                target=self._run_worker,
                args=(
                    "delete",
                    self._token(self.admin),
                    lambda client: client.delete(
                        f"/api/inventario/entradas/{self.delete_entry.pk}/delete_completo/"
                    ),
                    shared,
                ),
                daemon=True,
            ),
        )
        wait_observed = False
        with patch.object(QuerySet, "delete", new=pause_before_inventory_delete), patch(
            "ventas.views.registrar_evento_manual",
            side_effect=self._audit_probe(shared),
        ), patch(
            "inventario.views.registrar_evento_manual",
            side_effect=self._audit_probe(shared),
        ), patch(
            "inventario.views.logger.exception",
            side_effect=self._logger_probe(shared),
        ):
            try:
                threads[1].start()
                self.assertTrue(delete_holds_purchase.wait(10))
                threads[0].start()
                self.assertTrue(shared["pid_ready"]["sale"].wait(10))
                wait_observed = self._wait_for_blocked(
                    shared["pids"]["sale"], shared["pids"]["delete"]
                )
            finally:
                release_delete.set()
                self._assert_workers_closed(threads, shared)

        self.assertTrue(wait_observed)
        self.assertEqual(shared["statuses"], {"delete": 204, "sale": 400})
        entry_exists = EntradaInventario.objects.filter(pk=self.delete_entry.pk).exists()
        self.assertFalse(entry_exists)
        self.assertFalse(
            DetalleEntradaInventario.objects.filter(pk=self.delete_detail.pk).exists()
        )
        self.assertFalse(
            Inventario.objects.filter(pk__in=[unit.pk for unit in units]).exists()
        )
        self.assertEqual(Venta.objects.count(), 0)
        self.assertEqual(DetalleVenta.objects.count(), 0)
        self.assertEqual(
            LogAuditoria.objects.filter(
                modulo__in=("VENTAS", "INVENTARIO"), resultado="EXITOSO"
            ).count(),
            1,
        )
        self._assert_no_deadlock(shared)

    def test_sale_waits_for_purchase_edit_with_inverse_multiunit_order(self):
        units = self.delete_units
        shared = self._shared(("sale", "edit"))
        edit_holds_purchase = Event()
        release_edit = Event()
        real_save = EntradaInventario.save
        paused = False

        def pause_before_entry_save(instance, *args, **kwargs):
            nonlocal paused
            if (
                current_thread().name == "edit"
                and instance.pk == self.delete_entry.pk
                and not paused
            ):
                paused = True
                edit_holds_purchase.set()
                if not release_edit.wait(10):
                    raise TimeoutError("No se liberó la edición")
            return real_save(instance, *args, **kwargs)

        edit_payload = {
            "proveedorId": self.delete_entry.IdProveedor_id,
            "detalles": [
                {
                    "id": self.delete_detail.pk,
                    "productoId": self.delete_detail.IdProducto_id,
                    "tipoCompra": self.delete_detail.TipoCompra,
                    "cantidad": 2,
                    "precioCompraUnitario": "31.00",
                }
            ],
        }
        threads = (
            Thread(
                name="sale",
                target=self._run_worker,
                args=(
                    "sale",
                    self._token(self.cashier_a),
                    lambda client: client.post(
                        "/api/ventas/procesar/",
                        self._sale_payload(list(reversed(units))),
                        format="json",
                    ),
                    shared,
                ),
                daemon=True,
            ),
            Thread(
                name="edit",
                target=self._run_worker,
                args=(
                    "edit",
                    self._token(self.admin),
                    lambda client: client.put(
                        f"/api/inventario/entradas/{self.delete_entry.pk}/editar-completa/",
                        edit_payload,
                        format="json",
                    ),
                    shared,
                ),
                daemon=True,
            ),
        )

        wait_observed = False
        with patch.object(EntradaInventario, "save", new=pause_before_entry_save), patch(
            "ventas.views.registrar_evento_manual",
            side_effect=self._audit_probe(shared),
        ), patch(
            "inventario.views.registrar_evento_manual",
            side_effect=self._audit_probe(shared),
        ), patch(
            "inventario.views.logger.exception",
            side_effect=self._logger_probe(shared),
        ):
            try:
                threads[1].start()
                self.assertTrue(edit_holds_purchase.wait(10))
                threads[0].start()
                self.assertTrue(shared["pid_ready"]["sale"].wait(10))
                wait_observed = self._wait_for_blocked(
                    shared["pids"]["sale"], shared["pids"]["edit"]
                )
            finally:
                release_edit.set()
                self._assert_workers_closed(threads, shared)

        self.assertTrue(wait_observed)
        self.assertEqual(shared["statuses"], {"edit": 200, "sale": 201})
        self.delete_entry.refresh_from_db()
        self.delete_detail.refresh_from_db()
        self.assertEqual(self.delete_detail.PrecioCompraUnitario, Decimal("31.00"))
        self.assertEqual(self.delete_detail.Cantidad, 2)
        self.assertEqual(
            list(
                Inventario.objects.filter(
                    IdDetalleEntrada=self.delete_detail
                )
                .order_by("IdInventario")
                .values_list("IdInventario", "Estado")
            ),
            [(unit.pk, "Vendido") for unit in units],
        )
        sale = Venta.objects.get()
        self.assertEqual(sale.detalles.count(), 2)
        self.assertEqual(
            sale.Total,
            sum(
                sale.detalles.values_list("PrecioVentaUnitario", flat=True),
                Decimal("0.00"),
            ),
        )
        self.assertEqual(
            LogAuditoria.objects.filter(
                modulo__in=("VENTAS", "INVENTARIO"), resultado="EXITOSO"
            ).count(),
            2,
        )
        self._assert_no_deadlock(shared)

    def test_sale_waits_for_return_with_inverse_multiunit_order(self):
        units = self.delete_units
        shared = self._shared(("sale", "return"))
        return_holds_purchase = Event()
        release_return = Event()
        real_save = Inventario.save
        paused = False

        def pause_return_before_first_unit_save(instance, *args, **kwargs):
            nonlocal paused
            if (
                current_thread().name == "return"
                and instance.pk in {unit.pk for unit in units}
                and not paused
            ):
                paused = True
                return_holds_purchase.set()
                if not release_return.wait(10):
                    raise TimeoutError("No se liberó la devolución")
            return real_save(instance, *args, **kwargs)

        return_payload = {
            "IdEntradaInventario": self.delete_entry.pk,
            "Observaciones": "Prueba concurrente",
            "detalles": [
                {
                    "inventarioId": unit.pk,
                    "MotivoRechazo": "Prueba concurrente",
                }
                for unit in units
            ],
        }
        threads = (
            Thread(
                name="sale",
                target=self._run_worker,
                args=(
                    "sale",
                    self._token(self.cashier_a),
                    lambda client: client.post(
                        "/api/ventas/procesar/",
                        self._sale_payload(list(reversed(units))),
                        format="json",
                    ),
                    shared,
                ),
                daemon=True,
            ),
            Thread(
                name="return",
                target=self._run_worker,
                args=(
                    "return",
                    self._token(self.admin),
                    lambda client: client.post(
                        "/api/inventario/procesar-devolucion/",
                        return_payload,
                        format="json",
                    ),
                    shared,
                ),
                daemon=True,
            ),
        )

        wait_observed = False
        with patch.object(
            Inventario, "save", new=pause_return_before_first_unit_save
        ), patch(
            "ventas.views.registrar_evento_manual",
            side_effect=self._audit_probe(shared),
        ), patch(
            "inventario.views.registrar_evento_manual",
            side_effect=self._audit_probe(shared),
        ), patch(
            "inventario.views.logger.exception",
            side_effect=self._logger_probe(shared),
        ):
            try:
                threads[1].start()
                self.assertTrue(return_holds_purchase.wait(10))
                threads[0].start()
                self.assertTrue(shared["pid_ready"]["sale"].wait(10))
                wait_observed = self._wait_for_blocked(
                    shared["pids"]["sale"], shared["pids"]["return"]
                )
            finally:
                release_return.set()
                self._assert_workers_closed(threads, shared)

        self.assertTrue(wait_observed)
        self.assertEqual(shared["statuses"], {"return": 201, "sale": 400})
        self.assertEqual(Venta.objects.count(), 0)
        self.assertEqual(DetalleVenta.objects.count(), 0)
        solicitud = SolicitudDevolucion.objects.get()
        self.assertEqual(solicitud.Estado, "Pendiente")
        self.assertEqual(
            DetalleSolicitudDevolucion.objects.filter(
                IdSolicitudDevolucion=solicitud
            ).count(),
            2,
        )
        for unit in units:
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Devuelto")
        self.assertEqual(
            LogAuditoria.objects.filter(
                modulo__in=("VENTAS", "DEVOLUCIONES"), resultado="EXITOSO"
            ).count(),
            1,
        )
        self._assert_no_deadlock(shared)
