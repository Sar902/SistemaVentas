"""Aprovisiona el rol restringido usado por las pruebas RLS.

Uso reproducible, siempre desde la configuración local autorizada::

    python manage.py preparar_rol_rls_pruebas

El comando deriva ``test_bendicion_de_dios_fork`` desde la configuración,
se conecta directamente a esa base y se niega a operar sobre la base de
desarrollo. No crea ni modifica políticas, propietarios o credenciales.
"""

import psycopg2
from psycopg2 import sql

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


ROLE_NAME = "sistemaventas_rls_test"
ROLE_MARKER = "SistemaVentas: rol restringido exclusivo para pruebas RLS"

SELECT_TABLES = (
    "Miscelanea",
    "Usuario",
    "DelegacionAdmin",
    "Categoria",
    "Proveedor",
    "Producto",
    "EntradaInventario",
    "DetalleEntradaInventario",
    "Inventario",
    "SolicitudDevolucion",
    "DetalleSolicitudDevolucion",
    "Perdida",
    "DetallePerdida",
    "Venta",
    "DetalleVenta",
)

INSERT_TABLES = (
    "Categoria",
    "Proveedor",
    "Producto",
    "EntradaInventario",
    "DetalleEntradaInventario",
    "Inventario",
    "SolicitudDevolucion",
    "DetalleSolicitudDevolucion",
    "Perdida",
    "DetallePerdida",
    "LogAuditoria",
    "Venta",
    "DetalleVenta",
)

UPDATE_TABLES = (
    "Categoria",
    "Proveedor",
    "Producto",
    "EntradaInventario",
    "DetalleEntradaInventario",
    "Inventario",
    "SolicitudDevolucion",
    "DetalleSolicitudDevolucion",
    "Venta",
    "DetalleVenta",
)

DELETE_TABLES = (
    "Categoria",
    "Proveedor",
    "Producto",
    "EntradaInventario",
    "DetalleEntradaInventario",
    "Inventario",
)

SEQUENCES = (
    "Categoria_IdCategoria_seq",
    "Proveedor_IdProveedor_seq",
    "Producto_IdProducto_seq",
    "EntradaInventario_IdEntradaInventario_seq",
    "DetalleEntradaInventario_IdDetalleEntrada_seq",
    "Inventario_IdInventario_seq",
    "SolicitudDevolucion_IdSolicitudDevolucion_seq",
    "DetalleSolicitudDevolucion_IdDetalleSolicitudDevolucion_seq",
    "Perdida_IdPerdida_seq",
    "DetallePerdida_IdDetallePerdida_seq",
    "LogAuditoria_IdLog_seq",
    "Venta_IdVenta_seq",
    "DetalleVenta_IdDetalleVenta_seq",
)


