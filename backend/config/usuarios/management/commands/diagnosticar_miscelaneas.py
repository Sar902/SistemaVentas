from django.apps import apps
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.core.management.base import BaseCommand
from django.db import connection


NO_VERIFICABLE_RLS = "no verificable por RLS"


class Command(BaseCommand):
    help = "Cuenta filas y valores NULL de Miscelanea sin mostrar datos personales."

    def handle(self, *args, **options):
        self.stdout.write("tabla\ttotal\tNULL\tclasificacion")
        for model in self._tenant_models():
            table = model._meta.db_table
            if self._global_count_blocked_by_rls(table):
                self.stdout.write(
                    f"{table}\t{NO_VERIFICABLE_RLS}\t{NO_VERIFICABLE_RLS}\t{NO_VERIFICABLE_RLS}"
                )
                continue

            queryset = model._default_manager.all()
            total = queryset.count()
            null_queryset = queryset.filter(Miscelanea__isnull=True)
            null_count = null_queryset.count()
            classification = self._classify_nulls(model, null_queryset, null_count)
            self.stdout.write(f"{table}\t{total}\t{null_count}\t{classification}")

    @staticmethod
    def _tenant_models():
        models = []
        for model in apps.get_models():
            try:
                model._meta.get_field("Miscelanea")
            except FieldDoesNotExist:
                continue
            models.append(model)
        return sorted(models, key=lambda model: model._meta.db_table.lower())

    @staticmethod
    def _global_count_blocked_by_rls(table):
        """Return True when the current role cannot see all rows of an RLS table."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.relrowsecurity,
                    c.relforcerowsecurity,
                    pg_get_userbyid(c.relowner) = current_user AS is_owner,
                    r.rolsuper,
                    r.rolbypassrls
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_roles r ON r.rolname = current_user
                WHERE n.nspname = current_schema() AND c.relname = %s
                """,
                [table],
            )
            row = cursor.fetchone()
        if not row:
            return False
        rls_enabled, rls_forced, is_owner, is_superuser, bypass_rls = row
        if not rls_enabled or is_superuser or bypass_rls:
            return False
        if is_owner and not rls_forced:
            return False
        return True

    @staticmethod
    def _classify_nulls(model, null_queryset, null_count):
        if null_count == 0:
            return "sin nulos"

        if model._meta.label_lower == "usuarios.usuario":
            from usuarios.permissions import is_technical_admin

            candidates = null_queryset.filter(
                Email__iexact=settings.DJANGO_ADMIN_EMAIL,
                is_staff=True,
            )
            evidenced = sum(1 for user in candidates if is_technical_admin(user))
            return Command._classification(evidenced, null_count, "cuenta técnica")

        if model._meta.label_lower == "auditoria.logauditoria":
            evidenced = null_queryset.filter(
                usuario__isnull=True,
                accion="LOGIN",
                datos_nuevos__tenant_identificado=False,
            ).count()
            return Command._classification(
                evidenced,
                null_count,
                "login sin miscelánea identificable",
            )

        return "no intencional evidenciado"

    @staticmethod
    def _classification(evidenced, total, reason):
        if evidenced == total:
            return f"intencional: {reason}"
        if evidenced:
            return f"mixto: {reason}; resto no evidenciado"
        return "no intencional evidenciado"
