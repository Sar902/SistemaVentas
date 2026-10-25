from uuid import uuid4

from django.contrib.auth.hashers import make_password
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from auditoria.models import LogAuditoria
from usuarios.models import Miscelanea, Usuario
from usuarios.test_utils import TemporaryRLSNeutralizationMixin


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AuditSecretSanitizationTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    def setUp(self):
        super().setUp()
        self.tenant = Miscelanea.objects.create(
            Nombre="Tenant auditoria",
            Codigo="AUDITORIA-T3",
        )
        self.admin = Usuario.objects.create_user(
            Email="audit-admin@example.test",
            Password="test-only-password",
            Nombre="Admin auditoria",
            Miscelanea=self.tenant,
            Rol="admin",
            Estado="Activo",
        )
        self.client.force_authenticate(user=self.admin)

    def _sensitive_user(self, suffix):
        user = Usuario.objects.create_user(
            Email=f"audit-target-{suffix}@example.test",
            Password=f"test-only-{uuid4()}",
            Nombre=f"Objetivo {suffix}",
            Miscelanea=self.tenant,
            Rol="cajero",
            Estado="Activo",
        )
        user.password = make_password(f"test-only-{uuid4()}")
        user.EmailPendiente = f"pending-{uuid4()}@example.test"
        user.TokenVerificacion = uuid4()
        user.save(
            update_fields=["password", "EmailPendiente", "TokenVerificacion"]
        )
        return user

    @staticmethod
    def _contains_value(value, sensitive_values):
        if isinstance(value, dict):
            return any(
                AuditSecretSanitizationTests._contains_value(item, sensitive_values)
                for item in value.values()
            )
        if isinstance(value, (list, tuple)):
            return any(
                AuditSecretSanitizationTests._contains_value(item, sensitive_values)
                for item in value
            )
        return str(value) in sensitive_values

    @staticmethod
    def _user_secrets(user):
        return {
            str(user.password),
            str(user.EmailPendiente),
            str(user.TokenVerificacion),
        }

    def _assert_not_exposed_by_logs_endpoint(self, sensitive_values):
        response = self.client.get("/api/auditoria/logs/?page_size=200")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self._contains_value(response.json(), sensitive_values))

    def test_user_update_persists_only_explicit_auditable_fields(self):
        target = self._sensitive_user("update")
        sensitive_values = self._user_secrets(target)

        response = self.client.patch(
            f"/api/usuarios/usuarios/{target.pk}/",
            {"nombre": "Nombre actualizado"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            LogAuditoria.objects.filter(
                accion="MODIFICAR",
                modulo="USUARIOS",
            ).count(),
            1,
        )
        log = LogAuditoria.objects.filter(
            accion="MODIFICAR",
            modulo="USUARIOS",
        ).latest("IdLog")
        self.assertFalse(
            self._contains_value(
                {"antes": log.datos_anteriores, "despues": log.datos_nuevos},
                sensitive_values,
            )
        )
        self.assertEqual(log.datos_anteriores["IdUsuario"], target.pk)
        self.assertEqual(log.datos_nuevos["Nombre"], "Nombre actualizado")
        self._assert_not_exposed_by_logs_endpoint(sensitive_values)

    def test_user_delete_persists_only_explicit_auditable_fields(self):
        target = self._sensitive_user("delete")
        target_id = target.pk
        sensitive_values = self._user_secrets(target)

        response = self.client.delete(f"/api/usuarios/usuarios/{target_id}/")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            LogAuditoria.objects.filter(
                accion="ELIMINAR",
                modulo="USUARIOS",
            ).count(),
            1,
        )
        log = LogAuditoria.objects.filter(
            accion="ELIMINAR",
            modulo="USUARIOS",
        ).latest("IdLog")
        self.assertFalse(
            self._contains_value(log.datos_anteriores, sensitive_values)
        )
        self.assertEqual(log.datos_anteriores["IdUsuario"], target_id)
        self.assertEqual(log.datos_anteriores["Miscelanea"], self.tenant.pk)
        self._assert_not_exposed_by_logs_endpoint(sensitive_values)

    def test_historical_sensitive_json_is_redacted_only_in_http_response(self):
        historical_values = {str(uuid4()) for _ in range(6)}
        values = iter(historical_values)
        stored_before = {
            "password": next(values),
            "TokenVerificacion": next(values),
            "CodigoHash": next(values),
            "nested": {
                "access_token": next(values),
                "refresh": next(values),
                "secret_key": next(values),
                "campo_auditable": "conservar",
            },
        }
        historical = LogAuditoria.objects.create(
            Miscelanea=self.tenant,
            usuario=self.admin,
            nombre_usuario="Historial",
            accion="MODIFICAR",
            modulo="USUARIOS",
            descripcion="Registro historico de prueba",
            datos_anteriores=stored_before,
            datos_nuevos={"authorization": str(uuid4()), "Estado": "Activo"},
            resultado="EXITOSO",
        )
        persisted_values = historical_values | {
            historical.datos_nuevos["authorization"]
        }

        response = self.client.get("/api/auditoria/logs/?page_size=200")

        self.assertEqual(response.status_code, 200)
        row = next(
            item
            for item in response.json()["results"]
            if item["IdLog"] == historical.pk
        )
        self.assertFalse(self._contains_value(row, persisted_values))
        self.assertEqual(
            row["datos_anteriores"]["nested"]["campo_auditable"],
            "conservar",
        )
        self.assertEqual(row["datos_nuevos"]["Estado"], "Activo")

        historical.refresh_from_db()
        self.assertEqual(historical.datos_anteriores, stored_before)
        self.assertTrue(
            self._contains_value(
                {
                    "antes": historical.datos_anteriores,
                    "despues": historical.datos_nuevos,
                },
                persisted_values,
            )
        )