class Command(BaseCommand):
    help = "Crea/verifica el rol NOLOGIN mínimo para pruebas RLS en la base temporal."

    def handle(self, *args, **options):
        config, test_name = self._validated_configuration()
        development_name = str(config["NAME"])

        with self._connect(config, test_name) as test_connection:
            with test_connection.cursor() as cursor:
                created = self._create_or_validate_role(cursor)
                self._assert_database_target(cursor, test_name)
                self._assert_no_ownership_or_memberships(cursor)
                self._assert_no_unexpected_test_privileges(cursor)
                self._grant_minimum_privileges(cursor)
                public_evidence = self._public_evidence(cursor)
            test_connection.commit()

        with self._connect(config, development_name) as development_connection:
            with development_connection.cursor() as cursor:
                self._assert_database_target(cursor, development_name)
                self._assert_no_development_table_privileges(cursor)

        action = "creado" if created else "verificado y reutilizado"
        self.stdout.write(f"ROL\t{ROLE_NAME}\t{action}")
        self.stdout.write(
            "ATRIBUTOS\tNOLOGIN,NOSUPERUSER,NOBYPASSRLS,NOCREATEDB,"
            "NOCREATEROLE,NOREPLICATION"
        )
        self.stdout.write(
            "PUBLIC\t"
            f"CONNECT={public_evidence[0]};USAGE_SCHEMA={public_evidence[1]};"
            f"privilegios_tabla={public_evidence[2]};"
            f"privilegios_secuencia={public_evidence[3]}"
        )
        self.stdout.write(
            f"DESTINO\t{test_name}; sin privilegios de tabla o secuencia en "
            f"{development_name}"
        )

    @staticmethod
    def _validated_configuration():
        config = settings.DATABASES["default"]
        name = str(config.get("NAME", ""))
        host = str(config.get("HOST", ""))
        port = str(config.get("PORT", ""))
        test_name = str(config.get("TEST", {}).get("NAME") or f"test_{name}")
        if (
            name != "bendicion_de_dios_fork"
            or host not in ("localhost", "127.0.0.1")
            or port != "5432"
            or test_name != "test_bendicion_de_dios_fork"
        ):
            raise CommandError("La configuración no apunta al entorno local autorizado.")
        return config, test_name

    @staticmethod
    def _connect(config, database_name):
        return psycopg2.connect(
            dbname=database_name,
            user=config["USER"],
            password=config["PASSWORD"],
            host=config["HOST"],
            port=config["PORT"],
        )

    @staticmethod
    def _assert_database_target(cursor, expected):
        cursor.execute("SELECT current_database()")
        actual = cursor.fetchone()[0]
        if actual != expected:
            raise CommandError(f"Base inesperada: {actual}.")

    @staticmethod
    def _create_or_validate_role(cursor):
        cursor.execute(
            """
            SELECT oid, rolcanlogin, rolsuper, rolbypassrls, rolcreatedb,
                   rolcreaterole, rolreplication,
                   shobj_description(oid, 'pg_authid')
            FROM pg_roles WHERE rolname = %s
            """,
            [ROLE_NAME],
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS "
                    "NOCREATEDB NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(ROLE_NAME))
            )
            cursor.execute(
                sql.SQL("COMMENT ON ROLE {} IS %s").format(
                    sql.Identifier(ROLE_NAME)
                ),
                [ROLE_MARKER],
            )
            return True

        _, can_login, superuser, bypass, create_db, create_role, replication, marker = row
        if any((can_login, superuser, bypass, create_db, create_role, replication)):
            raise CommandError("El rol existente tiene atributos incompatibles.")
        if marker != ROLE_MARKER:
            raise CommandError("El rol existente no tiene la marca de este proyecto.")
        return False

    @staticmethod
    def _assert_no_ownership_or_memberships(cursor):
        cursor.execute("SELECT oid FROM pg_roles WHERE rolname = %s", [ROLE_NAME])
        role_oid = cursor.fetchone()[0]
        cursor.execute(
            "SELECT 1 FROM pg_auth_members WHERE roleid = %s OR member = %s LIMIT 1",
            [role_oid, role_oid],
        )
        if cursor.fetchone():
            raise CommandError("El rol de pruebas no debe pertenecer ni contener otros roles.")
        cursor.execute("SELECT 1 FROM pg_database WHERE datdba = %s LIMIT 1", [role_oid])
        if cursor.fetchone():
            raise CommandError("El rol de pruebas no puede ser propietario de bases.")
        cursor.execute(
            """
            SELECT 1 FROM pg_namespace WHERE nspowner = %s
            UNION ALL
            SELECT 1 FROM pg_class WHERE relowner = %s
            LIMIT 1
            """,
            [role_oid, role_oid],
        )
        if cursor.fetchone():
            raise CommandError("El rol de pruebas no puede ser propietario de objetos.")

    @staticmethod
    def _assert_no_unexpected_test_privileges(cursor):
        expected = {
            table: {
                "SELECT": table in SELECT_TABLES,
                "INSERT": table in INSERT_TABLES,
                "UPDATE": table in UPDATE_TABLES,
                "DELETE": table in DELETE_TABLES,
            }
            for table in set(
                SELECT_TABLES + INSERT_TABLES + UPDATE_TABLES + DELETE_TABLES
            )
        }
        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        )
        for (table,) in cursor.fetchall():
            qualified = f'"public"."{table}"'
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cursor.execute(
                    "SELECT has_table_privilege(%s, %s, %s)",
                    [ROLE_NAME, qualified, privilege],
                )
                effective = cursor.fetchone()[0]
                allowed = expected.get(table, {}).get(privilege, False)
                if effective and not allowed:
                    raise CommandError(
                        f"El rol existente tiene {privilege} inesperado sobre {table}."
                    )
        cursor.execute(
            "SELECT sequencename FROM pg_sequences WHERE schemaname = current_schema()"
        )
        for (sequence,) in cursor.fetchall():
            qualified = f'"public"."{sequence}"'
            for privilege in ("USAGE", "SELECT", "UPDATE"):
                cursor.execute(
                    "SELECT has_sequence_privilege(%s, %s, %s)",
                    [ROLE_NAME, qualified, privilege],
                )
                effective = cursor.fetchone()[0]
                allowed = sequence in SEQUENCES and privilege == "USAGE"
                if effective and not allowed:
                    raise CommandError(
                        f"El rol existente tiene {privilege} inesperado sobre {sequence}."
                    )

    @staticmethod
    def _grant_minimum_privileges(cursor):
        for privilege, tables in (
            ("SELECT", SELECT_TABLES),
            ("INSERT", INSERT_TABLES),
            ("UPDATE", UPDATE_TABLES),
            ("DELETE", DELETE_TABLES),
        ):
            cursor.execute(
                sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                    sql.SQL(privilege),
                    sql.SQL(", ").join(sql.Identifier(table) for table in tables),
                    sql.Identifier(ROLE_NAME),
                )
            )
        cursor.execute(
            sql.SQL("GRANT SELECT ({}) ON TABLE {} TO {}").format(
                sql.Identifier("IdLog"),
                sql.Identifier("LogAuditoria"),
                sql.Identifier(ROLE_NAME),
            )
        )
        cursor.execute(
            sql.SQL("GRANT USAGE ON SEQUENCE {} TO {}").format(
                sql.SQL(", ").join(sql.Identifier(sequence) for sequence in SEQUENCES),
                sql.Identifier(ROLE_NAME),
            )
        )

    @staticmethod
    def _assert_no_development_table_privileges(cursor):
        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        )
        for (table,) in cursor.fetchall():
            qualified = f'"public"."{table}"'
            cursor.execute(
                "SELECT has_table_privilege(%s, %s, %s)",
                [ROLE_NAME, qualified, "SELECT,INSERT,UPDATE,DELETE"],
            )
            if cursor.fetchone()[0]:
                raise CommandError(
                    f"El rol obtuvo privilegios de tabla en desarrollo: {table}."
                )
        cursor.execute(
            "SELECT sequencename FROM pg_sequences WHERE schemaname = current_schema()"
        )
        for (sequence,) in cursor.fetchall():
            qualified = f'"public"."{sequence}"'
            cursor.execute(
                "SELECT has_sequence_privilege(%s, %s, %s)",
                [ROLE_NAME, qualified, "USAGE,SELECT,UPDATE"],
            )
            if cursor.fetchone()[0]:
                raise CommandError(
                    f"El rol obtuvo privilegios de secuencia en desarrollo: {sequence}."
                )

    @staticmethod
    def _public_evidence(cursor):
        cursor.execute(
            """
            SELECT has_database_privilege('public', current_database(), 'CONNECT'),
                   has_schema_privilege('public', current_schema(), 'USAGE')
            """
        )
        database_connect, schema_usage = cursor.fetchone()
        table_privileges = 0
        for table in set(
            SELECT_TABLES + INSERT_TABLES + UPDATE_TABLES + DELETE_TABLES
        ):
            qualified = f'"public"."{table}"'
            cursor.execute(
                "SELECT has_table_privilege('public', %s, 'SELECT,INSERT,UPDATE,DELETE')",
                [qualified],
            )
            table_privileges += int(cursor.fetchone()[0])
        sequence_privileges = 0
        for sequence in SEQUENCES:
            qualified = f'"public"."{sequence}"'
            cursor.execute(
                "SELECT has_sequence_privilege('public', %s, 'USAGE,SELECT,UPDATE')",
                [qualified],
            )
            sequence_privileges += int(cursor.fetchone()[0])
        return database_connect, schema_usage, table_privileges, sequence_privileges
