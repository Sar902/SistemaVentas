from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


RLS_TEST_ROLE = "sistemaventas_rls_test"


RLS_TABLES = (
    "Categoria",
    "Proveedor",
    "Producto",
    "EntradaInventario",
    "DetalleEntradaInventario",
    "Inventario",
    "Perdida",
    "DetallePerdida",
    "SolicitudDevolucion",
    "DetalleSolicitudDevolucion",
    "Venta",
    "DetalleVenta",
)


class TemporaryRLSNeutralizationMixin:
    """Neutraliza RLS únicamente en la base temporal y restaura sus flags."""

    def setUp(self):
        super().setUp()
        self._rls_state = {}
        expected_test_db = "test_bendicion_de_dios_fork"
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            active_db = cursor.fetchone()[0]
            host = connection.settings_dict.get("HOST")
            port = str(connection.settings_dict.get("PORT"))
            if (
                active_db != expected_test_db
                or active_db == "bendicion_de_dios_fork"
                or host != "localhost"
                or port != "5432"
            ):
                self.fail(
                    "RLS solo puede neutralizarse en la base de pruebas separada de Django."
                )
            cursor.execute(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema() AND c.relname = ANY(%s)
                """,
                [list(RLS_TABLES)],
            )
            self._rls_state = {
                row[0]: (row[1], row[2]) for row in cursor.fetchall()
            }
            self.assertEqual(set(self._rls_state), set(RLS_TABLES))
            for table in self._rls_state:
                quoted = connection.ops.quote_name(table)
                cursor.execute(f"ALTER TABLE {quoted} NO FORCE ROW LEVEL SECURITY")
                cursor.execute(f"ALTER TABLE {quoted} DISABLE ROW LEVEL SECURITY")

    def _post_teardown(self):
        """Restaura RLS después del rollback/flush del caso de prueba."""
        try:
            super()._post_teardown()
        finally:
            self._restore_rls()

    def _restore_rls(self):
        if not self._rls_state:
            return
        with connection.cursor() as cursor:
            for table, (enabled, forced) in self._rls_state.items():
                quoted = connection.ops.quote_name(table)
                cursor.execute(
                    f"ALTER TABLE {quoted} "
                    f"{'ENABLE' if enabled else 'DISABLE'} ROW LEVEL SECURITY"
                )
                cursor.execute(
                    f"ALTER TABLE {quoted} "
                    f"{'FORCE' if forced else 'NO FORCE'} ROW LEVEL SECURITY"
                )


class RestrictedRLSRoleMixin:
    """Gestiona una conexión reutilizada bajo el rol RLS exclusivo de pruebas."""

    rls_test_role = RLS_TEST_ROLE

    def setUp(self):
        super().setUp()
        self._restricted = False
        self._original_conn_max_age = connection.settings_dict.get("CONN_MAX_AGE")
        self.addCleanup(self._leave_restricted_role)

    @staticmethod
    def _token(user):
        return str(RefreshToken.for_user(user).access_token)

    def _client(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._token(user)}")
        return client

    def _assert_role_is_prepared(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreatedb,
                       rolcreaterole, rolreplication
                FROM pg_roles WHERE rolname = %s
                """,
                [self.rls_test_role],
            )
            row = cursor.fetchone()
        if row is None:
            raise AssertionError(
                "Ejecute manage.py preparar_rol_rls_pruebas antes de esta suite."
            )
        if any(row):
            raise AssertionError("El rol RLS de pruebas tiene atributos inseguros.")

    def _enter_restricted_role(self):
        if self._restricted:
            return
        connection.close()
        connection.settings_dict["CONN_MAX_AGE"] = None
        connection.connect()
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone()[0], "test_bendicion_de_dios_fork")
            cursor.execute(f'SET ROLE "{self.rls_test_role}"')
        self._restricted = True

    def _leave_restricted_role(self):
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
        connection.settings_dict["CONN_MAX_AGE"] = self._original_conn_max_age
        self._restricted = False

    @staticmethod
    def _context():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_setting(%s, true)", ["app.miscelanea_id"]
            )
            return cursor.fetchone()[0] or ""

    @staticmethod
    def _raw_context():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_setting(%s, true)", ["app.miscelanea_id"]
            )
            return cursor.fetchone()[0]

    @staticmethod
    def _backend_pid():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            return cursor.fetchone()[0]

    @staticmethod
    def _set_context(value):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config(%s, %s, false)",
                ["app.miscelanea_id", str(value) if value is not None else ""],
            )
