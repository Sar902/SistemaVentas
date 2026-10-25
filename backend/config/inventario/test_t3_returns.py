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
from inventario.serializers import ProcesarDevolucionInputSerializer
from usuarios.models import DelegacionAdmin, Miscelanea, Usuario
from usuarios.test_utils import TemporaryRLSNeutralizationMixin
from ventas.models import DetalleVenta, Venta


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ProcesarDevolucionTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    url = "/api/inventario/procesar-devolucion/"

    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Devoluciones A", Codigo="DEVOLUCIONES-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Devoluciones B", Codigo="DEVOLUCIONES-B"
        )
        self.admin_a = self._user("admin-devoluciones-a@example.test", self.tenant_a, "admin")
        self.cashier_a = self._user(
            "cajero-devoluciones-a@example.test", self.tenant_a, "cajero"
        )
        self.admin_b = self._user("admin-devoluciones-b@example.test", self.tenant_b, "admin")
        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoria devoluciones A",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Categoria devoluciones B",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor devoluciones A"
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor devoluciones B"
        )
        self.product_a = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Producto compartido por lotes A",
        )
        self.product_b = Producto.objects.create(
            Miscelanea=self.tenant_b,
            IdCategoria=self.category_b,
            IdProveedor=self.provider_b,
            Nombre="Producto devoluciones B",
        )
        self.entry_a = self._entry(self.tenant_a, self.provider_a, self.admin_a)
        self.detail_a_expensive = self._detail(
            self.tenant_a, self.entry_a, self.product_a, 3, Decimal("10.25")
        )
        self.detail_a_cheap = self._detail(
            self.tenant_a, self.entry_a, self.product_a, 3, Decimal("3.40")
        )
        self.units_expensive = list(
            self.detail_a_expensive.inventarios.order_by("IdInventario")
        )
        self.units_cheap = list(
            self.detail_a_cheap.inventarios.order_by("IdInventario")
        )
        self.other_entry_a = self._entry(
            self.tenant_a, self.provider_a, self.admin_a
        )
        self.other_detail_a = self._detail(
            self.tenant_a,
            self.other_entry_a,
            self.product_a,
            1,
            Decimal("7.00"),
        )
        self.other_unit_a = self.other_detail_a.inventarios.get()
        self.entry_b = self._entry(self.tenant_b, self.provider_b, self.admin_b)
        self.detail_b = self._detail(
            self.tenant_b, self.entry_b, self.product_b, 1, Decimal("1.00")
        )
        self.unit_b = self.detail_b.inventarios.get()
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

    def _payload(self, unit_ids, **extra):
        payload = {
            "IdEntradaInventario": self.entry_a.pk,
            "usuarioId": self.admin_a.pk,
            "fechaSolicitud": timezone.now().isoformat(),
            "Estado": "Pendiente",
            "Observaciones": "Empaque deteriorado",
            "detalles": [
                {
                    "inventarioId": unit_id,
                    "MotivoRechazo": "Producto recibido dañado",
                    "PrecioCompraUnitario": "1.00",
                    "EstadoItem": "Pendiente",
                }
                for unit_id in unit_ids
            ],
        }
        payload.update(extra)
        return payload

    @staticmethod
    def _domain_counts():
        return SolicitudDevolucion.objects.count(), DetalleSolicitudDevolucion.objects.count()

    def _success_logs(self):
        return LogAuditoria.objects.filter(
            Miscelanea=self.tenant_a,
            accion="PROCESAR",
            modulo="DEVOLUCIONES",
            resultado="EXITOSO",
        )

    def test_server_controls_identity_time_states_costs_and_success_audit(self):
        old_date = (timezone.now() - timedelta(days=90)).isoformat()
        start = timezone.now()
        response = self.client.post(
            self.url,
            self._payload(
                [self.units_expensive[0].pk, self.units_cheap[0].pk],
                usuarioId=self.admin_b.pk,
                fechaSolicitud=old_date,
                Estado="Rechazada",
                Miscelanea=self.tenant_b.pk,
                detalles=[
                    {
                        "inventarioId": self.units_expensive[0].pk,
                        "MotivoRechazo": "Costo caro",
                        "PrecioCompraUnitario": "999.99",
                        "EstadoItem": "Rechazado",
                    },
                    {
                        "inventarioId": self.units_cheap[0].pk,
                        "MotivoRechazo": "Costo barato",
                        "PrecioCompraUnitario": "888.88",
                        "EstadoItem": "Aceptado",
                    },
                ],
            ),
            format="json",
        )
        end = timezone.now()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            response.json(), {"message": "Solicitud de devolución procesada."}
        )
        request_record = SolicitudDevolucion.objects.get()
        self.assertEqual(request_record.IdUsuario_id, self.admin_a.pk)
        self.assertEqual(request_record.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(request_record.Estado, "Pendiente")
        self.assertLessEqual(start, request_record.Fecha)
        self.assertLessEqual(request_record.Fecha, end)
        details = list(
            DetalleSolicitudDevolucion.objects.filter(
                IdSolicitudDevolucion=request_record
            ).order_by("IdInventario_id")
        )
        self.assertEqual(
            {detail.IdInventario_id: detail.PrecioCompraUnitario for detail in details},
            {
                self.units_expensive[0].pk: Decimal("10.25"),
                self.units_cheap[0].pk: Decimal("3.40"),
            },
        )
        self.assertEqual({detail.EstadoItem for detail in details}, {"Pendiente"})
        self.assertEqual(
            {detail.MotivoRechazo for detail in details},
            {"Costo caro", "Costo barato"},
        )
        for unit in (self.units_expensive[0], self.units_cheap[0]):
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Devuelto")
            self.assertLessEqual(start, unit.FechaMovimiento)
            self.assertLessEqual(unit.FechaMovimiento, end)
        self.assertEqual(self._success_logs().count(), 1)

    def test_ignored_fields_and_optional_texts_may_be_omitted(self):
        response = self.client.post(
            self.url,
            {
                "IdEntradaInventario": self.entry_a.pk,
                "detalles": [{"inventarioId": self.units_expensive[1].pk}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        request_record = SolicitudDevolucion.objects.get()
        detail = DetalleSolicitudDevolucion.objects.get()
        self.assertEqual(request_record.IdUsuario_id, self.admin_a.pk)
        self.assertEqual(request_record.Estado, "Pendiente")
        self.assertIn(request_record.Observaciones, (None, ""))
        self.assertEqual(detail.PrecioCompraUnitario, Decimal("10.25"))
        self.assertEqual(detail.EstadoItem, "Pendiente")
        self.assertIn(detail.MotivoRechazo, (None, ""))

    def test_empty_malformed_duplicate_and_missing_ids_are_rejected(self):
        valid_id = self.units_expensive[0].pk
        cases = (
            {},
            {"IdEntradaInventario": self.entry_a.pk},
            self._payload([]),
            self._payload([None]),
            self._payload([True]),
            self._payload([1.5]),
            self._payload(["1.5"]),
            self._payload([0]),
            self._payload([-1]),
            self._payload([999999999]),
            self._payload([valid_id, valid_id]),
            self._payload([valid_id], IdEntradaInventario=True),
            self._payload([valid_id], IdEntradaInventario=None),
            self._payload([valid_id], IdEntradaInventario=1.5),
            self._payload([valid_id], IdEntradaInventario="1.5"),
            self._payload([valid_id], IdEntradaInventario=[]),
            self._payload([valid_id], IdEntradaInventario=0),
            self._payload([valid_id], IdEntradaInventario=999999999),
            self._payload([valid_id], Observaciones={"no": "texto"}),
            self._payload(
                [valid_id],
                detalles=[{"inventarioId": valid_id, "MotivoRechazo": []}],
            ),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                before = self._domain_counts()
                response = self.client.post(self.url, payload, format="json")
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self._domain_counts(), before)
                self.units_expensive[0].refresh_from_db()
                self.assertEqual(self.units_expensive[0].Estado, "Disponible")

    def test_foreign_and_other_entry_units_are_rejected_without_writes(self):
        for unit in (self.unit_b, self.other_unit_a):
            with self.subTest(unit=unit.pk):
                before = self._domain_counts()
                response = self.client.post(
                    self.url, self._payload([unit.pk]), format="json"
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self._domain_counts(), before)
                unit.refresh_from_db()
                self.assertEqual(unit.Estado, "Disponible")

    def test_each_nonavailable_state_is_rejected_without_changes(self):
        unit = self.units_expensive[0]
        for state in ("Vendido", "Perdido", "Devuelto"):
            with self.subTest(state=state):
                unit.Estado = state
                unit.FechaMovimiento = timezone.now() - timedelta(days=3)
                unit.save(update_fields=["Estado", "FechaMovimiento"])
                old_movement = unit.FechaMovimiento
                before = self._domain_counts()
                response = self.client.post(
                    self.url, self._payload([unit.pk]), format="json"
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self._domain_counts(), before)
                unit.refresh_from_db()
                self.assertEqual((unit.Estado, unit.FechaMovimiento), (state, old_movement))

    def test_cross_tenant_purchase_relation_is_rejected_without_changes(self):
        inconsistent = Inventario.objects.create(
            Miscelanea=self.tenant_a,
            IdDetalleEntrada=self.detail_b,
            Estado="Disponible",
        )
        before = self._domain_counts()
        response = self.client.post(
            self.url, self._payload([inconsistent.pk]), format="json"
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self._domain_counts(), before)
        inconsistent.refresh_from_db()
        self.assertEqual(inconsistent.Estado, "Disponible")

    def test_admin_cashier_delegate_and_user_without_tenant_permissions(self):
        payload = self._payload([self.units_expensive[0].pk])
        self.client.force_authenticate(self.cashier_a)
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 403)

        now = timezone.now()
        DelegacionAdmin.objects.create(
            Miscelanea=self.tenant_a,
            CreadoPor=self.admin_a,
            Empleado=self.cashier_a,
            CodigoHash=make_password("valid-refund-delegation"),
            CodigoPrefijo="OKD",
            Modulos=["DEVOLUCIONES"],
            DuracionMinutos=30,
            CodigoExpiraEn=now + timedelta(minutes=10),
            UsadoEn=now,
            ConcedidoHasta=now + timedelta(minutes=30),
        )
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(SolicitudDevolucion.objects.get().IdUsuario_id, self.cashier_a.pk)

        no_tenant_admin = self._user(
            "admin-devoluciones-sin-tenant@example.test", None, "admin"
        )
        self.client.force_authenticate(no_tenant_admin)
        before = self._domain_counts()
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self._domain_counts(), before)

    def test_unexpected_failure_rolls_back_and_hides_exception(self):
        secret_marker = "controlled-private-refund-error"
        original_save = Inventario.save
        failing_id = self.units_cheap[0].pk

        def fail_after_first_write(instance, *args, **kwargs):
            if instance.pk == failing_id:
                raise RuntimeError(secret_marker)
            return original_save(instance, *args, **kwargs)

        with patch.object(Inventario, "save", new=fail_after_first_write):
            response = self.client.post(
                self.url,
                self._payload([self.units_expensive[0].pk, failing_id]),
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn(secret_marker, str(response.data))
        self.assertEqual(self._domain_counts(), (0, 0))
        for unit in (self.units_expensive[0], self.units_cheap[0]):
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Disponible")
        self.assertEqual(self._success_logs().count(), 0)
        failed_logs = LogAuditoria.objects.filter(
            Miscelanea=self.tenant_a,
            modulo="DEVOLUCIONES",
            resultado="FALLIDO",
        )
        self.assertEqual(failed_logs.count(), 1)
        self.assertNotIn(secret_marker, failed_logs.get().descripcion)

    def test_success_audit_failure_is_best_effort(self):
        with patch(
            "auditoria.mixins.LogAuditoria.objects.create",
            side_effect=RuntimeError("audit-only-refund"),
        ) as mocked_create:
            response = self.client.post(
                self.url,
                self._payload([self.units_expensive[0].pk]),
                format="json",
            )

        self.assertEqual(response.status_code, 201, response.data)
        mocked_create.assert_called_once()
        self.assertEqual(self._domain_counts(), (1, 1))
        self.units_expensive[0].refresh_from_db()
        self.assertEqual(self.units_expensive[0].Estado, "Devuelto")
        self.assertEqual(LogAuditoria.objects.count(), 0)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ProcesarDevolucionConcurrencyTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant = Miscelanea.objects.create(
            Nombre="Concurrencia devolución", Codigo="CONC-DEVOLUCION"
        )
        self.admin = Usuario.objects.create_user(
            Email="admin-concurrencia-devolucion@example.test",
            Password="A-strong-test-password-2026!",
            Nombre="Admin concurrencia devolución",
            Miscelanea=self.tenant,
            Rol="admin",
            Estado="Activo",
        )
        self.cashier = Usuario.objects.create_user(
            Email="cajero-concurrencia-devolucion@example.test",
            Password="A-strong-test-password-2026!",
            Nombre="Cajero concurrencia devolución",
            Miscelanea=self.tenant,
            Rol="cajero",
            Estado="Activo",
        )
        category = Categoria.objects.create(
            Miscelanea=self.tenant,
            Nombre="Categoria concurrencia devolución",
            PorcentajeGanancia=Decimal("20.00"),
        )
        provider = Proveedor.objects.create(
            Miscelanea=self.tenant, Nombre="Proveedor concurrencia devolución"
        )
        product = Producto.objects.create(
            Miscelanea=self.tenant,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre="Producto concurrencia devolución",
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
            Cantidad=1,
            PrecioCompraUnitario=Decimal("10.00"),
        )
        self.unit = detail.inventarios.get()

    def _refund_payload(self):
        return {
            "IdEntradaInventario": self.entry.pk,
            "usuarioId": self.admin.pk,
            "fechaSolicitud": timezone.now().isoformat(),
            "Estado": "Pendiente",
            "Observaciones": "Concurrencia",
            "detalles": [
                {
                    "inventarioId": self.unit.pk,
                    "MotivoRechazo": "Concurrencia",
                    "PrecioCompraUnitario": "10.00",
                    "EstadoItem": "Pendiente",
                }
            ],
        }

    @staticmethod
    def _wait_for_database_lock(pid, timeout=10):
        deadline = monotonic() + timeout
        tick = Event()
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                    [pid],
                )
                row = cursor.fetchone()
            if row and row[0] == "Lock":
                return True
            tick.wait(0.02)
        return False

    def test_sale_committing_first_makes_waiting_refund_reject(self):
        sale_at_audit = Event()
        release_sale = Event()
        refund_validation_completed = Event()
        refund_finished = Event()
        result_lock = Lock()
        results = {}
        errors = []
        real_refund_validation = ProcesarDevolucionInputSerializer.is_valid

        def blocking_sale_audit(*args, **kwargs):
            real_audit_event(*args, **kwargs)
            sale_at_audit.set()
            if not release_sale.wait(10):
                raise TimeoutError("La venta no fue liberada a tiempo")

        def record_refund_connection(serializer, *args, **kwargs):
            result = real_refund_validation(serializer, *args, **kwargs)
            refund_validation_completed.set()
            return result

        def sale_worker():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.cashier)
                response = client.post(
                    "/api/ventas/procesar/",
                    {
                        "fecha": timezone.now().isoformat(),
                        "detalles": [{"inventarioId": self.unit.pk}],
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

        def refund_worker():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.admin)
                response = client.post(
                    "/api/inventario/procesar-devolucion/",
                    self._refund_payload(),
                    format="json",
                )
                with result_lock:
                    results["refund"] = response.status_code
            except BaseException as exc:
                with result_lock:
                    errors.append(("refund", repr(exc)))
            finally:
                refund_finished.set()
                close_old_connections()

        sale_thread = Thread(target=sale_worker, daemon=True)
        refund_thread = Thread(target=refund_worker, daemon=True)
        with patch(
            "ventas.views.registrar_evento_manual",
            side_effect=blocking_sale_audit,
        ), patch(
            "inventario.views.ProcesarDevolucionInputSerializer.is_valid",
            new=record_refund_connection,
        ):
            try:
                sale_thread.start()
                self.assertTrue(sale_at_audit.wait(10))
                refund_thread.start()
                self.assertTrue(refund_validation_completed.wait(10))
                self.assertFalse(
                    refund_finished.wait(1),
                    (results, errors),
                )
            finally:
                release_sale.set()
                sale_thread.join(10)
                refund_thread.join(10)

        self.assertFalse(sale_thread.is_alive())
        self.assertFalse(refund_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, {"sale": 201, "refund": 400})
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.Estado, "Vendido")
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(DetalleVenta.objects.count(), 1)
        self.assertEqual(SolicitudDevolucion.objects.count(), 0)
        self.assertEqual(DetalleSolicitudDevolucion.objects.count(), 0)

    def test_refund_committing_first_makes_waiting_loss_reject(self):
        refund_at_audit = Event()
        release_refund = Event()
        loss_pid_ready = Event()
        result_lock = Lock()
        results = {}
        errors = []
        pids = {}

        def blocking_refund_audit(*args, **kwargs):
            real_audit_event(*args, **kwargs)
            if kwargs.get("modulo") == "DEVOLUCIONES" and kwargs.get(
                "resultado", "EXITOSO"
            ) == "EXITOSO":
                refund_at_audit.set()
                if not release_refund.wait(10):
                    raise TimeoutError("La devolución no fue liberada a tiempo")

        def refund_worker():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.admin)
                response = client.post(
                    "/api/inventario/procesar-devolucion/",
                    self._refund_payload(),
                    format="json",
                )
                with result_lock:
                    results["refund"] = response.status_code
            except BaseException as exc:
                with result_lock:
                    errors.append(("refund", repr(exc)))
            finally:
                close_old_connections()

        def loss_worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                with result_lock:
                    pids["loss"] = pid
                loss_pid_ready.set()
                client = APIClient()
                client.force_authenticate(self.admin)
                response = client.post(
                    "/api/inventario/procesar-perdida/",
                    {
                        "tipoPerdida": "Robo",
                        "detalles": [{"inventarioId": self.unit.pk}],
                    },
                    format="json",
                )
                with result_lock:
                    results["loss"] = response.status_code
            except BaseException as exc:
                with result_lock:
                    errors.append(("loss", repr(exc)))
            finally:
                close_old_connections()

        refund_thread = Thread(target=refund_worker, daemon=True)
        loss_thread = Thread(target=loss_worker, daemon=True)
        loss_wait_observed = False
        with patch(
            "inventario.views.registrar_evento_manual",
            side_effect=blocking_refund_audit,
        ):
            try:
                refund_thread.start()
                self.assertTrue(refund_at_audit.wait(10))
                loss_thread.start()
                self.assertTrue(loss_pid_ready.wait(10))
                loss_wait_observed = self._wait_for_database_lock(pids["loss"])
            finally:
                release_refund.set()
                refund_thread.join(10)
                if loss_thread.ident is not None:
                    loss_thread.join(10)

        self.assertFalse(refund_thread.is_alive())
        self.assertFalse(loss_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(loss_wait_observed)
        self.assertEqual(results, {"refund": 201, "loss": 400})
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.Estado, "Devuelto")
        self.assertEqual(SolicitudDevolucion.objects.count(), 1)
        self.assertEqual(DetalleSolicitudDevolucion.objects.count(), 1)
        self.assertEqual(Perdida.objects.count(), 0)
        self.assertEqual(DetallePerdida.objects.count(), 0)
