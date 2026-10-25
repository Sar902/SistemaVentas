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
class ReturnLegacyClosureTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Cierre devoluciones A", Codigo="CIERRE-DEV-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Cierre devoluciones B", Codigo="CIERRE-DEV-B"
        )
        self.admin_a = self._user("admin-cierre-a@example.test", self.tenant_a, "admin")
        self.cashier_a = self._user(
            "cajero-cierre-a@example.test", self.tenant_a, "cajero"
        )
        self.admin_b = self._user("admin-cierre-b@example.test", self.tenant_b, "admin")
        category_a = self._category(self.tenant_a, "Categoria cierre A")
        category_b = self._category(self.tenant_b, "Categoria cierre B")
        provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor cierre A"
        )
        provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor cierre B"
        )
        product_a = self._product(
            self.tenant_a, category_a, provider_a, "Producto cierre A"
        )
        product_b = self._product(
            self.tenant_b, category_b, provider_b, "Producto cierre B"
        )
        self.entry_a = self._entry(self.tenant_a, provider_a, self.admin_a)
        self.entry_b = self._entry(self.tenant_b, provider_b, self.admin_b)
        self.purchase_detail_a = self._purchase_detail(
            self.tenant_a, self.entry_a, product_a, 8, Decimal("10.25")
        )
        self.purchase_detail_b = self._purchase_detail(
            self.tenant_b, self.entry_b, product_b, 3, Decimal("3.40")
        )
        self.units_a = list(
            self.purchase_detail_a.inventarios.order_by("IdInventario")
        )
        self.units_b = list(
            self.purchase_detail_b.inventarios.order_by("IdInventario")
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
    def _category(tenant, name):
        return Categoria.objects.create(
            Miscelanea=tenant,
            Nombre=name,
            PorcentajeGanancia=Decimal("20.00"),
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
    def _purchase_detail(tenant, entry, product, quantity, cost):
        return DetalleEntradaInventario.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=quantity,
            PrecioCompraUnitario=cost,
        )

    def _request(self, unit, *, tenant=None, entry=None, user=None, observations="Original"):
        tenant = tenant or self.tenant_a
        entry = entry or self.entry_a
        user = user or self.admin_a
        unit.Estado = "Devuelto"
        unit.FechaMovimiento = timezone.now() - timedelta(hours=2)
        unit.save(update_fields=["Estado", "FechaMovimiento"])
        request_record = SolicitudDevolucion.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdUsuario=user,
            Estado="Pendiente",
            Observaciones=observations,
            Fecha=timezone.now() - timedelta(hours=1),
        )
        detail = DetalleSolicitudDevolucion.objects.create(
            Miscelanea=tenant,
            IdSolicitudDevolucion=request_record,
            IdInventario=unit,
            MotivoRechazo="Empaque deteriorado",
            PrecioCompraUnitario=unit.IdDetalleEntrada.PrecioCompraUnitario,
            EstadoItem="Pendiente",
        )
        return request_record, detail

    @staticmethod
    def _url(request_record):
        return f"/api/inventario/solicitudes-devolucion/{request_record.pk}/"

    @staticmethod
    def _snapshot(request_record, detail, unit):
        request_record.refresh_from_db()
        detail.refresh_from_db()
        unit.refresh_from_db()
        return (
            request_record.Estado,
            request_record.Observaciones,
            request_record.IdEntradaInventario_id,
            request_record.IdUsuario_id,
            request_record.Miscelanea_id,
            request_record.Fecha,
            detail.EstadoItem,
            detail.IdInventario_id,
            detail.PrecioCompraUnitario,
            detail.Miscelanea_id,
            unit.Estado,
            unit.FechaMovimiento,
            unit.IdDetalleEntrada_id,
            unit.Miscelanea_id,
        )

    def test_patch_updates_only_observations_and_preserves_domain_fields(self):
        request_record, detail = self._request(self.units_a[0])
        before = self._snapshot(request_record, detail, self.units_a[0])

        response = self.client.patch(
            self._url(request_record),
            {"observaciones": "Observacion actualizada de forma segura"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        after = self._snapshot(request_record, detail, self.units_a[0])
        self.assertEqual(after[1], "Observacion actualizada de forma segura")
        self.assertEqual(after[:1] + after[2:], before[:1] + before[2:])

    def test_patch_rejects_forbidden_and_mixed_fields_without_changes(self):
        request_record, detail = self._request(self.units_a[1])
        before = self._snapshot(request_record, detail, self.units_a[1])
        cases = (
            {"estado": "Aceptada"},
            {"usuarioId": self.admin_b.pk},
            {"entradaInventarioId": self.entry_b.pk},
            {"fecha": timezone.now().isoformat()},
            {"detalles": []},
            {"miscelaneaId": self.tenant_b.pk},
            {"Miscelanea": self.tenant_b.pk},
            {"total": "999.99"},
            {"precioCompraUnitario": "0.01"},
            {"Observaciones": "Nombre incorrecto"},
            {"observaciones": "No aplicar", "estado": "Aceptada"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.patch(
                    self._url(request_record), payload, format="json"
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(
                    self._snapshot(request_record, detail, self.units_a[1]), before
                )

    def test_patch_rejects_resolved_request_without_changes(self):
        request_record, detail = self._request(self.units_a[2])
        resolve = self.client.post(
            f"{self._url(request_record)}resolver/",
            {"estado": "Aceptada"},
            format="json",
        )
        self.assertEqual(resolve.status_code, 200, resolve.data)
        before = self._snapshot(request_record, detail, self.units_a[2])

        response = self.client.patch(
            self._url(request_record),
            {"observaciones": "No debe aplicarse"},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self._snapshot(request_record, detail, self.units_a[2]), before)

    def test_patch_permissions_delegate_and_cross_tenant_isolation(self):
        own, own_detail = self._request(self.units_a[3])
        foreign, foreign_detail = self._request(
            self.units_b[0],
            tenant=self.tenant_b,
            entry=self.entry_b,
            user=self.admin_b,
            observations="Extranjera",
        )
        foreign_before = self._snapshot(foreign, foreign_detail, self.units_b[0])
        self.assertEqual(
            self.client.patch(
                self._url(foreign), {"observaciones": "Cruce"}, format="json"
            ).status_code,
            404,
        )
        self.assertEqual(
            self._snapshot(foreign, foreign_detail, self.units_b[0]), foreign_before
        )

        self.client.force_authenticate(self.cashier_a)
        self.assertEqual(
            self.client.patch(
                self._url(own), {"observaciones": "Sin permiso"}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/inventario/devolver-stock/",
                {"solicitud_id": own.pk},
                format="json",
            ).status_code,
            403,
        )
        now = timezone.now()
        DelegacionAdmin.objects.create(
            Miscelanea=self.tenant_a,
            CreadoPor=self.admin_a,
            Empleado=self.cashier_a,
            CodigoHash=make_password("valid-return-closure-delegation"),
            CodigoPrefijo="RCL",
            Modulos=["DEVOLUCIONES"],
            DuracionMinutos=30,
            CodigoExpiraEn=now + timedelta(minutes=10),
            UsadoEn=now,
            ConcedidoHasta=now + timedelta(minutes=30),
        )
        delegated = self.client.patch(
            self._url(own), {"observaciones": "Delegado autorizado"}, format="json"
        )
        self.assertEqual(delegated.status_code, 200, delegated.data)
        self.assertEqual(
            self.client.post(
                "/api/inventario/devolver-stock/",
                {"solicitud_id": own.pk},
                format="json",
            ).status_code,
            400,
        )
        own.refresh_from_db()
        own_detail.refresh_from_db()
        self.assertEqual(own.Observaciones, "Delegado autorizado")
        self.assertEqual((own.Estado, own_detail.EstadoItem), ("Pendiente", "Pendiente"))

        anonymous = APIClient()
        self.assertEqual(
            anonymous.patch(
                self._url(own), {"observaciones": "Anonimo"}, format="json"
            ).status_code,
            401,
        )
        self.assertEqual(
            anonymous.post(
                "/api/inventario/devolver-stock/",
                {"solicitud_id": own.pk},
                format="json",
            ).status_code,
            401,
        )

    def test_devolver_stock_is_closed_without_queries_or_domain_changes(self):
        request_record, detail = self._request(self.units_a[4])
        resolve = self.client.post(
            f"{self._url(request_record)}resolver/",
            {"estado": "Aceptada"},
            format="json",
        )
        self.assertEqual(resolve.status_code, 200, resolve.data)
        before = self._snapshot(request_record, detail, self.units_a[4])
        counts = (
            SolicitudDevolucion.objects.count(),
            DetalleSolicitudDevolucion.objects.count(),
            Inventario.objects.count(),
            Perdida.objects.count(),
            DetallePerdida.objects.count(),
        )

        with patch(
            "inventario.views.SolicitudDevolucion.objects.get",
            wraps=SolicitudDevolucion.objects.get,
        ) as request_get, patch(
            "inventario.views.DetalleSolicitudDevolucion.objects.filter",
            wraps=DetalleSolicitudDevolucion.objects.filter,
        ) as detail_filter:
            response = self.client.post(
                "/api/inventario/devolver-stock/",
                {"solicitud_id": request_record.pk},
                format="json",
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("resolver", response.json()["error"].lower())
        request_get.assert_not_called()
        detail_filter.assert_not_called()
        self.assertEqual(self._snapshot(request_record, detail, self.units_a[4]), before)
        self.assertEqual(
            (
                SolicitudDevolucion.objects.count(),
                DetalleSolicitudDevolucion.objects.count(),
                Inventario.objects.count(),
                Perdida.objects.count(),
                DetallePerdida.objects.count(),
            ),
            counts,
        )

    def test_create_and_resolve_routes_remain_operational(self):
        unit = self.units_a[5]
        create = self.client.post(
            "/api/inventario/procesar-devolucion/",
            {
                "IdEntradaInventario": self.entry_a.pk,
                "Observaciones": "Creada por ruta oficial",
                "detalles": [
                    {
                        "inventarioId": unit.pk,
                        "MotivoRechazo": "Empaque deteriorado",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201, create.data)
        request_record = SolicitudDevolucion.objects.get(
            Observaciones="Creada por ruta oficial"
        )
        resolve = self.client.post(
            f"{self._url(request_record)}resolver/",
            {"estado": "Rechazada"},
            format="json",
        )
        self.assertEqual(resolve.status_code, 200, resolve.data)
        request_record.refresh_from_db()
        unit.refresh_from_db()
        self.assertEqual((request_record.Estado, unit.Estado), ("Rechazada", "Perdido"))
        self.assertEqual(Perdida.objects.filter(TipoPerdida="RechazoDevolucion").count(), 1)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ReturnPatchResolutionConcurrencyTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant = Miscelanea.objects.create(
            Nombre="Concurrencia cierre devolucion", Codigo="CONC-CIERRE-DEV"
        )
        self.admin = Usuario.objects.create_user(
            Email="admin-concurrencia-cierre@example.test",
            Password="A-strong-test-password-2026!",
            Nombre="Admin concurrencia cierre",
            Miscelanea=self.tenant,
            Rol="admin",
            Estado="Activo",
        )
        category = Categoria.objects.create(
            Miscelanea=self.tenant,
            Nombre="Categoria concurrencia cierre",
            PorcentajeGanancia=Decimal("20.00"),
        )
        provider = Proveedor.objects.create(
            Miscelanea=self.tenant, Nombre="Proveedor concurrencia cierre"
        )
        product = Producto.objects.create(
            Miscelanea=self.tenant,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre="Producto concurrencia cierre",
        )
        self.entry = EntradaInventario.objects.create(
            Miscelanea=self.tenant,
            IdProveedor=provider,
            IdUsuario=self.admin,
            FechaEntrada=timezone.now(),
            Total=Decimal("10.00"),
        )
        purchase_detail = DetalleEntradaInventario.objects.create(
            Miscelanea=self.tenant,
            IdEntradaInventario=self.entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=1,
            PrecioCompraUnitario=Decimal("10.00"),
        )
        self.unit = purchase_detail.inventarios.get()
        self.unit.Estado = "Devuelto"
        self.unit.save(update_fields=["Estado"])
        self.request_record = SolicitudDevolucion.objects.create(
            Miscelanea=self.tenant,
            IdEntradaInventario=self.entry,
            IdUsuario=self.admin,
            Estado="Pendiente",
            Observaciones="Original concurrente",
            Fecha=timezone.now(),
        )
        DetalleSolicitudDevolucion.objects.create(
            Miscelanea=self.tenant,
            IdSolicitudDevolucion=self.request_record,
            IdInventario=self.unit,
            PrecioCompraUnitario=Decimal("10.00"),
            EstadoItem="Pendiente",
        )

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

    def test_patch_waits_for_resolution_and_cannot_update_resolved_request(self):
        resolver_at_audit = Event()
        release_resolver = Event()
        patch_pid_ready = Event()
        result_lock = Lock()
        results = {}
        errors = []
        pids = {}

        def blocking_audit(*args, **kwargs):
            real_audit_event(*args, **kwargs)
            if kwargs.get("accion") == "RESOLVER" and kwargs.get(
                "resultado", "EXITOSO"
            ) == "EXITOSO":
                resolver_at_audit.set()
                if not release_resolver.wait(10):
                    raise TimeoutError("La resolucion no fue liberada a tiempo")

        def resolver_worker():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.admin)
                response = client.post(
                    f"/api/inventario/solicitudes-devolucion/{self.request_record.pk}/resolver/",
                    {"estado": "Aceptada"},
                    format="json",
                )
                with result_lock:
                    results["resolver"] = response.status_code
            except BaseException as exc:
                with result_lock:
                    errors.append(("resolver", repr(exc)))
            finally:
                close_old_connections()

        def patch_worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                with result_lock:
                    pids["patch"] = pid
                patch_pid_ready.set()
                client = APIClient()
                client.force_authenticate(self.admin)
                response = client.patch(
                    f"/api/inventario/solicitudes-devolucion/{self.request_record.pk}/",
                    {"observaciones": "No debe aplicarse despues"},
                    format="json",
                )
                with result_lock:
                    results["patch"] = response.status_code
            except BaseException as exc:
                with result_lock:
                    errors.append(("patch", repr(exc)))
            finally:
                close_old_connections()

        resolver_thread = Thread(target=resolver_worker, daemon=True)
        patch_thread = Thread(target=patch_worker, daemon=True)
        lock_observed = False
        with patch(
            "inventario.views.registrar_evento_manual", side_effect=blocking_audit
        ):
            try:
                resolver_thread.start()
                self.assertTrue(resolver_at_audit.wait(10), (results, errors))
                patch_thread.start()
                self.assertTrue(patch_pid_ready.wait(10), (results, errors))
                lock_observed = self._wait_for_database_lock(pids["patch"])
            finally:
                release_resolver.set()
                resolver_thread.join(10)
                if patch_thread.ident is not None:
                    patch_thread.join(10)

        self.assertFalse(resolver_thread.is_alive())
        self.assertFalse(patch_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(lock_observed)
        self.assertEqual(results, {"resolver": 200, "patch": 400})
        self.request_record.refresh_from_db()
        self.unit.refresh_from_db()
        detail = self.request_record.detallesolicituddevolucion_set.get()
        self.assertEqual(self.request_record.Estado, "Aceptada")
        self.assertEqual(self.request_record.Observaciones, "Original concurrente")
        self.assertEqual(detail.EstadoItem, "Aceptado")
        self.assertEqual(self.unit.Estado, "Devuelto")
