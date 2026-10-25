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
    EntradaInventario,
    Inventario,
    Perdida,
)
from usuarios.models import DelegacionAdmin, Miscelanea, Usuario
from usuarios.test_utils import TemporaryRLSNeutralizationMixin
from ventas.models import DetalleVenta, Venta


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ProcesarPerdidaTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    url = "/api/inventario/procesar-perdida/"

    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Perdidas A", Codigo="PERDIDAS-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Perdidas B", Codigo="PERDIDAS-B"
        )
        self.admin_a = self._user("admin-perdidas-a@example.test", self.tenant_a, "admin")
        self.cashier_a = self._user(
            "cajero-perdidas-a@example.test", self.tenant_a, "cajero"
        )
        self.admin_b = self._user("admin-perdidas-b@example.test", self.tenant_b, "admin")
        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoria perdidas A",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Categoria perdidas B",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor perdidas A"
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor perdidas B"
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
            Nombre="Producto perdidas B",
        )
        self.entry_a = EntradaInventario.objects.create(
            Miscelanea=self.tenant_a,
            IdProveedor=self.provider_a,
            IdUsuario=self.admin_a,
            FechaEntrada=timezone.now() - timedelta(days=2),
            Total=Decimal("27.30"),
        )
        self.detail_a_expensive = self._detail(
            self.tenant_a, self.entry_a, self.product_a, 2, Decimal("10.25")
        )
        self.detail_a_cheap = self._detail(
            self.tenant_a, self.entry_a, self.product_a, 2, Decimal("3.40")
        )
        self.units_expensive = list(
            self.detail_a_expensive.inventarios.order_by("IdInventario")
        )
        self.units_cheap = list(
            self.detail_a_cheap.inventarios.order_by("IdInventario")
        )
        self.entry_b = EntradaInventario.objects.create(
            Miscelanea=self.tenant_b,
            IdProveedor=self.provider_b,
            IdUsuario=self.admin_b,
            FechaEntrada=timezone.now() - timedelta(days=1),
            Total=Decimal("1.00"),
        )
        self.detail_b = self._detail(
            self.tenant_b, self.entry_b, self.product_b, 1, Decimal("1.00")
        )
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
    def _detail(tenant, entry, product, quantity, cost):
        return DetalleEntradaInventario.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=quantity,
            PrecioCompraUnitario=cost,
        )

    @staticmethod
    def _payload(unit_ids, loss_type="Vencimiento", **extra):
        payload = {
            "tipoPerdida": loss_type,
            "detalles": [{"inventarioId": unit_id} for unit_id in unit_ids],
        }
        payload.update(extra)
        return payload

    @staticmethod
    def _domain_counts():
        return Perdida.objects.count(), DetallePerdida.objects.count()

    def _success_logs(self):
        return LogAuditoria.objects.filter(
            Miscelanea=self.tenant_a,
            accion="PROCESAR",
            modulo="PERDIDAS",
            resultado="EXITOSO",
        )

    def test_server_uses_authenticated_user_time_and_exact_purchase_lot_costs(self):
        old_date = (timezone.now() - timedelta(days=90)).isoformat()
        start = timezone.now()
        response = self.client.post(
            self.url,
            self._payload(
                [self.units_expensive[0].pk, self.units_cheap[0].pk],
                usuarioId=self.admin_b.pk,
                fecha=old_date,
                total="999999.99",
                detalles=[
                    {
                        "inventarioId": self.units_expensive[0].pk,
                        "precioCompraUnitario": "999.99",
                    },
                    {
                        "inventarioId": self.units_cheap[0].pk,
                        "precioCompraUnitario": "888.88",
                    },
                ],
            ),
            format="json",
        )
        end = timezone.now()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.json(), {"message": "Pérdida procesada"})
        loss = Perdida.objects.get()
        self.assertEqual(loss.IdUsuario_id, self.admin_a.pk)
        self.assertEqual(loss.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(loss.Total, Decimal("13.65"))
        self.assertLessEqual(start, loss.Fecha)
        self.assertLessEqual(loss.Fecha, end)
        details = list(
            DetallePerdida.objects.filter(IdPerdida=loss).order_by("IdInventario_id")
        )
        expected_costs = {
            self.units_expensive[0].pk: Decimal("10.25"),
            self.units_cheap[0].pk: Decimal("3.40"),
        }
        self.assertEqual(
            {detail.IdInventario_id: detail.PrecioCompraUnitario for detail in details},
            expected_costs,
        )
        for unit in (self.units_expensive[0], self.units_cheap[0]):
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Perdido")
            self.assertLessEqual(start, unit.FechaMovimiento)
            self.assertLessEqual(unit.FechaMovimiento, end)
        self.assertEqual(self._success_logs().count(), 1)

    def test_ignored_fields_may_be_omitted(self):
        response = self.client.post(
            self.url,
            self._payload([self.units_expensive[1].pk], loss_type="Otra"),
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        loss = Perdida.objects.get()
        detail = DetallePerdida.objects.get(IdPerdida=loss)
        self.assertEqual(loss.IdUsuario_id, self.admin_a.pk)
        self.assertEqual(loss.Total, Decimal("10.25"))
        self.assertEqual(detail.PrecioCompraUnitario, Decimal("10.25"))

    def test_empty_malformed_and_duplicate_inventory_ids_are_rejected(self):
        valid_id = self.units_expensive[0].pk
        cases = (
            [],
            {},
            [None],
            [{"inventarioId": None}],
            [{"inventarioId": True}],
            [{"inventarioId": 1.5}],
            [{"inventarioId": "1.5"}],
            [{"inventarioId": 0}],
            [{"inventarioId": -1}],
            [{"inventarioId": 999999999, "precioCompraUnitario": "1.00"}],
            [
                {"inventarioId": valid_id, "precioCompraUnitario": "1.00"},
                {"inventarioId": valid_id, "precioCompraUnitario": "1.00"},
            ],
        )
        for details in cases:
            with self.subTest(details=details):
                before = self._domain_counts()
                response = self.client.post(
                    self.url,
                    {
                        "usuarioId": self.admin_a.pk,
                        "tipoPerdida": "Otra",
                        "fecha": timezone.now().isoformat(),
                        "total": "1.00",
                        "detalles": details,
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self._domain_counts(), before)
                self.units_expensive[0].refresh_from_db()
                self.assertEqual(self.units_expensive[0].Estado, "Disponible")

    def test_manual_loss_types_are_allowlisted(self):
        allowed = ("Vencimiento", "Dañado", "Robo", "Uso Interno", "Otra")
        type_detail = self._detail(
            self.tenant_a,
            self.entry_a,
            self.product_a,
            8,
            Decimal("2.00"),
        )
        units = list(type_detail.inventarios.order_by("IdInventario"))
        for loss_type, unit in zip(allowed, units[:5]):
            with self.subTest(loss_type=loss_type):
                response = self.client.post(
                    self.url,
                    self._payload(
                        [unit.pk],
                        loss_type=loss_type,
                        usuarioId=self.admin_a.pk,
                        fecha=timezone.now().isoformat(),
                        total="2.00",
                        detalles=[
                            {
                                "inventarioId": unit.pk,
                                "precioCompraUnitario": "2.00",
                            }
                        ],
                    ),
                    format="json",
                )
                self.assertEqual(response.status_code, 201, response.data)

        for loss_type, unit in zip(
            ("RechazoDevolucion", "NoAutorizado", None), units[5:]
        ):
            with self.subTest(loss_type=loss_type):
                before = self._domain_counts()
                response = self.client.post(
                    self.url,
                    self._payload([unit.pk], loss_type=loss_type),
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self._domain_counts(), before)

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
                    self.url,
                    self._payload(
                        [unit.pk],
                        usuarioId=self.admin_a.pk,
                        fecha=timezone.now().isoformat(),
                        total="1.00",
                        detalles=[
                            {
                                "inventarioId": unit.pk,
                                "precioCompraUnitario": "1.00",
                            }
                        ],
                    ),
                    format="json",
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
            self.url,
            self._payload(
                [inconsistent.pk],
                usuarioId=self.admin_a.pk,
                fecha=timezone.now().isoformat(),
                total="1.00",
                detalles=[
                    {
                        "inventarioId": inconsistent.pk,
                        "precioCompraUnitario": "1.00",
                    }
                ],
            ),
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self._domain_counts(), before)
        inconsistent.refresh_from_db()
        self.assertEqual(inconsistent.Estado, "Disponible")

    def test_cashier_and_delegations_keep_current_permissions(self):
        self.client.force_authenticate(self.cashier_a)
        payload = self._payload([self.units_expensive[0].pk])
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 403)

        now = timezone.now()
        DelegacionAdmin.objects.create(
            Miscelanea=self.tenant_b,
            CreadoPor=self.admin_b,
            Empleado=self.cashier_a,
            CodigoHash=make_password("wrong-tenant-loss-delegation"),
            CodigoPrefijo="WRG",
            Modulos=["PERDIDAS"],
            DuracionMinutos=30,
            CodigoExpiraEn=now + timedelta(minutes=10),
            UsadoEn=now,
            ConcedidoHasta=now + timedelta(minutes=30),
        )
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 403)

        DelegacionAdmin.objects.create(
            Miscelanea=self.tenant_a,
            CreadoPor=self.admin_a,
            Empleado=self.cashier_a,
            CodigoHash=make_password("valid-loss-delegation"),
            CodigoPrefijo="OKL",
            Modulos=["PERDIDAS"],
            DuracionMinutos=30,
            CodigoExpiraEn=now + timedelta(minutes=10),
            UsadoEn=now,
            ConcedidoHasta=now + timedelta(minutes=30),
        )
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Perdida.objects.get().IdUsuario_id, self.cashier_a.pk)

    def test_unexpected_failure_rolls_back_and_does_not_expose_exception(self):
        secret_marker = "controlled-private-loss-error"
        original_save = Inventario.save
        failing_id = self.units_cheap[0].pk

        def fail_after_first_write(instance, *args, **kwargs):
            if instance.pk == failing_id:
                raise RuntimeError(secret_marker)
            return original_save(instance, *args, **kwargs)

        with patch.object(Inventario, "save", new=fail_after_first_write):
            response = self.client.post(
                self.url,
                self._payload(
                    [self.units_expensive[0].pk, failing_id],
                    usuarioId=self.admin_a.pk,
                    fecha=timezone.now().isoformat(),
                    total="13.65",
                    detalles=[
                        {
                            "inventarioId": self.units_expensive[0].pk,
                            "precioCompraUnitario": "10.25",
                        },
                        {
                            "inventarioId": failing_id,
                            "precioCompraUnitario": "3.40",
                        },
                    ],
                ),
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn(secret_marker, str(response.data))
        self.assertEqual(self._domain_counts(), (0, 0))
        for unit in (self.units_expensive[0], self.units_cheap[0]):
            unit.refresh_from_db()
            self.assertEqual(unit.Estado, "Disponible")
        failed_logs = LogAuditoria.objects.filter(
            Miscelanea=self.tenant_a,
            accion="PROCESAR",
            modulo="PERDIDAS",
            resultado="FALLIDO",
        )
        self.assertEqual(failed_logs.count(), 1)
        self.assertNotIn(secret_marker, failed_logs.get().descripcion)

    def test_success_audit_failure_is_best_effort(self):
        with patch(
            "auditoria.mixins.LogAuditoria.objects.create",
            side_effect=RuntimeError("audit-only-loss"),
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
        self.assertEqual(self.units_expensive[0].Estado, "Perdido")
        self.assertEqual(LogAuditoria.objects.count(), 0)

    def test_total_overflow_is_rejected_without_changes(self):
        detail = self._detail(
            self.tenant_a,
            self.entry_a,
            self.product_a,
            2,
            Decimal("9999999999.99"),
        )
        units = list(detail.inventarios.order_by("IdInventario"))
        before = self._domain_counts()
        response = self.client.post(
            self.url,
            self._payload(
                [unit.pk for unit in units],
                usuarioId=self.admin_a.pk,
                fecha=timezone.now().isoformat(),
                total="0.00",
                detalles=[
                    {"inventarioId": unit.pk, "precioCompraUnitario": "0.00"}
                    for unit in units
                ],
            ),
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self._domain_counts(), before)
        self.assertEqual(
            set(Inventario.objects.filter(pk__in=[unit.pk for unit in units]).values_list("Estado", flat=True)),
            {"Disponible"},
        )


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PerdidaVentaConcurrencyTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant = Miscelanea.objects.create(
            Nombre="Concurrencia perdida", Codigo="CONC-PERDIDA"
        )
        self.admin = Usuario.objects.create_user(
            Email="admin-concurrencia-perdida@example.test",
            Password="A-strong-test-password-2026!",
            Nombre="Admin concurrencia perdida",
            Miscelanea=self.tenant,
            Rol="admin",
            Estado="Activo",
        )
        self.cashier = Usuario.objects.create_user(
            Email="cajero-concurrencia-perdida@example.test",
            Password="A-strong-test-password-2026!",
            Nombre="Cajero concurrencia perdida",
            Miscelanea=self.tenant,
            Rol="cajero",
            Estado="Activo",
        )
        category = Categoria.objects.create(
            Miscelanea=self.tenant,
            Nombre="Categoria concurrencia perdida",
            PorcentajeGanancia=Decimal("20.00"),
        )
        provider = Proveedor.objects.create(
            Miscelanea=self.tenant, Nombre="Proveedor concurrencia perdida"
        )
        product = Producto.objects.create(
            Miscelanea=self.tenant,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre="Producto concurrencia perdida",
        )
        entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant,
            IdProveedor=provider,
            IdUsuario=self.admin,
            FechaEntrada=timezone.now(),
            Total=Decimal("10.00"),
        )
        detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant,
            IdEntradaInventario=entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=1,
            PrecioCompraUnitario=Decimal("10.00"),
        )
        self.unit = detail.inventarios.get()

    def test_sale_committing_first_forces_waiting_loss_to_reject(self):
        sale_at_audit = Event()
        release_sale = Event()
        loss_pid_ready = Event()
        poll_tick = Event()
        result_lock = Lock()
        results = {}
        errors = []
        pids = {}

        def blocking_sale_audit(*args, **kwargs):
            real_audit_event(*args, **kwargs)
            sale_at_audit.set()
            if not release_sale.wait(10):
                raise TimeoutError("La venta no fue liberada a tiempo")

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
                        "usuarioId": self.admin.pk,
                        "tipoPerdida": "Robo",
                        "fecha": timezone.now().isoformat(),
                        "total": "10.00",
                        "detalles": [
                            {
                                "inventarioId": self.unit.pk,
                                "precioCompraUnitario": "10.00",
                            }
                        ],
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

        sale_thread = Thread(target=sale_worker, daemon=True)
        loss_thread = Thread(target=loss_worker, daemon=True)
        loss_wait_observed = False
        with patch(
            "ventas.views.registrar_evento_manual",
            side_effect=blocking_sale_audit,
        ):
            try:
                sale_thread.start()
                self.assertTrue(sale_at_audit.wait(10))
                loss_thread.start()
                self.assertTrue(loss_pid_ready.wait(10))
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                            [pids["loss"]],
                        )
                        row = cursor.fetchone()
                    if row and row[0] == "Lock":
                        loss_wait_observed = True
                        break
                    poll_tick.wait(0.02)
            finally:
                release_sale.set()
                sale_thread.join(10)
                loss_thread.join(10)

        self.assertFalse(sale_thread.is_alive())
        self.assertFalse(loss_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(loss_wait_observed)
        self.assertEqual(results, {"sale": 201, "loss": 400})
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.Estado, "Vendido")
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(DetalleVenta.objects.count(), 1)
        self.assertEqual(Perdida.objects.count(), 0)
        self.assertEqual(DetallePerdida.objects.count(), 0)
