from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework_simplejwt.tokens import RefreshToken

from auditoria.models import LogAuditoria
from usuarios.models import DelegacionAdmin, Usuario

from .test_t1_isolation import ApplicationIsolationTestCase


TENANT_REQUIRED = {"detail": "La cuenta no tiene una miscelánea asignada."}


class TenantAdminApiGuardTests(ApplicationIsolationTestCase):
    """Las APIs administrativas de negocio no convierten NULL en un tenant."""

    def setUp(self):
        super().setUp()
        self.tenantless_admin = self._user(
            "tenantless-admin-api@example.test", "Admin histórico", None, "admin"
        )
        self.tenantless_cashier = self._user(
            "tenantless-cashier-api@example.test", "Cajero histórico", None, "cajero"
        )

    def authenticate_with_jwt(self, user):
        token = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def assert_tenant_required(self, response):
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(response.json(), TENANT_REQUIRED)

    def successful_user_audits(self):
        return LogAuditoria.objects.filter(
            modulo="USUARIOS",
            resultado="EXITOSO",
        ).count()

    def test_tenantless_admin_cannot_create_user(self):
        self.authenticate_with_jwt(self.tenantless_admin)
        before = (Usuario.objects.count(), self.successful_user_audits())

        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(
                "/api/usuarios/usuarios/",
                {
                    "nombre": "No crear",
                    "email": "must-not-exist@example.test",
                    "password": "Test-only-password-2026!",
                    "rol": "cajero",
                    "estado": "Activo",
                },
                format="json",
            )

        self.assert_tenant_required(response)
        self.assertEqual((Usuario.objects.count(), self.successful_user_audits()), before)
        sql = "\n".join(item["sql"] for item in queries.captured_queries).upper()
        self.assertNotIn("INSERT INTO \"USUARIO\"", sql)
        self.assertNotIn("INSERT INTO \"LOGAUDITORIA\"", sql)

    def test_tenantless_admin_cannot_read_or_mutate_tenant_admin_actions(self):
        self.authenticate_with_jwt(self.tenantless_admin)
        self.client.raise_request_exception = False
        old_email = self.tenantless_cashier.Email
        old_delegation = (
            self.delegation_a.Revocado,
            self.delegation_a.RevocadoEn,
            self.delegation_a.RevocadoPor_id,
        )
        before = (
            Usuario.objects.count(),
            DelegacionAdmin.objects.count(),
            self.successful_user_audits(),
        )
        cases = (
            ("get", "/api/usuarios/cajeros-activos/", None),
            ("get", "/api/usuarios/delegacion-admin/", None),
            (
                "post",
                "/api/usuarios/delegacion-admin/crear/",
                {
                    "empleadoId": self.tenantless_cashier.pk,
                    "modulos": ["USUARIOS"],
                    "duracionHoras": 1,
                },
            ),
            (
                "post",
                "/api/usuarios/delegacion-admin/activar/",
                {"codigo": "ADM-NO-TENANT"},
            ),
            (
                "post",
                f"/api/usuarios/delegacion-admin/{self.delegation_a.pk}/revocar/",
                {},
            ),
            (
                "patch",
                f"/api/usuarios/cambiar-email-directo/{self.tenantless_cashier.pk}/",
                {"email": "must-not-change@example.test"},
            ),
        )

        for method, url, payload in cases:
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, payload, format="json")
                self.assert_tenant_required(response)

        self.tenantless_cashier.refresh_from_db()
        self.delegation_a.refresh_from_db()
        self.assertEqual(self.tenantless_cashier.Email, old_email)
        self.assertEqual(
            (
                self.delegation_a.Revocado,
                self.delegation_a.RevocadoEn,
                self.delegation_a.RevocadoPor_id,
            ),
            old_delegation,
        )
        self.assertEqual(
            (
                Usuario.objects.count(),
                DelegacionAdmin.objects.count(),
                self.successful_user_audits(),
            ),
            before,
        )

    @override_settings(DJANGO_ADMIN_EMAIL="technical-tenant-admin@example.test")
    def test_technical_identity_keeps_global_contracts_but_not_business_admin_apis(self):
        technical = self._user(
            "technical-tenant-admin@example.test", "Identidad técnica", None, "admin"
        )
        technical.is_staff = True
        technical.save(update_fields=["is_staff"])
        self.authenticate_with_jwt(technical)
        self.client.raise_request_exception = False

        self.assertEqual(self.client.get("/api/usuarios/me/").status_code, 200)
        generic = self.client.get("/api/usuarios/usuarios/")
        self.assertEqual(generic.status_code, 200)
        self.assertEqual(generic.json()["results"], [])
        self.assertEqual(self.client.get("/api/auditoria/estado-bd/").status_code, 200)

        cases = (
            ("get", "/api/usuarios/cajeros-activos/", None),
            ("get", "/api/usuarios/delegacion-admin/", None),
            (
                "post",
                "/api/usuarios/usuarios/",
                {
                    "nombre": "No crear técnico",
                    "email": "technical-must-not-create@example.test",
                    "password": "Test-only-password-2026!",
                    "estado": "Activo",
                },
            ),
            (
                "post",
                "/api/usuarios/delegacion-admin/crear/",
                {"empleadoId": self.tenantless_cashier.pk, "modulos": ["USUARIOS"]},
            ),
            ("post", "/api/usuarios/delegacion-admin/activar/", {"codigo": "X"}),
            (
                "post",
                f"/api/usuarios/delegacion-admin/{self.delegation_a.pk}/revocar/",
                {},
            ),
            (
                "patch",
                f"/api/usuarios/cambiar-email-directo/{self.tenantless_cashier.pk}/",
                {"email": "technical-must-not-change@example.test"},
            ),
        )
        for method, url, payload in cases:
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, payload, format="json")
                self.assert_tenant_required(response)

    def test_tenant_admin_delegate_isolation_and_seller_permissions_are_preserved(self):
        self.authenticate_with_jwt(self.admin_a)
        cashiers = self.client.get("/api/usuarios/cajeros-activos/")
        self.assertEqual(cashiers.status_code, 200)
        self.assertEqual({row["id"] for row in cashiers.json()}, {self.cashier_a.pk})

        created = self.client.post(
            "/api/usuarios/usuarios/",
            {
                "nombre": "Creado en A",
                "email": "created-in-a@example.test",
                "password": "Test-only-password-2026!",
                "rol": "cajero",
                "estado": "Activo",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        created_user = Usuario.objects.get(pk=created.json()["id"])
        self.assertEqual(created_user.Miscelanea_id, self.tenant_a.pk)
        own_email = self.client.patch(
            f"/api/usuarios/cambiar-email-directo/{created_user.pk}/",
            {"email": "created-in-a-updated@example.test"},
            format="json",
        )
        self.assertEqual(own_email.status_code, 200, own_email.content)

        cross_email = self.client.patch(
            f"/api/usuarios/cambiar-email-directo/{self.cashier_b.pk}/",
            {"email": "cross-tenant-must-not-change@example.test"},
            format="json",
        )
        self.assertEqual(cross_email.status_code, 404)
        cross_delegation = self.client.post(
            "/api/usuarios/delegacion-admin/crear/",
            {"empleadoId": self.cashier_b.pk, "modulos": ["USUARIOS"]},
            format="json",
        )
        self.assertEqual(cross_delegation.status_code, 404)

        grant = self.client.post(
            "/api/usuarios/delegacion-admin/crear/",
            {
                "empleadoId": self.cashier_a.pk,
                "modulos": ["USUARIOS"],
                "duracionHoras": 1,
            },
            format="json",
        )
        self.assertEqual(grant.status_code, 201, grant.content)
        listed = self.client.get("/api/usuarios/delegacion-admin/")
        self.assertEqual(listed.status_code, 200)
        listed_ids = {row["id"] for row in listed.json()}
        self.assertIn(grant.json()["id"], listed_ids)
        self.assertNotIn(self.delegation_b.pk, listed_ids)
        revoked = self.client.post(
            f"/api/usuarios/delegacion-admin/{self.delegation_a.pk}/revocar/",
            {},
            format="json",
        )
        self.assertEqual(revoked.status_code, 200, revoked.content)

        self.authenticate_with_jwt(self.cashier_a)
        activation = self.client.post(
            "/api/usuarios/delegacion-admin/activar/",
            {"codigo": grant.json()["codigo"]},
            format="json",
        )
        self.assertEqual(activation.status_code, 200, activation.content)
        delegated_create = self.client.post(
            "/api/usuarios/usuarios/",
            {
                "nombre": "Creado por delegado",
                "email": "delegated-in-a@example.test",
                "password": "Test-only-password-2026!",
                "rol": "cajero",
                "estado": "Activo",
            },
            format="json",
        )
        self.assertEqual(delegated_create.status_code, 201, delegated_create.content)
        self.assertEqual(
            Usuario.objects.get(pk=delegated_create.json()["id"]).Miscelanea_id,
            self.tenant_a.pk,
        )

        seller_b = self._user(
            "seller-b-tenant-admin@example.test", "Vendedor B", self.tenant_b, "vendedor"
        )
        self.authenticate_with_jwt(seller_b)
        denied = self.client.post(
            "/api/usuarios/usuarios/",
            {
                "nombre": "No crear vendedor",
                "email": "seller-must-not-create@example.test",
                "password": "Test-only-password-2026!",
                "estado": "Activo",
            },
            format="json",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(
            self.client.get("/api/usuarios/cajeros-activos/").status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/usuarios/delegacion-admin/").status_code,
            403,
        )
