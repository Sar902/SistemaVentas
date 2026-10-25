from decimal import Decimal
from unittest.mock import patch

from django.db import DatabaseError, connection, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import APIException
from rest_framework.test import APIClient, APITransactionTestCase

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


RLS_RETURN_TABLES = (
    "EntradaInventario",
    "DetalleEntradaInventario",
    "Inventario",
    "SolicitudDevolucion",
    "DetalleSolicitudDevolucion",
    "Perdida",
    "DetallePerdida",
)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DevolucionesRLSRealRoleTests(
    RestrictedRLSRoleMixin,
    APITransactionTestCase,
):
    """Ejecuta el flujo real bajo un rol no propietario y sin BYPASSRLS."""

    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="RLS devoluciones A", Codigo="RLS-DEV-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="RLS devoluciones B", Codigo="RLS-DEV-B"
        )
        self.admin_a = self._user("rls-admin-a@example.test", self.tenant_a)
        self.admin_b = self._user("rls-admin-b@example.test", self.tenant_b)
        self.user_without_tenant = self._user(
            "rls-no-tenant@example.test", None
        )
        self.data_a = self._inventory_domain(self.tenant_a, self.admin_a, "A")
        self.data_b = self._inventory_domain(self.tenant_b, self.admin_b, "B")

        self.accept_a = self._pending_request(
            self.tenant_a,
            self.admin_a,
            self.data_a["entry"],
            self.data_a["units"][0],
            "Aceptar A",
        )
        self.reject_a = self._pending_request(
            self.tenant_a,
            self.admin_a,
            self.data_a["entry"],
            self.data_a["units"][1],
            "Rechazar A",
        )
        self.pending_b = self._pending_request(
            self.tenant_b,
            self.admin_b,
            self.data_b["entry"],
            self.data_b["units"][0],
            "Pendiente B",
        )
        self._assert_role_is_prepared()

    @staticmethod
    def _user(email, tenant, role="admin"):
        return Usuario.objects.create_user(
            Email=email,
            Password="A-strong-test-password-2026!",
            Nombre=email.split("@")[0],
            Miscelanea=tenant,
            Rol=role,
            Estado="Activo",
        )

    @staticmethod
    def _inventory_domain(tenant, user, suffix):
        category = Categoria.objects.create(
            Miscelanea=tenant,
            Nombre=f"Categoria RLS {suffix}",
            PorcentajeGanancia=Decimal("20.00"),
        )
        provider = Proveedor.objects.create(
            Miscelanea=tenant, Nombre=f"Proveedor RLS {suffix}"
        )
        product = Producto.objects.create(
            Miscelanea=tenant,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre=f"Producto RLS {suffix}",
        )
        entry = EntradaInventario.objects.create(
            Miscelanea=tenant,
            IdProveedor=provider,
            IdUsuario=user,
            FechaEntrada=timezone.now(),
            Total=Decimal("60.00"),
        )
        detail = DetalleEntradaInventario.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=6,
            PrecioCompraUnitario=Decimal("10.00"),
        )
        return {
            "entry": entry,
            "detail": detail,
            "units": list(detail.inventarios.order_by("IdInventario")),
        }

    @staticmethod
    def _pending_request(tenant, user, entry, unit, observations):
        unit.Estado = "Devuelto"
        unit.save(update_fields=["Estado"])
        request_record = SolicitudDevolucion.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdUsuario=user,
            Estado="Pendiente",
            Observaciones=observations,
            Fecha=timezone.now(),
        )
        detail = DetalleSolicitudDevolucion.objects.create(
            Miscelanea=tenant,
            IdSolicitudDevolucion=request_record,
            IdInventario=unit,
            PrecioCompraUnitario=unit.IdDetalleEntrada.PrecioCompraUnitario,
            EstadoItem="Pendiente",
        )
        return request_record, detail, unit

    def test_restricted_role_and_direct_queries_demonstrate_rls(self):
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
                  AND c.relname = 'SolicitudDevolucion'
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
                [list(RLS_RETURN_TABLES)],
            )
            self.assertEqual(
                cursor.fetchall(),
                sorted((table, True, True) for table in RLS_RETURN_TABLES),
            )

        self.assertIsNone(self._raw_context())
        with connection.cursor() as cursor:
            for table in RLS_RETURN_TABLES:
                cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                self.assertEqual(cursor.fetchone()[0], 0, table)

        self._set_context("")
        self.assertEqual(self._raw_context(), "")
        with connection.cursor() as cursor:
            for table in RLS_RETURN_TABLES:
                cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                self.assertEqual(cursor.fetchone()[0], 0, table)

        self._set_context(self.tenant_a.pk)
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT "IdSolicitudDevolucion", "IdMiscelanea" '
                'FROM "SolicitudDevolucion" ORDER BY "IdSolicitudDevolucion"'
            )
            visible = cursor.fetchall()
            cursor.execute(
                'SELECT COUNT(*) FROM "SolicitudDevolucion" '
                'WHERE "IdSolicitudDevolucion" = %s',
                [self.pending_b[0].pk],
            )
            foreign_count = cursor.fetchone()[0]
        self.assertEqual(
            visible,
            [(self.accept_a[0].pk, self.tenant_a.pk), (self.reject_a[0].pk, self.tenant_a.pk)],
        )
        self.assertEqual(foreign_count, 0)

    def test_creation_read_patch_and_cross_references_under_restricted_role(self):
        unit_a = self.data_a["units"][2]
        unit_b = self.data_b["units"][2]
        before_b = (
            SolicitudDevolucion.objects.filter(Miscelanea=self.tenant_b).count(),
            DetalleSolicitudDevolucion.objects.filter(Miscelanea=self.tenant_b).count(),
            unit_b.Estado,
        )
        self._enter_restricted_role()
        client = self._client(self.admin_a)

        create = client.post(
            "/api/inventario/procesar-devolucion/",
            {
                "IdEntradaInventario": self.data_a["entry"].pk,
                "Observaciones": "Creada bajo RLS",
                "detalles": [{"inventarioId": unit_a.pk}],
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201, create.data)
        self.assertEqual(self._context(), "")
        self._set_context(self.tenant_a.pk)
        created = SolicitudDevolucion.objects.get(Observaciones="Creada bajo RLS")
        self.assertEqual(created.Miscelanea_id, self.tenant_a.pk)

        retrieve = client.get(
            f"/api/inventario/solicitudes-devolucion/{created.pk}/"
        )
        self.assertEqual(retrieve.status_code, 200, retrieve.data)
        patch_response = client.patch(
            f"/api/inventario/solicitudes-devolucion/{created.pk}/",
            {"observaciones": "Observaciones bajo RLS"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200, patch_response.data)
        self.assertEqual(
            client.get(
                f"/api/inventario/solicitudes-devolucion/{self.pending_b[0].pk}/"
            ).status_code,
            404,
        )

        cross_payloads = (
            (self.data_b["entry"].pk, unit_b.pk),
            (self.data_a["entry"].pk, unit_b.pk),
            (self.data_b["entry"].pk, self.data_a["units"][3].pk),
        )
        for entry_id, unit_id in cross_payloads:
            with self.subTest(entry=entry_id, unit=unit_id):
                response = client.post(
                    "/api/inventario/procesar-devolucion/",
                    {
                        "IdEntradaInventario": entry_id,
                        "detalles": [{"inventarioId": unit_id}],
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.data)

        self.assertEqual(self._context(), "")
        self._set_context(self.tenant_a.pk)
        with connection.cursor() as cursor:
            for table, column, foreign_id in (
                ("EntradaInventario", "IdEntradaInventario", self.data_b["entry"].pk),
                ("DetalleEntradaInventario", "IdDetalleEntrada", self.data_b["detail"].pk),
                ("Inventario", "IdInventario", unit_b.pk),
            ):
                cursor.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = %s',
                    [foreign_id],
                )
                self.assertEqual(cursor.fetchone()[0], 0, table)

        self._leave_restricted_role()
        unit_a.refresh_from_db()
        unit_b.refresh_from_db()
        created.refresh_from_db()
        self.assertEqual(unit_a.Estado, "Devuelto")
        self.assertEqual(created.Observaciones, "Observaciones bajo RLS")
        self.assertEqual(
            (
                SolicitudDevolucion.objects.filter(Miscelanea=self.tenant_b).count(),
                DetalleSolicitudDevolucion.objects.filter(Miscelanea=self.tenant_b).count(),
                unit_b.Estado,
            ),
            before_b,
        )

    def test_acceptance_rejection_and_loss_stay_in_authenticated_tenant(self):
        self._enter_restricted_role()
        client = self._client(self.admin_a)

        accepted = client.post(
            f"/api/inventario/solicitudes-devolucion/{self.accept_a[0].pk}/resolver/",
            {"estado": "Aceptada"},
            format="json",
        )
        rejected = client.post(
            f"/api/inventario/solicitudes-devolucion/{self.reject_a[0].pk}/resolver/",
            {"estado": "Rechazada"},
            format="json",
        )
        foreign = client.post(
            f"/api/inventario/solicitudes-devolucion/{self.pending_b[0].pk}/resolver/",
            {"estado": "Aceptada"},
            format="json",
        )

        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(rejected.status_code, 200, rejected.data)
        self.assertEqual(foreign.status_code, 404, foreign.data)
        self.assertEqual(self._context(), "")
        self._set_context(self.tenant_a.pk)
        loss = Perdida.objects.get(TipoPerdida="RechazoDevolucion")
        loss_details = list(DetallePerdida.objects.filter(IdPerdida=loss))
        self.assertEqual(loss.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(len(loss_details), 1)
        self.assertEqual(loss_details[0].Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(loss_details[0].IdInventario_id, self.reject_a[2].pk)

        self._leave_restricted_role()
        for obj in (
            self.accept_a[0], self.accept_a[1], self.accept_a[2],
            self.reject_a[0], self.reject_a[1], self.reject_a[2],
            self.pending_b[0], self.pending_b[1], self.pending_b[2],
        ):
            obj.refresh_from_db()
        self.assertEqual(
            (self.accept_a[0].Estado, self.accept_a[1].EstadoItem, self.accept_a[2].Estado),
            ("Aceptada", "Aceptado", "Devuelto"),
        )
        self.assertEqual(
            (self.reject_a[0].Estado, self.reject_a[1].EstadoItem, self.reject_a[2].Estado),
            ("Rechazada", "Rechazado", "Perdido"),
        )
        self.assertEqual(
            (self.pending_b[0].Estado, self.pending_b[1].EstadoItem, self.pending_b[2].Estado),
            ("Pendiente", "Pendiente", "Devuelto"),
        )
        self.assertEqual(
            LogAuditoria.objects.filter(
                Miscelanea=self.tenant_a,
                accion="RESOLVER",
                resultado="EXITOSO",
            ).count(),
            2,
        )

    def test_real_authentication_clears_context_on_reused_connection(self):
        self._enter_restricted_role()
        initial_pid = self._backend_pid()

        response_a = self._client(self.admin_a).get(
            "/api/inventario/solicitudes-devolucion/"
        )
        context_after_a = self._context()
        pid_after_a = self._backend_pid()
        response_b = self._client(self.admin_b).get(
            "/api/inventario/solicitudes-devolucion/"
        )
        context_after_b = self._context()
        pid_after_b = self._backend_pid()

        self._set_context(self.tenant_b.pk)
        anonymous = APIClient().get("/api/inventario/solicitudes-devolucion/")
        context_after_anonymous = self._context()
        self._set_context(self.tenant_b.pk)
        invalid_client = APIClient()
        invalid_client.credentials(HTTP_AUTHORIZATION="Bearer token-invalido")
        invalid = invalid_client.get("/api/inventario/solicitudes-devolucion/")
        context_after_invalid = self._context()

        self._set_context(self.tenant_b.pk)
        no_tenant = self._client(self.user_without_tenant).get(
            "/api/inventario/solicitudes-devolucion/"
        )
        context_after_no_tenant = self._context()
        final_pid = self._backend_pid()

        self.assertEqual((response_a.status_code, response_b.status_code), (200, 200))
        self.assertEqual(
            {item["id"] for item in response_a.json()},
            {self.accept_a[0].pk, self.reject_a[0].pk},
        )
        self.assertEqual(
            {item["id"] for item in response_b.json()},
            {self.pending_b[0].pk},
        )
        self.assertEqual((anonymous.status_code, invalid.status_code), (401, 401))
        self.assertEqual(no_tenant.status_code, 200)
        self.assertEqual(no_tenant.json(), [])
        self.assertEqual(
            {initial_pid, pid_after_a, pid_after_b, final_pid},
            {initial_pid},
        )
        self.assertEqual(context_after_a, "")
        self.assertEqual(context_after_b, "")
        self.assertEqual(
            {
                "anonima": context_after_anonymous,
                "token_invalido": context_after_invalid,
                "usuario_sin_tenant": context_after_no_tenant,
            },
            {"anonima": "", "token_invalido": "", "usuario_sin_tenant": ""},
        )

    def test_context_is_cleared_after_permission_denial_and_rollback(self):
        self._enter_restricted_role()

        denied = self._client(self.admin_a).get("/api/auditoria/estado-bd/")
        self.assertEqual(denied.status_code, 403, denied.data)
        self.assertEqual(self._context(), "")

        def fail_after_query(_view, _request, *args, **kwargs):
            with transaction.atomic():
                self.assertEqual(self._context(), str(self.tenant_a.pk))
                with connection.cursor() as cursor:
                    cursor.execute(
                        'SELECT COUNT(*) FROM "SolicitudDevolucion"'
                    )
                    self.assertEqual(cursor.fetchone()[0], 2)
                raise APIException("fallo controlado durante la petición")

        with patch(
            "inventario.views.SolicitudDevolucionViewSet.list",
            autospec=True,
            side_effect=fail_after_query,
        ):
            failed = self._client(self.admin_a).get(
                "/api/inventario/solicitudes-devolucion/"
            )

        self.assertEqual(failed.status_code, 500)
        self.assertEqual(
            failed.json(), {"detail": "fallo controlado durante la petición"}
        )
        self.assertEqual(self._context(), "")
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM "SolicitudDevolucion"')
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_cleanup_failure_discards_connection_without_masking_response(self):
        from usuarios import tenant_context

        self._enter_restricted_role()
        original_write = tenant_context._write_tenant_context
        empty_writes = 0

        def fail_final_cleanup(value):
            nonlocal empty_writes
            if value == "":
                empty_writes += 1
                if empty_writes == 2:
                    raise DatabaseError("fallo simulado de limpieza")
            return original_write(value)

        with patch(
            "usuarios.tenant_context._write_tenant_context",
            side_effect=fail_final_cleanup,
        ), patch(
            "inventario.views.SolicitudDevolucionViewSet.list",
            autospec=True,
            side_effect=APIException("excepción original conservada"),
        ):
            response = self._client(self.admin_a).get(
                "/api/inventario/solicitudes-devolucion/"
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "excepción original conservada"})
        self.assertIsNone(connection.connection)
        self._restricted = False
