from decimal import Decimal
from threading import Event, Lock, Thread
from unittest.mock import patch

from django.db import connection, close_old_connections
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from auditoria.models import LogAuditoria
from auditoria.mixins import registrar_evento_manual as real_audit_event
from catalogo.models import Categoria, Producto, Proveedor
from inventario.models import (
    DetalleEntradaInventario,
    DetallePerdida,
    EntradaInventario,
    Inventario,
    Perdida,
)
from inventario.serializers import ProcesarPerdidaInputSerializer
from usuarios.models import Miscelanea, Usuario
from usuarios.test_utils import RLS_TEST_ROLE, RestrictedRLSRoleMixin
from ventas.models import DetalleVenta, Venta


RLS_LOSS_TABLES = ("Perdida", "DetallePerdida")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PerdidasRLSRealRoleTests(
    RestrictedRLSRoleMixin,
    APITransactionTestCase,
):
    """Valida pérdidas manuales con RLS aplicado al rol restringido."""

    reset_sequences = True
    loss_url = "/api/inventario/procesar-perdida/"

    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="RLS pérdidas A", Codigo="RLS-PER-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="RLS pérdidas B", Codigo="RLS-PER-B"
        )
        self.admin_a = self._user(
            "rls-loss-admin-a@example.test", self.tenant_a, "admin"
        )
        self.cashier_a = self._user(
            "rls-loss-cashier-a@example.test", self.tenant_a, "cajero"
        )
        self.admin_b = self._user(
            "rls-loss-admin-b@example.test", self.tenant_b, "admin"
        )
        self.tenantless_admin = self._user(
            "rls-loss-no-tenant@example.test", None, "admin"
        )
        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoría RLS pérdidas A",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Categoría RLS pérdidas B",
            PorcentajeGanancia=Decimal("20.00"),
        )
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor RLS pérdidas A"
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor RLS pérdidas B"
        )
        self.product_a = self._product(
            self.tenant_a, self.category_a, self.provider_a,
            "Producto RLS pérdidas A",
        )
        self.product_b = self._product(
            self.tenant_b, self.category_b, self.provider_b,
            "Producto RLS pérdidas B",
        )
        self.entry_a = self._entry(self.tenant_a, self.admin_a, self.provider_a)
        self.entry_b = self._entry(self.tenant_b, self.admin_b, self.provider_b)
        self.detail_a_expensive = self._detail(
            self.tenant_a, self.entry_a, self.product_a, 4, "10.25"
        )
        self.detail_a_cheap = self._detail(
            self.tenant_a, self.entry_a, self.product_a, 3, "3.40"
        )
        self.detail_b = self._detail(
            self.tenant_b, self.entry_b, self.product_b, 3, "50.00"
        )
        self.units_a_expensive = list(
            self.detail_a_expensive.inventarios.order_by("IdInventario")
        )
        self.units_a_cheap = list(
            self.detail_a_cheap.inventarios.order_by("IdInventario")
        )
        self.units_b = list(self.detail_b.inventarios.order_by("IdInventario"))
        self.loss_a = self._historical_loss(
            self.tenant_a, self.admin_a, self.units_a_expensive[0], "10.25"
        )
        self.loss_b = self._historical_loss(
            self.tenant_b, self.admin_b, self.units_b[0], "50.00"
        )
        self._assert_role_is_prepared()

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
    def _product(tenant, category, provider, name):
        return Producto.objects.create(
            Miscelanea=tenant,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre=name,
        )

    @staticmethod
    def _entry(tenant, user, provider):
        return EntradaInventario.objects.create(
            Miscelanea=tenant,
            IdProveedor=provider,
            IdUsuario=user,
            FechaEntrada=timezone.now(),
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
            PrecioCompraUnitario=Decimal(cost),
        )

    @staticmethod
    def _historical_loss(tenant, user, unit, cost):
        loss = Perdida.objects.create(
            Miscelanea=tenant,
            IdUsuario=user,
            TipoPerdida="Otra",
            Fecha=timezone.now(),
            Total=Decimal(cost),
        )
        DetallePerdida.objects.create(
            Miscelanea=tenant,
            IdPerdida=loss,
            IdInventario=unit,
            PrecioCompraUnitario=Decimal(cost),
        )
        unit.Estado = "Perdido"
        unit.save(update_fields=["Estado"])
        return loss

    @staticmethod
    def _payload(unit_ids, **extra):
        payload = {
            "tipoPerdida": "Dañado",
            "detalles": [{"inventarioId": unit_id} for unit_id in unit_ids],
        }
        payload.update(extra)
        return payload

    @staticmethod
    def _domain_snapshot(unit_ids):
        return (
            list(
                Perdida.objects.order_by("IdPerdida").values_list(
                    "IdPerdida", "Miscelanea_id", "IdUsuario_id",
                    "TipoPerdida", "Fecha", "Total",
                )
            ),
            list(
                DetallePerdida.objects.order_by("IdDetallePerdida").values_list(
                    "IdDetallePerdida", "Miscelanea_id", "IdPerdida_id",
                    "IdInventario_id", "PrecioCompraUnitario",
                )
            ),
            list(
                Inventario.objects.filter(pk__in=unit_ids)
                .order_by("IdInventario")
                .values_list("IdInventario", "Estado", "FechaMovimiento")
            ),
        )

    def test_direct_queries_prove_loss_rls_for_restricted_role(self):
        own_detail_id = self.loss_a.detalleperdida_set.get().pk
        foreign_detail_id = self.loss_b.detalleperdida_set.get().pk
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
                  AND c.relname = 'Perdida'
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
                [list(RLS_LOSS_TABLES)],
            )
            self.assertEqual(
                cursor.fetchall(),
                sorted((table, True, True) for table in RLS_LOSS_TABLES),
            )

        self.assertIsNone(self._raw_context())
        for empty_context in (None, ""):
            if empty_context == "":
                self._set_context("")
            with connection.cursor() as cursor:
                for table in RLS_LOSS_TABLES:
                    cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                    self.assertEqual(cursor.fetchone()[0], 0, table)

        self._set_context(self.tenant_a.pk)
        with connection.cursor() as cursor:
            for table, column, own_id, foreign_id in (
                ("Perdida", "IdPerdida", self.loss_a.pk, self.loss_b.pk),
                (
                    "DetallePerdida", "IdDetallePerdida",
                    own_detail_id, foreign_detail_id,
                ),
            ):
                cursor.execute(f'SELECT "{column}" FROM "{table}"')
                visible = {row[0] for row in cursor.fetchall()}
                self.assertIn(own_id, visible)
                self.assertNotIn(foreign_id, visible)

    def test_http_reads_are_isolated_and_generic_writes_remain_closed(self):
        own_detail = self.loss_a.detalleperdida_set.get()
        foreign_detail = self.loss_b.detalleperdida_set.get()
        before = self._domain_snapshot(
            [self.units_a_expensive[0].pk, self.units_b[0].pk]
        )
        self._enter_restricted_role()
        client = self._client(self.admin_a)

        losses = client.get("/api/inventario/perdidas/")
        self.assertEqual(losses.status_code, 200, losses.data)
        self.assertEqual({row["id"] for row in losses.json()}, {self.loss_a.pk})
        self.assertEqual(self._context(), "")
        details = client.get("/api/inventario/detalles-perdida/")
        self.assertEqual(details.status_code, 200, details.data)
        self.assertEqual({row["id"] for row in details.json()}, {own_detail.pk})
        self.assertEqual(self._context(), "")

        for url, expected in (
            (f"/api/inventario/perdidas/{self.loss_a.pk}/", 200),
            (f"/api/inventario/perdidas/{self.loss_b.pk}/", 404),
            (f"/api/inventario/detalles-perdida/{own_detail.pk}/", 200),
            (f"/api/inventario/detalles-perdida/{foreign_detail.pk}/", 404),
        ):
            response = client.get(url)
            self.assertEqual(response.status_code, expected, response.data)
            self.assertEqual(self._context(), "")

        for method, url, data in (
            ("post", "/api/inventario/perdidas/", {}),
            ("patch", f"/api/inventario/perdidas/{self.loss_a.pk}/", {}),
            ("delete", f"/api/inventario/perdidas/{self.loss_a.pk}/", None),
            ("post", "/api/inventario/detalles-perdida/", {}),
            ("patch", f"/api/inventario/detalles-perdida/{own_detail.pk}/", {}),
            ("delete", f"/api/inventario/detalles-perdida/{own_detail.pk}/", None),
        ):
            response = getattr(client, method)(url, data, format="json")
            self.assertEqual(response.status_code, 405, response.data)
            self.assertEqual(self._context(), "")

        self._leave_restricted_role()
        self.assertEqual(
            self._domain_snapshot(
                [self.units_a_expensive[0].pk, self.units_b[0].pk]
            ),
            before,
        )

    def test_valid_loss_uses_exact_costs_and_server_identity(self):
        expensive = self.units_a_expensive[1]
        cheap = self.units_a_cheap[0]
        started = timezone.now()
        self._enter_restricted_role()
        response = self._client(self.admin_a).post(
            self.loss_url,
            self._payload(
                [expensive.pk, cheap.pk],
                usuarioId=self.admin_b.pk,
                fecha="2000-01-01T00:00:00Z",
                total="999.99",
                detalles=[
                    {"inventarioId": expensive.pk, "precioCompraUnitario": "0.01"},
                    {"inventarioId": cheap.pk, "precioCompraUnitario": "0.02"},
                ],
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.json(), {"message": "Pérdida procesada"})
        self.assertEqual(self._context(), "")
        self._set_context(self.tenant_a.pk)
        loss = Perdida.objects.exclude(pk=self.loss_a.pk).get()
        details = list(loss.detalleperdida_set.order_by("IdDetallePerdida"))
        self.assertEqual(loss.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(loss.IdUsuario_id, self.admin_a.pk)
        self.assertEqual(loss.Total, Decimal("13.65"))
        self.assertGreaterEqual(loss.Fecha, started)
        self.assertEqual(
            [detail.PrecioCompraUnitario for detail in details],
            [Decimal("10.25"), Decimal("3.40")],
        )
        self.assertEqual(
            {detail.Miscelanea_id for detail in details}, {self.tenant_a.pk}
        )
        self.assertEqual(
            set(
                Inventario.objects.filter(pk__in=[expensive.pk, cheap.pk])
                .values_list("Estado", flat=True)
            ),
            {"Perdido"},
        )

    def test_foreign_duplicate_and_unavailable_units_are_rejected(self):
        available = self.units_a_expensive[1]
        unavailable = self.units_a_expensive[2]
        unavailable.Estado = "Vendido"
        unavailable.save(update_fields=["Estado"])
        unit_ids = [available.pk, unavailable.pk, self.units_b[1].pk]
        before = self._domain_snapshot(unit_ids)
        self._enter_restricted_role()
        client = self._client(self.admin_a)

        for payload in (
            self._payload([self.units_b[1].pk]),
            self._payload([available.pk, available.pk]),
            self._payload([unavailable.pk]),
        ):
            response = client.post(self.loss_url, payload, format="json")
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(self._context(), "")

        self._leave_restricted_role()
        self.assertEqual(self._domain_snapshot(unit_ids), before)

    def test_cross_tenant_purchase_relation_is_rejected_without_writes(self):
        inconsistent = Inventario.objects.create(
            Miscelanea=self.tenant_a,
            IdDetalleEntrada=self.detail_b,
            Estado="Disponible",
            FechaMovimiento=timezone.now(),
        )
        before = self._domain_snapshot([inconsistent.pk])
        self._enter_restricted_role()

        response = self._client(self.admin_a).post(
            self.loss_url, self._payload([inconsistent.pk]), format="json"
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertEqual(self._domain_snapshot([inconsistent.pk]), before)

    def test_tenantless_user_is_rejected_without_changes(self):
        unit = self.units_a_cheap[1]
        before = self._domain_snapshot([unit.pk])
        self._enter_restricted_role()

        response = self._client(self.tenantless_admin).post(
            self.loss_url, self._payload([unit.pk]), format="json"
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertEqual(self._domain_snapshot([unit.pk]), before)

    def test_controlled_failure_after_writes_rolls_back_completely(self):
        unit = self.units_a_cheap[1]
        before = self._domain_snapshot([unit.pk])
        calls = 0

        def fail_stock_save(instance, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("fallo controlado RLS pérdida")

        self._enter_restricted_role()
        with patch.object(
            Inventario,
            "save",
            autospec=True,
            side_effect=fail_stock_save,
        ):
            response = self._client(self.admin_a).post(
                self.loss_url, self._payload([unit.pk]), format="json"
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(calls, 1)
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertEqual(self._domain_snapshot([unit.pk]), before)

    def test_sale_and_loss_serialize_under_restricted_role(self):
        unit = self.units_a_cheap[1]
        sale_at_audit = Event()
        release_sale = Event()
        loss_validated = Event()
        result_lock = Lock()
        results = {}
        response_data = {}
        errors = []
        roles = {}
        contexts = {}
        active_contexts = {}
        pids = {}
        original_conn_max_age = connection.settings_dict.get("CONN_MAX_AGE")
        connection.settings_dict["CONN_MAX_AGE"] = None
        sale_token = self._token(self.cashier_a)
        loss_token = self._token(self.admin_a)
        real_loss_validation = ProcesarPerdidaInputSerializer.is_valid

        def blocking_sale_audit(*args, **kwargs):
            real_audit_event(*args, **kwargs)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_user, pg_backend_pid(), "
                    "current_setting(%s, true)",
                    ["app.miscelanea_id"],
                )
                roles["sale"], pids["sale"], active_contexts["sale"] = (
                    cursor.fetchone()
                )
            sale_at_audit.set()
            if not release_sale.wait(20):
                raise TimeoutError("La venta no fue liberada a tiempo")

        def mark_loss_validation(serializer, *args, **kwargs):
            result = real_loss_validation(serializer, *args, **kwargs)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_user, pg_backend_pid(), "
                    "current_setting(%s, true)",
                    ["app.miscelanea_id"],
                )
                roles["loss"], pids["loss"], active_contexts["loss"] = (
                    cursor.fetchone()
                )
            loss_validated.set()
            return result

        def worker(name, token, url, payload):
            close_old_connections()
            try:
                connection.connect()
                with connection.cursor() as cursor:
                    cursor.execute(f'SET ROLE "{RLS_TEST_ROLE}"')
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                response = client.post(url, payload, format="json")
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT current_setting(%s, true)",
                        ["app.miscelanea_id"],
                    )
                    context = cursor.fetchone()[0] or ""
                with result_lock:
                    results[name] = response.status_code
                    response_data[name] = response.data
                    contexts[name] = context
            except BaseException as exc:
                with result_lock:
                    errors.append((name, repr(exc)))
            finally:
                if connection.connection is not None:
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "SELECT set_config(%s, %s, false)",
                                ["app.miscelanea_id", ""],
                            )
                            cursor.execute("RESET ROLE")
                    finally:
                        connection.close()

        sale_thread = Thread(
            target=worker,
            args=(
                "sale",
                sale_token,
                "/api/ventas/procesar/",
                {
                    "fecha": timezone.now().isoformat(),
                    "detalles": [{"inventarioId": unit.pk}],
                },
            ),
            daemon=True,
        )
        loss_thread = Thread(
            target=worker,
            args=("loss", loss_token, self.loss_url, self._payload([unit.pk])),
            daemon=True,
        )
        with patch(
            "ventas.views.registrar_evento_manual",
            side_effect=blocking_sale_audit,
        ), patch.object(
            ProcesarPerdidaInputSerializer,
            "is_valid",
            autospec=True,
            side_effect=mark_loss_validation,
        ):
            try:
                sale_thread.start()
                self.assertTrue(sale_at_audit.wait(10))
                loss_thread.start()
                self.assertTrue(loss_validated.wait(10))
            finally:
                release_sale.set()
                sale_thread.join(10)
                loss_thread.join(10)
                connection.settings_dict["CONN_MAX_AGE"] = original_conn_max_age

        self.assertFalse(sale_thread.is_alive())
        self.assertFalse(loss_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(roles, {"sale": RLS_TEST_ROLE, "loss": RLS_TEST_ROLE})
        self.assertEqual(
            active_contexts,
            {"sale": str(self.tenant_a.pk), "loss": str(self.tenant_a.pk)},
        )
        self.assertEqual(len(set(pids.values())), 2, pids)
        self.assertEqual(contexts, {"sale": "", "loss": ""})
        self.assertEqual(
            results,
            {"sale": 201, "loss": 400},
            response_data,
        )
        unit.refresh_from_db()
        self.assertEqual(unit.Estado, "Vendido")
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(DetalleVenta.objects.count(), 1)
        self.assertEqual(Perdida.objects.count(), 2)
        self.assertEqual(DetallePerdida.objects.count(), 2)
        self.assertEqual(
            LogAuditoria.objects.filter(
                modulo="PERDIDAS", resultado="EXITOSO"
            ).count(),
            0,
        )
