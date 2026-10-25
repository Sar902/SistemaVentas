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
from usuarios.models import DelegacionAdmin, Miscelanea, Usuario
from usuarios.test_utils import TemporaryRLSNeutralizationMixin


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ResolverDevolucionTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Resolucion A", Codigo="RESOLUCION-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Resolucion B", Codigo="RESOLUCION-B"
        )
        self.admin_a = self._user("admin-resolver-a@example.test", self.tenant_a, "admin")
        self.cashier_a = self._user(
            "cajero-resolver-a@example.test", self.tenant_a, "cajero"
        )
        self.admin_b = self._user("admin-resolver-b@example.test", self.tenant_b, "admin")
        self.category_a = self._category(self.tenant_a, "Categoria resolver A")
        self.category_b = self._category(self.tenant_b, "Categoria resolver B")
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor resolver A"
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor resolver B"
        )
        self.product_a = self._product(
            self.tenant_a, self.category_a, self.provider_a, "Producto resolver A"
        )
        self.product_b = self._product(
            self.tenant_b, self.category_b, self.provider_b, "Producto resolver B"
        )
        self.entry_a = self._entry(self.tenant_a, self.provider_a, self.admin_a)
        self.expensive = self._detail(
            self.tenant_a, self.entry_a, self.product_a, 6, Decimal("10.25")
        )
        self.cheap = self._detail(
            self.tenant_a, self.entry_a, self.product_a, 6, Decimal("3.40")
        )
        self.expensive_units = list(self.expensive.inventarios.order_by("IdInventario"))
        self.cheap_units = list(self.cheap.inventarios.order_by("IdInventario"))
        self.other_entry_a = self._entry(self.tenant_a, self.provider_a, self.admin_a)
        self.other_detail_a = self._detail(
            self.tenant_a, self.other_entry_a, self.product_a, 2, Decimal("7.00")
        )
        self.other_units_a = list(self.other_detail_a.inventarios.order_by("IdInventario"))
        self.entry_b = self._entry(self.tenant_b, self.provider_b, self.admin_b)
        self.detail_b = self._detail(
            self.tenant_b, self.entry_b, self.product_b, 2, Decimal("1.00")
        )
        self.units_b = list(self.detail_b.inventarios.order_by("IdInventario"))
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
    def _category(tenant, name):
        return Categoria.objects.create(
            Miscelanea=tenant, Nombre=name, PorcentajeGanancia=Decimal("20.00")
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
    def _entry(tenant, provider, user):
        return EntradaInventario.objects.create(
            Miscelanea=tenant,
            IdProveedor=provider,
            IdUsuario=user,
            FechaEntrada=timezone.now() - timedelta(days=2),
            Total=Decimal("100.00"),
        )

    @staticmethod
    def _detail(tenant, entry, product, quantity, cost):
        return DetalleEntradaInventario.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=quantity,
            PrecioCompraUnitario=cost,
        )

    def _request(self, units, *, entry=None, tenant=None, user=None):
        entry = entry or self.entry_a
        tenant = tenant or self.tenant_a
        user = user or self.admin_a
        request_record = SolicitudDevolucion.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdUsuario=user,
            Estado="Pendiente",
            Observaciones="Solicitud para resolver",
            Fecha=timezone.now() - timedelta(days=1),
        )
        for unit in units:
            unit.Estado = "Devuelto"
            unit.FechaMovimiento = timezone.now() - timedelta(hours=3)
            unit.save(update_fields=["Estado", "FechaMovimiento"])
            DetalleSolicitudDevolucion.objects.create(
                Miscelanea=tenant,
                IdSolicitudDevolucion=request_record,
                IdInventario=unit,
                MotivoRechazo="Empaque deteriorado",
                PrecioCompraUnitario=unit.IdDetalleEntrada.PrecioCompraUnitario,
                EstadoItem="Pendiente",
            )
        return request_record

    @staticmethod
    def _url(request_record):
        return (
            "/api/inventario/solicitudes-devolucion/"
            f"{request_record.pk}/resolver/"
        )

    def _success_logs(self):
        return LogAuditoria.objects.filter(
            Miscelanea=self.tenant_a,
            accion="RESOLVER",
            modulo="DEVOLUCIONES",
            resultado="EXITOSO",
        )

    def test_accept_keeps_returned_units_and_movement_without_loss(self):
        request_record = self._request(
            [self.expensive_units[0], self.cheap_units[0]]
        )
        movements = {
            unit.pk: Inventario.objects.get(pk=unit.pk).FechaMovimiento
            for unit in (self.expensive_units[0], self.cheap_units[0])
        }

        response = self.client.post(
            self._url(request_record), {"estado": "Aceptada"}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json(), {"message": "Solicitud resuelta como Aceptada."})
        request_record.refresh_from_db()
        self.assertEqual(request_record.Estado, "Aceptada")
        self.assertEqual(
            set(request_record.detallesolicituddevolucion_set.values_list("EstadoItem", flat=True)),
            {"Aceptado"},
        )
        for unit_id, movement in movements.items():
            unit = Inventario.objects.get(pk=unit_id)
            self.assertEqual(unit.Estado, "Devuelto")
            self.assertEqual(unit.FechaMovimiento, movement)
        self.assertEqual(Perdida.objects.count(), 0)
        self.assertEqual(DetallePerdida.objects.count(), 0)
        self.assertEqual(self._success_logs().count(), 1)

    def test_reject_creates_one_exact_loss_and_server_owned_resolution(self):
        units = [self.expensive_units[1], self.cheap_units[1]]
        request_record = self._request(units)
        start = timezone.now()
        response = self.client.post(
            self._url(request_record),
            {
                "estado": "Rechazada",
                "usuarioId": self.admin_b.pk,
                "Miscelanea": self.tenant_b.pk,
                "total": "999.99",
                "fecha": "2000-01-01T00:00:00Z",
            },
            format="json",
        )
        end = timezone.now()

        self.assertEqual(response.status_code, 200, response.data)
        request_record.refresh_from_db()
        self.assertEqual(request_record.Estado, "Rechazada")
        loss = Perdida.objects.get()
        self.assertEqual(loss.TipoPerdida, "RechazoDevolucion")
        self.assertEqual(loss.IdUsuario_id, self.admin_a.pk)
        self.assertEqual(loss.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(loss.Total, Decimal("13.65"))
        self.assertLessEqual(start, loss.Fecha)
        self.assertLessEqual(loss.Fecha, end)
        details = DetallePerdida.objects.filter(IdPerdida=loss)
        self.assertEqual(
            {detail.IdInventario_id: detail.PrecioCompraUnitario for detail in details},
            {units[0].pk: Decimal("10.25"), units[1].pk: Decimal("3.40")},
        )
        self.assertEqual(
            set(request_record.detallesolicituddevolucion_set.values_list("EstadoItem", flat=True)),
            {"Rechazado"},
        )
        for unit in units:
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Perdido")
            self.assertLessEqual(start, unit.FechaMovimiento)
            self.assertLessEqual(unit.FechaMovimiento, end)
        log = self._success_logs().get()
        self.assertEqual(log.datos_nuevos["id_solicitud"], request_record.pk)
        self.assertEqual(log.datos_nuevos["id_perdida"], loss.pk)

    def test_second_resolution_cannot_change_result_or_duplicate_loss_or_audit(self):
        request_record = self._request([self.expensive_units[2]])
        first = self.client.post(
            self._url(request_record), {"estado": "Rechazada"}, format="json"
        )
        counts = (
            Perdida.objects.count(),
            DetallePerdida.objects.count(),
            self._success_logs().count(),
        )
        second = self.client.post(
            self._url(request_record), {"estado": "Aceptada"}, format="json"
        )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 400, second.data)
        self.assertEqual(
            (Perdida.objects.count(), DetallePerdida.objects.count(), self._success_logs().count()),
            counts,
        )
        request_record.refresh_from_db()
        self.expensive_units[2].refresh_from_db()
        self.assertEqual(request_record.Estado, "Rechazada")
        self.assertEqual(self.expensive_units[2].Estado, "Perdido")

    def test_invalid_payloads_empty_duplicate_and_nonpending_details_are_rejected(self):
        pending = self._request([self.expensive_units[3]])
        for payload in ({}, {"estado": "Pendiente"}, {"estado": None}, {"estado": 1}):
            with self.subTest(payload=payload):
                response = self.client.post(self._url(pending), payload, format="json")
                self.assertEqual(response.status_code, 400, response.data)
        empty = self._request([])
        self.assertEqual(
            self.client.post(self._url(empty), {"estado": "Aceptada"}, format="json").status_code,
            400,
        )
        duplicate = self._request([self.cheap_units[2]])
        DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant_a,
            IdSolicitudDevolucion=duplicate,
            IdInventario=self.cheap_units[2],
            PrecioCompraUnitario=Decimal("3.40"),
            EstadoItem="Pendiente",
        )
        self.assertEqual(
            self.client.post(self._url(duplicate), {"estado": "Aceptada"}, format="json").status_code,
            400,
        )
        nonpending = self._request([self.other_units_a[1]], entry=self.other_entry_a)
        nonpending_detail = nonpending.detallesolicituddevolucion_set.get()
        nonpending_detail.EstadoItem = "Aceptado"
        nonpending_detail.save(update_fields=["EstadoItem"])
        self.assertEqual(
            self.client.post(self._url(nonpending), {"estado": "Rechazada"}, format="json").status_code,
            400,
        )
        self.assertEqual(Perdida.objects.count(), 0)
        self.assertEqual(self._success_logs().count(), 0)

    def test_cost_state_and_relationship_inconsistencies_are_rejected_without_changes(self):
        cases = []
        wrong_cost = self._request([self.cheap_units[3]])
        wrong_cost.detallesolicituddevolucion_set.update(PrecioCompraUnitario=Decimal("9.99"))
        cases.append((wrong_cost, self.cheap_units[3]))
        wrong_state = self._request([self.cheap_units[4]])
        Inventario.objects.filter(pk=self.cheap_units[4].pk).update(Estado="Disponible")
        cases.append((wrong_state, self.cheap_units[4]))
        wrong_entry = self._request([self.other_units_a[0]], entry=self.entry_a)
        cases.append((wrong_entry, self.other_units_a[0]))
        wrong_tenant_relation = self._request([self.expensive_units[4]])
        Inventario.objects.filter(pk=self.expensive_units[4].pk).update(
            IdDetalleEntrada=self.detail_b
        )
        cases.append((wrong_tenant_relation, self.expensive_units[4]))
        wrong_detail_tenant = self._request([self.expensive_units[0]])
        wrong_detail_tenant.detallesolicituddevolucion_set.update(
            Miscelanea=self.tenant_b
        )
        cases.append((wrong_detail_tenant, self.expensive_units[0]))
        wrong_request_user = self._request([self.expensive_units[1]])
        SolicitudDevolucion.objects.filter(pk=wrong_request_user.pk).update(
            IdUsuario=self.admin_b
        )
        cases.append((wrong_request_user, self.expensive_units[1]))

        for request_record, unit in cases:
            with self.subTest(request_id=request_record.pk):
                unit.refresh_from_db()
                before = (request_record.Estado, unit.Estado, Perdida.objects.count())
                response = self.client.post(
                    self._url(request_record), {"estado": "Rechazada"}, format="json"
                )
                self.assertEqual(response.status_code, 400, response.data)
                request_record.refresh_from_db()
                unit.refresh_from_db()
                self.assertEqual((request_record.Estado, unit.Estado, Perdida.objects.count()), before)
        self.assertEqual(self._success_logs().count(), 0)

    def test_permissions_isolation_missing_and_user_without_tenant(self):
        foreign = self._request(
            [self.units_b[0]], entry=self.entry_b, tenant=self.tenant_b, user=self.admin_b
        )
        self.assertEqual(
            self.client.post(self._url(foreign), {"estado": "Aceptada"}, format="json").status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/api/inventario/solicitudes-devolucion/999999999/resolver/",
                {"estado": "Aceptada"},
                format="json",
            ).status_code,
            404,
        )
        own = self._request([self.expensive_units[5]])
        self.client.force_authenticate(self.cashier_a)
        self.assertEqual(
            self.client.post(self._url(own), {"estado": "Aceptada"}, format="json").status_code,
            403,
        )
        now = timezone.now()
        DelegacionAdmin.objects.create(
            Miscelanea=self.tenant_a,
            CreadoPor=self.admin_a,
            Empleado=self.cashier_a,
            CodigoHash=make_password("valid-resolution-delegation"),
            CodigoPrefijo="RSL",
            Modulos=["DEVOLUCIONES"],
            DuracionMinutos=30,
            CodigoExpiraEn=now + timedelta(minutes=10),
            UsadoEn=now,
            ConcedidoHasta=now + timedelta(minutes=30),
        )
        self.assertEqual(
            self.client.post(self._url(own), {"estado": "Aceptada"}, format="json").status_code,
            200,
        )
        no_tenant = self._user("admin-resolver-none@example.test", None, "admin")
        another = self._request([self.cheap_units[5]])
        self.client.force_authenticate(no_tenant)
        self.assertEqual(
            self.client.post(self._url(another), {"estado": "Aceptada"}, format="json").status_code,
            400,
        )
        another.refresh_from_db()
        self.assertEqual(another.Estado, "Pendiente")

    def test_unexpected_failure_after_writes_rolls_back_and_hides_exception(self):
        request_record = self._request([self.expensive_units[0], self.cheap_units[0]])
        secret = "private-resolution-failure"
        original_save = SolicitudDevolucion.save

        def fail_final_request_save(instance, *args, **kwargs):
            if instance.pk == request_record.pk and instance.Estado == "Rechazada":
                raise RuntimeError(secret)
            return original_save(instance, *args, **kwargs)

        with patch.object(SolicitudDevolucion, "save", new=fail_final_request_save):
            response = self.client.post(
                self._url(request_record), {"estado": "Rechazada"}, format="json"
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertNotIn(secret, str(response.data))
        request_record.refresh_from_db()
        self.assertEqual(request_record.Estado, "Pendiente")
        self.assertEqual(
            set(request_record.detallesolicituddevolucion_set.values_list("EstadoItem", flat=True)),
            {"Pendiente"},
        )
        for unit in (self.expensive_units[0], self.cheap_units[0]):
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Devuelto")
        self.assertEqual(Perdida.objects.count(), 0)
        self.assertEqual(DetallePerdida.objects.count(), 0)
        self.assertEqual(self._success_logs().count(), 0)
        failed = LogAuditoria.objects.filter(
            Miscelanea=self.tenant_a, modulo="DEVOLUCIONES", resultado="FALLIDO"
        )
        self.assertEqual(failed.count(), 1)
        self.assertNotIn(secret, failed.get().descripcion)

    def test_success_audit_remains_best_effort(self):
        request_record = self._request([self.expensive_units[1]])
        with patch(
            "auditoria.mixins.LogAuditoria.objects.create",
            side_effect=RuntimeError("audit-only-resolution"),
        ) as mocked_create:
            response = self.client.post(
                self._url(request_record), {"estado": "Aceptada"}, format="json"
            )

        self.assertEqual(response.status_code, 200, response.data)
        mocked_create.assert_called_once()
        request_record.refresh_from_db()
        self.assertEqual(request_record.Estado, "Aceptada")
        self.assertEqual(LogAuditoria.objects.count(), 0)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ResolverDevolucionConcurrencyTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant = Miscelanea.objects.create(
            Nombre="Concurrencia resolver", Codigo="CONC-RESOLVER"
        )
        self.admin = Usuario.objects.create_user(
            Email="admin-concurrencia-resolver@example.test",
            Password="A-strong-test-password-2026!",
            Nombre="Admin concurrencia resolver",
            Miscelanea=self.tenant,
            Rol="admin",
            Estado="Activo",
        )
        category = Categoria.objects.create(
            Miscelanea=self.tenant,
            Nombre="Categoria concurrencia resolver",
            PorcentajeGanancia=Decimal("20.00"),
        )
        provider = Proveedor.objects.create(
            Miscelanea=self.tenant, Nombre="Proveedor concurrencia resolver"
        )
        product = Producto.objects.create(
            Miscelanea=self.tenant,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre="Producto concurrencia resolver",
        )
        self.entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant,
            IdProveedor=provider,
            IdUsuario=self.admin,
            FechaEntrada=timezone.now(),
            Total=Decimal("10.00"),
        )
        detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant,
            IdEntradaInventario=self.entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=2,
            PrecioCompraUnitario=Decimal("10.00"),
        )
        self.units = list(detail.inventarios.order_by("IdInventario"))

    def _request(self, unit):
        unit.Estado = "Devuelto"
        unit.FechaMovimiento = timezone.now() - timedelta(hours=2)
        unit.save(update_fields=["Estado", "FechaMovimiento"])
        request_record = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant,
            IdEntradaInventario=self.entry,
            IdUsuario=self.admin,
            Estado="Pendiente",
            Fecha=timezone.now() - timedelta(hours=1),
        )
        DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant,
            IdSolicitudDevolucion=request_record,
            IdInventario=unit,
            PrecioCompraUnitario=Decimal("10.00"),
            EstadoItem="Pendiente",
        )
        return request_record

    @staticmethod
    def _url(request_record):
        return f"/api/inventario/solicitudes-devolucion/{request_record.pk}/resolver/"

    @staticmethod
    def _wait_for_database_lock(pid, timeout=10):
        deadline = monotonic() + timeout
        tick = Event()
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid]
                )
                row = cursor.fetchone()
            if row and row[0] == "Lock":
                return True
            tick.wait(0.02)
        return False

    def _run_competing_resolutions(self, first_state, second_state):
        request_record = self._request(self.units[0])
        self.units[0].refresh_from_db()
        self._movement_before_resolution = self.units[0].FechaMovimiento
        first_at_audit = Event()
        release_first = Event()
        second_pid_ready = Event()
        result_lock = Lock()
        results = {}
        errors = []
        pids = {}

        def blocking_audit(*args, **kwargs):
            real_audit_event(*args, **kwargs)
            if (
                kwargs.get("accion") == "RESOLVER"
                and kwargs.get("resultado", "EXITOSO") == "EXITOSO"
                and not first_at_audit.is_set()
            ):
                first_at_audit.set()
                if not release_first.wait(10):
                    raise TimeoutError("La primera resolucion no fue liberada")

        def worker(name, state, announce_pid=False):
            close_old_connections()
            try:
                if announce_pid:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid()")
                        pid = cursor.fetchone()[0]
                    with result_lock:
                        pids[name] = pid
                    second_pid_ready.set()
                client = APIClient()
                client.force_authenticate(self.admin)
                response = client.post(
                    self._url(request_record), {"estado": state}, format="json"
                )
                with result_lock:
                    results[name] = response.status_code
            except BaseException as exc:
                with result_lock:
                    errors.append((name, repr(exc)))
            finally:
                close_old_connections()

        first = Thread(target=worker, args=("first", first_state), daemon=True)
        second = Thread(target=worker, args=("second", second_state, True), daemon=True)
        lock_observed = False
        with patch(
            "inventario.views.registrar_evento_manual", side_effect=blocking_audit
        ):
            try:
                first.start()
                self.assertTrue(first_at_audit.wait(10), (results, errors))
                second.start()
                self.assertTrue(second_pid_ready.wait(10), (results, errors))
                lock_observed = self._wait_for_database_lock(pids["second"])
            finally:
                release_first.set()
                first.join(10)
                if second.ident is not None:
                    second.join(10)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(lock_observed)
        self.assertEqual(results, {"first": 200, "second": 400})
        return request_record

    def test_two_concurrent_rejections_create_one_loss(self):
        request_record = self._run_competing_resolutions("Rechazada", "Rechazada")
        request_record.refresh_from_db()
        self.units[0].refresh_from_db()
        self.assertEqual(request_record.Estado, "Rechazada")
        self.assertEqual(self.units[0].Estado, "Perdido")
        self.assertEqual(Perdida.objects.count(), 1)
        self.assertEqual(DetallePerdida.objects.count(), 1)
        self.assertEqual(
            LogAuditoria.objects.filter(
                accion="RESOLVER", modulo="DEVOLUCIONES", resultado="EXITOSO"
            ).count(),
            1,
        )

    def test_acceptance_winning_against_rejection_is_the_only_resolution(self):
        request_record = self._run_competing_resolutions("Aceptada", "Rechazada")
        request_record.refresh_from_db()
        detail = request_record.detallesolicituddevolucion_set.get()
        self.units[0].refresh_from_db()
        self.assertEqual(request_record.Estado, "Aceptada")
        self.assertEqual(detail.EstadoItem, "Aceptado")
        self.assertEqual(self.units[0].Estado, "Devuelto")
        self.assertEqual(
            self.units[0].FechaMovimiento, self._movement_before_resolution
        )
        self.assertEqual(Perdida.objects.count(), 0)
        self.assertEqual(DetallePerdida.objects.count(), 0)
