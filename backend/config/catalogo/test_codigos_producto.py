from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from auditoria.models import LogAuditoria
from catalogo.models import Categoria, Producto, Proveedor
from inventario.models import Inventario
from usuarios.models import Miscelanea, Usuario
from usuarios.test_utils import TemporaryRLSNeutralizationMixin
from ventas.models import Venta


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CodigosProductoTests(
    TemporaryRLSNeutralizationMixin,
    APITransactionTestCase,
):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="Códigos A", Codigo="COD-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="Códigos B", Codigo="COD-B"
        )
        self.admin_a = self._user("cod-admin-a@example.test", self.tenant_a, "admin")
        self.admin_b = self._user("cod-admin-b@example.test", self.tenant_b, "admin")
        self.seller_a = self._user(
            "cod-seller-a@example.test", self.tenant_a, "vendedor"
        )
        self.tenantless = self._user(
            "cod-no-tenant@example.test", None, "admin"
        )
        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoría A",
            PorcentajeGanancia=Decimal("10.00"),
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Categoría B",
            PorcentajeGanancia=Decimal("10.00"),
        )
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor A"
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Proveedor B"
        )
        self.product_a = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Producto A",
            Codigo="00123ABC",
        )
        self.product_b = Producto.objects.create(
            Miscelanea=self.tenant_b,
            IdCategoria=self.category_b,
            IdProveedor=self.provider_b,
            Nombre="Producto B",
            Codigo="00123ABC",
        )

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

    def _authenticate(self, user):
        self.client.force_authenticate(user=user)

    def _payload(self, name, code):
        return {
            "name": name,
            "categoryId": self.category_a.pk,
            "proveedorId": self.provider_a.pk,
            "code": code,
        }

    def test_codigo_opcional_normaliza_bordes_y_conserva_ceros(self):
        self._authenticate(self.admin_a)
        first = self.client.post(
            "/api/catalogo/productos/",
            self._payload("Con código", "  000045-AB  "),
            format="json",
        )
        without_code = self.client.post(
            "/api/catalogo/productos/",
            self._payload("Sin código 1", "   "),
            format="json",
        )
        another_without_code = self.client.post(
            "/api/catalogo/productos/",
            self._payload("Sin código 2", None),
            format="json",
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.json()["code"], "000045-AB")
        self.assertIsNone(without_code.json()["code"])
        self.assertIsNone(another_without_code.json()["code"])
        self.assertEqual(
            Producto.objects.get(pk=first.json()["id"]).Codigo,
            "000045-AB",
        )

    def test_valida_longitud_y_caracteres_sin_modificar_contenido_interno(self):
        self._authenticate(self.admin_a)
        invalid_cases = ("ABC 123", "ABCÑ123", "X" * 65)
        for index, code in enumerate(invalid_cases):
            with self.subTest(code=code):
                response = self.client.post(
                    "/api/catalogo/productos/",
                    self._payload(f"Inválido {index}", code),
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("code", response.json())

    def test_duplicado_propio_se_rechaza_y_el_mismo_codigo_se_permite_en_otro_tenant(self):
        self._authenticate(self.admin_a)
        created_a = self.client.post(
            "/api/catalogo/productos/",
            self._payload("Compartido A", "SHARED-NEW-001"),
            format="json",
        )
        duplicate = self.client.post(
            "/api/catalogo/productos/",
            self._payload("Duplicado A", "SHARED-NEW-001"),
            format="json",
        )
        self.assertEqual(created_a.status_code, 201, created_a.data)
        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertIn("Ya existe", duplicate.json()["code"][0])

        self._authenticate(self.admin_b)
        same_other_tenant = self.client.post(
            "/api/catalogo/productos/",
            {
                "name": "Otro B",
                "categoryId": self.category_b.pk,
                "proveedorId": self.provider_b.pk,
                "code": "SHARED-NEW-001",
            },
            format="json",
        )
        self.assertEqual(same_other_tenant.status_code, 201, same_other_tenant.data)

    def test_actualizacion_conserva_codigo_y_admite_el_codigo_propio(self):
        self._authenticate(self.admin_a)
        partial = self.client.patch(
            f"/api/catalogo/productos/{self.product_a.pk}/",
            {"presentacion": "Caja"},
            format="json",
        )
        same_code = self.client.patch(
            f"/api/catalogo/productos/{self.product_a.pk}/",
            {"code": "  00123ABC  "},
            format="json",
        )
        self.assertEqual(partial.status_code, 200, partial.data)
        self.assertEqual(partial.json()["code"], "00123ABC")
        self.assertEqual(same_code.status_code, 200, same_code.data)

    def test_busqueda_es_exacta_normalizada_y_aislada_sin_efectos(self):
        inventory_before = list(
            Inventario.objects.order_by("pk").values_list("pk", "Estado")
        )
        sales_before = Venta.objects.count()
        audits_before = LogAuditoria.objects.count()
        self._authenticate(self.seller_a)

        found = self.client.get(
            "/api/catalogo/productos/buscar-codigo/",
            {"codigo": "  00123ABC  "},
        )
        case_mismatch = self.client.get(
            "/api/catalogo/productos/buscar-codigo/",
            {"codigo": "00123abc"},
        )
        unknown = self.client.get(
            "/api/catalogo/productos/buscar-codigo/",
            {"codigo": "NO-EXISTE"},
        )

        self.assertEqual(found.status_code, 200, found.data)
        self.assertEqual(found.json()["id"], self.product_a.pk)
        self.assertEqual(case_mismatch.status_code, 404, case_mismatch.data)
        self.assertEqual(unknown.status_code, 404, unknown.data)
        self.assertEqual(
            list(Inventario.objects.order_by("pk").values_list("pk", "Estado")),
            inventory_before,
        )
        self.assertEqual(Venta.objects.count(), sales_before)
        self.assertEqual(LogAuditoria.objects.count(), audits_before)

    def test_busqueda_equivalente_upca_ean13_funciona_en_ambos_sentidos(self):
        ean13 = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="EAN equivalente A",
            Codigo="0012345678905",
        )
        upca = Producto.objects.create(
            Miscelanea=self.tenant_b,
            IdCategoria=self.category_b,
            IdProveedor=self.provider_b,
            Nombre="UPC equivalente B",
            Codigo="012345678905",
        )

        self._authenticate(self.seller_a)
        desde_upca = self.client.get(
            "/api/catalogo/productos/buscar-codigo/",
            {"codigo": "012345678905"},
        )
        self.assertEqual(desde_upca.status_code, 200, desde_upca.data)
        self.assertEqual(desde_upca.json()["id"], ean13.pk)
        self.assertEqual(desde_upca.json()["code"], "0012345678905")

        self._authenticate(self.admin_b)
        desde_ean13 = self.client.get(
            "/api/catalogo/productos/buscar-codigo/",
            {"codigo": "0012345678905"},
        )
        self.assertEqual(desde_ean13.status_code, 200, desde_ean13.data)
        self.assertEqual(desde_ean13.json()["id"], upca.pk)
        self.assertEqual(desde_ean13.json()["code"], "012345678905")

    def test_checksum_invalido_y_otros_codigos_conservan_busqueda_exacta(self):
        invalid_ean = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="EAN inválido",
            Codigo="0012345678906",
        )
        ean8 = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="EAN ocho",
            Codigo="00000000",
        )
        significant_zeros = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Ceros significativos",
            Codigo="00012345678905",
        )
        self._authenticate(self.seller_a)

        for code, expected_id in (
            ("0012345678906", invalid_ean.pk),
            ("00000000", ean8.pk),
            ("00012345678905", significant_zeros.pk),
            ("00123ABC", self.product_a.pk),
        ):
            with self.subTest(code=code):
                exact = self.client.get(
                    "/api/catalogo/productos/buscar-codigo/", {"codigo": code}
                )
                self.assertEqual(exact.status_code, 200, exact.data)
                self.assertEqual(exact.json()["id"], expected_id)

        for code in (
            "012345678906",
            "000000000",
            "0012345678905",
            "0123ABC",
        ):
            with self.subTest(non_equivalent=code):
                response = self.client.get(
                    "/api/catalogo/productos/buscar-codigo/", {"codigo": code}
                )
                self.assertEqual(response.status_code, 404, response.data)

    def test_busqueda_ambigua_devuelve_409_aunque_haya_coincidencia_exacta(self):
        exact = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="UPC histórico",
            Codigo="012345678905",
        )
        Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="EAN histórico",
            Codigo="0012345678905",
        )
        self._authenticate(self.seller_a)

        response = self.client.get(
            "/api/catalogo/productos/buscar-codigo/",
            {"codigo": exact.Codigo},
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn("más de un producto", response.json()["detail"])

    def test_crear_y_editar_rechazan_equivalente_ocupado_sin_bloquear_el_propio(self):
        existing = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="EAN reservado",
            Codigo="0012345678905",
        )
        editable = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Editable",
            Codigo="EDIT-001",
        )
        self._authenticate(self.admin_a)

        duplicate = self.client.post(
            "/api/catalogo/productos/",
            self._payload("UPC duplicado", "012345678905"),
            format="json",
        )
        occupied_update = self.client.patch(
            f"/api/catalogo/productos/{editable.pk}/",
            {"code": "012345678905"},
            format="json",
        )
        own_exact = self.client.patch(
            f"/api/catalogo/productos/{existing.pk}/",
            {"code": "0012345678905"},
            format="json",
        )
        own_equivalent = self.client.patch(
            f"/api/catalogo/productos/{existing.pk}/",
            {"code": "012345678905"},
            format="json",
        )

        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertEqual(occupied_update.status_code, 400, occupied_update.data)
        self.assertIn("equivalente", duplicate.json()["code"][0])
        self.assertIn("equivalente", occupied_update.json()["code"][0])
        self.assertEqual(own_exact.status_code, 200, own_exact.data)
        self.assertEqual(own_equivalent.status_code, 200, own_equivalent.data)
        existing.refresh_from_db()
        editable.refresh_from_db()
        self.assertEqual(existing.Codigo, "012345678905")
        self.assertEqual(editable.Codigo, "EDIT-001")

        self._authenticate(self.admin_b)
        other_tenant = self.client.post(
            "/api/catalogo/productos/",
            {
                "name": "UPC permitido B",
                "categoryId": self.category_b.pk,
                "proveedorId": self.provider_b.pk,
                "code": "0012345678905",
            },
            format="json",
        )
        self.assertEqual(other_tenant.status_code, 201, other_tenant.data)

    def test_busqueda_respeta_autenticacion_y_usuario_sin_tenant(self):
        anonymous = self.client.get(
            "/api/catalogo/productos/buscar-codigo/", {"codigo": "00123ABC"}
        )
        self.assertEqual(anonymous.status_code, 401, anonymous.data)

        self._authenticate(self.tenantless)
        tenantless = self.client.get(
            "/api/catalogo/productos/buscar-codigo/", {"codigo": "00123ABC"}
        )
        self.assertEqual(tenantless.status_code, 404, tenantless.data)

    def test_restriccion_de_base_rechaza_duplicado_no_vacio(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Producto.objects.create(
                    Miscelanea=self.tenant_a,
                    IdCategoria=self.category_a,
                    IdProveedor=self.provider_a,
                    Nombre="Duplicado directo",
                    Codigo="00123ABC",
                )

    def test_conflicto_de_carrera_devuelve_error_controlado_sin_auditoria(self):
        self._authenticate(self.admin_a)
        audits_before = LogAuditoria.objects.filter(
            modulo="PRODUCTOS", accion="CREAR", resultado="EXITOSO"
        ).count()
        with patch(
            "catalogo.serializers.ProductoSerializer.validate_code",
            return_value="00123ABC",
        ):
            response = self.client.post(
                "/api/catalogo/productos/",
                self._payload("Carrera", "00123ABC"),
                format="json",
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Ya existe", response.json()["code"][0])
        self.assertEqual(
            LogAuditoria.objects.filter(
                modulo="PRODUCTOS", accion="CREAR", resultado="EXITOSO"
            ).count(),
            audits_before,
        )

    def test_dos_creaciones_concurrentes_confirman_una_sola(self):
        barrier = Barrier(2, timeout=10)
        audits_before = LogAuditoria.objects.filter(
            modulo="PRODUCTOS", accion="CREAR", resultado="EXITOSO"
        ).count()

        def validate_after_both_requests(serializer, value):
            barrier.wait()
            return value

        def create_product(index):
            close_old_connections()
            client = self.client_class()
            client.force_authenticate(user=self.admin_a)
            try:
                response = client.post(
                    "/api/catalogo/productos/",
                    self._payload(f"Carrera real {index}", "RACE-001"),
                    format="json",
                )
                return response.status_code, response.json()
            finally:
                connections["default"].close()

        with patch(
            "catalogo.serializers.ProductoSerializer.validate_code",
            new=validate_after_both_requests,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(create_product, (1, 2)))

        self.assertEqual(sorted(status for status, _ in results), [201, 400])
        conflict = next(body for status, body in results if status == 400)
        self.assertIn("Ya existe", conflict["code"][0])
        self.assertEqual(
            Producto.objects.filter(
                Miscelanea=self.tenant_a, Codigo="RACE-001"
            ).count(),
            1,
        )
        self.assertEqual(
            LogAuditoria.objects.filter(
                modulo="PRODUCTOS", accion="CREAR", resultado="EXITOSO"
            ).count(),
            audits_before + 1,
        )

    def test_variantes_concurrentes_pueden_entrar_y_la_busqueda_detecta_ambiguedad(self):
        barrier = Barrier(2, timeout=10)

        def validate_after_both_requests(serializer, value):
            barrier.wait()
            return value

        def create_variant(item):
            index, code = item
            close_old_connections()
            client = self.client_class()
            client.force_authenticate(user=self.admin_a)
            try:
                response = client.post(
                    "/api/catalogo/productos/",
                    self._payload(f"Variante concurrente {index}", code),
                    format="json",
                )
                return response.status_code, response.json()
            finally:
                connections["default"].close()

        with patch(
            "catalogo.serializers.ProductoSerializer.validate_code",
            new=validate_after_both_requests,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        create_variant,
                        enumerate(("012345678905", "0012345678905"), start=1),
                    )
                )

        self.assertEqual(sorted(status for status, _ in results), [201, 201])
        self._authenticate(self.seller_a)
        search = self.client.get(
            "/api/catalogo/productos/buscar-codigo/",
            {"codigo": "012345678905"},
        )
        self.assertEqual(search.status_code, 409, search.data)
        self.assertIn("más de un producto", search.json()["detail"])
