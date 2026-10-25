from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from auditoria.models import LogAuditoria
from catalogo.models import Categoria, Producto, Proveedor
from inventario.models import DetalleEntradaInventario, EntradaInventario, Inventario
from usuarios.models import Miscelanea, Usuario
from usuarios.test_utils import RLS_TEST_ROLE, RestrictedRLSRoleMixin


RLS_CATALOG_TABLES = ("Categoria", "Proveedor", "Producto")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CatalogoRLSRealRoleTests(
    RestrictedRLSRoleMixin,
    APITransactionTestCase,
):
    """Valida el catálogo y sus agregados bajo el rol RLS real."""

    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.tenant_a = Miscelanea.objects.create(
            Nombre="RLS catálogo A", Codigo="RLS-CAT-A"
        )
        self.tenant_b = Miscelanea.objects.create(
            Nombre="RLS catálogo B", Codigo="RLS-CAT-B"
        )
        self.admin_a = self._user(
            "rls-catalog-admin-a@example.test", self.tenant_a, "admin"
        )
        self.seller_a = self._user(
            "rls-catalog-seller-a@example.test", self.tenant_a, "vendedor"
        )
        self.admin_b = self._user(
            "rls-catalog-admin-b@example.test", self.tenant_b, "admin"
        )
        self.tenantless_admin = self._user(
            "rls-catalog-no-tenant@example.test", None, "admin"
        )
        self.category_a = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoría propia A",
            PorcentajeGanancia=Decimal("25.00"),
        )
        self.category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Categoría propia B",
            PorcentajeGanancia=Decimal("80.00"),
        )
        self.provider_a = Proveedor.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Proveedor propio A",
            Contacto="contacto-a",
        )
        self.provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Proveedor propio B",
            Contacto="contacto-b",
        )
        self.product_a = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Producto propio A",
            Presentacion="unidad",
            Codigo="RLS-SHARED-001",
        )
        self.product_b = Producto.objects.create(
            Miscelanea=self.tenant_b,
            IdCategoria=self.category_b,
            IdProveedor=self.provider_b,
            Nombre="Producto propio B",
            Presentacion="unidad",
            Codigo="RLS-SHARED-001",
        )
        self._create_purchase(
            self.tenant_a,
            self.admin_a,
            self.provider_a,
            self.product_a,
            timezone.now() - timedelta(days=45),
            1,
            "5.00",
        )
        self._create_purchase(
            self.tenant_a,
            self.admin_a,
            self.provider_a,
            self.product_a,
            timezone.now() - timedelta(days=5),
            2,
            "10.00",
        )
        _, latest_detail = self._create_purchase(
            self.tenant_a,
            self.admin_a,
            self.provider_a,
            self.product_a,
            timezone.now() - timedelta(days=1),
            3,
            "20.00",
        )
        sold_unit = latest_detail.inventarios.order_by("IdInventario").first()
        sold_unit.Estado = "Vendido"
        sold_unit.save(update_fields=["Estado"])
        self._create_purchase(
            self.tenant_b,
            self.admin_b,
            self.provider_b,
            self.product_b,
            timezone.now() - timedelta(days=1),
            7,
            "90.00",
        )

        # Relaciones deliberadamente incoherentes para comprobar que los
        # agregados no confían solo en la FK y que RLS sigue delimitando filas.
        self.cross_product_b = Producto.objects.create(
            Miscelanea=self.tenant_b,
            IdCategoria=self.category_a,
            IdProveedor=self.provider_a,
            Nombre="Producto B con relaciones A",
        )
        self.cross_entry_b, self.cross_detail_b = self._create_purchase(
            self.tenant_b,
            self.admin_b,
            self.provider_a,
            self.product_a,
            timezone.now() - timedelta(hours=1),
            4,
            "99.00",
        )

        self.shared_category_b = Categoria.objects.create(
            Miscelanea=self.tenant_b,
            Nombre="Nombre compartido categoría",
            PorcentajeGanancia=Decimal("10.00"),
        )
        self.shared_provider_b = Proveedor.objects.create(
            Miscelanea=self.tenant_b, Nombre="Nombre compartido proveedor"
        )
        self.shared_product_b = Producto.objects.create(
            Miscelanea=self.tenant_b,
            IdCategoria=self.category_b,
            IdProveedor=self.provider_b,
            Nombre="Nombre compartido producto",
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
    def _create_purchase(tenant, user, provider, product, date, quantity, cost):
        entry = EntradaInventario.objects.create(
            Miscelanea=tenant,
            IdProveedor=provider,
            IdUsuario=user,
            FechaEntrada=date,
            Total=Decimal(cost) * quantity,
        )
        detail = DetalleEntradaInventario.objects.create(
            Miscelanea=tenant,
            IdEntradaInventario=entry,
            IdProducto=product,
            TipoCompra="unidad",
            Cantidad=quantity,
            PrecioCompraUnitario=Decimal(cost),
        )
        return entry, detail

    @staticmethod
    def _catalog_snapshot():
        return (
            tuple(
                Categoria.objects.order_by("IdCategoria").values_list(
                    "IdCategoria", "Miscelanea_id", "Nombre",
                    "PorcentajeGanancia", "Estado",
                )
            ),
            tuple(
                Proveedor.objects.order_by("IdProveedor").values_list(
                    "IdProveedor", "Miscelanea_id", "Nombre", "Contacto", "Estado"
                )
            ),
            tuple(
                Producto.objects.order_by("IdProducto").values_list(
                    "IdProducto", "Miscelanea_id", "IdCategoria_id",
                    "IdProveedor_id", "Nombre", "Presentacion", "Estado",
                )
            ),
        )

    def test_direct_queries_prove_catalog_rls_for_restricted_role(self):
        own_ids = {
            "Categoria": self.category_a.pk,
            "Proveedor": self.provider_a.pk,
            "Producto": self.product_a.pk,
        }
        foreign_ids = {
            "Categoria": self.category_b.pk,
            "Proveedor": self.provider_b.pk,
            "Producto": self.product_b.pk,
        }
        columns = {
            "Categoria": "IdCategoria",
            "Proveedor": "IdProveedor",
            "Producto": "IdProducto",
        }
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
                  AND c.relname = 'Producto'
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
                [list(RLS_CATALOG_TABLES)],
            )
            self.assertEqual(
                cursor.fetchall(),
                sorted((table, True, True) for table in RLS_CATALOG_TABLES),
            )

        self.assertIsNone(self._raw_context())
        for empty_context in (None, ""):
            if empty_context == "":
                self._set_context("")
            with connection.cursor() as cursor:
                for table in RLS_CATALOG_TABLES:
                    cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                    self.assertEqual(cursor.fetchone()[0], 0, table)

        self._set_context(self.tenant_a.pk)
        with connection.cursor() as cursor:
            for table in RLS_CATALOG_TABLES:
                column = columns[table]
                cursor.execute(f'SELECT "{column}" FROM "{table}"')
                visible = {row[0] for row in cursor.fetchall()}
                self.assertIn(own_ids[table], visible)
                self.assertNotIn(foreign_ids[table], visible)

    def test_http_lists_and_details_are_tenant_scoped(self):
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        for base_url, own_id, foreign_id in (
            ("/api/catalogo/categorias/", self.category_a.pk, self.category_b.pk),
            ("/api/catalogo/proveedores/", self.provider_a.pk, self.provider_b.pk),
            ("/api/catalogo/productos/", self.product_a.pk, self.product_b.pk),
        ):
            listing = client.get(base_url)
            self.assertEqual(listing.status_code, 200, listing.data)
            ids = {row["id"] for row in listing.json()}
            self.assertIn(own_id, ids)
            self.assertNotIn(foreign_id, ids)
            self.assertEqual(self._context(), "")
            own = client.get(f"{base_url}{own_id}/")
            foreign = client.get(f"{base_url}{foreign_id}/")
            self.assertEqual(own.status_code, 200, own.data)
            self.assertEqual(foreign.status_code, 404, foreign.data)
            self.assertEqual(self._context(), "")

    def test_busqueda_por_codigo_respeta_rls_y_el_tenant_http(self):
        self._enter_restricted_role()
        client = self._client(self.seller_a)
        response = client.get(
            "/api/catalogo/productos/buscar-codigo/",
            {"codigo": "RLS-SHARED-001"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json()["id"], self.product_a.pk)
        self.assertNotEqual(response.json()["id"], self.product_b.pk)
        self.assertEqual(self._context(), "")

    def test_busqueda_equivalente_respeta_tenant_con_rol_rls_real(self):
        self.product_a.Codigo = "0012345678905"
        self.product_a.save(update_fields=["Codigo"])
        self.product_b.Codigo = "012345678905"
        self.product_b.save(update_fields=["Codigo"])
        self._enter_restricted_role()
        client = self._client(self.seller_a)

        response = client.get(
            "/api/catalogo/productos/buscar-codigo/",
            {"codigo": "012345678905"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json()["id"], self.product_a.pk)
        self.assertNotEqual(response.json()["id"], self.product_b.pk)
        self.assertEqual(self._context(), "")

    def test_admin_creates_and_updates_own_catalog_records(self):
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        category = client.post(
            "/api/catalogo/categorias/",
            {
                "name": "Categoría creada por A",
                "profitPercentage": "12.50",
                "status": "Activo",
                "miscelaneaId": self.tenant_b.pk,
            },
            format="json",
        )
        provider = client.post(
            "/api/catalogo/proveedores/",
            {
                "name": "Proveedor creado por A",
                "contact": "inicial",
                "miscelaneaId": self.tenant_b.pk,
            },
            format="json",
        )
        self.assertEqual(category.status_code, 201, category.data)
        self.assertEqual(provider.status_code, 201, provider.data)
        product = client.post(
            "/api/catalogo/productos/",
            {
                "name": "Producto creado por A",
                "categoryId": category.json()["id"],
                "proveedorId": provider.json()["id"],
                "presentacion": "caja",
                "miscelaneaId": self.tenant_b.pk,
            },
            format="json",
        )
        self.assertEqual(product.status_code, 201, product.data)
        for response in (category, provider, product):
            self.assertEqual(self._context(), "")

        category_update = client.patch(
            f"/api/catalogo/categorias/{category.json()['id']}/",
            {"profitPercentage": "15.00"},
            format="json",
        )
        provider_update = client.patch(
            f"/api/catalogo/proveedores/{provider.json()['id']}/",
            {"contact": "actualizado"},
            format="json",
        )
        product_update = client.patch(
            f"/api/catalogo/productos/{product.json()['id']}/",
            {"presentacion": "pack"},
            format="json",
        )
        self.assertEqual(category_update.status_code, 200, category_update.data)
        self.assertEqual(provider_update.status_code, 200, provider_update.data)
        self.assertEqual(product_update.status_code, 200, product_update.data)
        self.assertEqual(self._context(), "")

        self._set_context(self.tenant_a.pk)
        created_category = Categoria.objects.get(pk=category.json()["id"])
        created_provider = Proveedor.objects.get(pk=provider.json()["id"])
        created_product = Producto.objects.get(pk=product.json()["id"])
        self.assertEqual(created_category.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(created_provider.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(created_product.Miscelanea_id, self.tenant_a.pk)
        self.assertEqual(created_category.PorcentajeGanancia, Decimal("15.00"))
        self.assertEqual(created_provider.Contacto, "actualizado")
        self.assertEqual(created_product.Presentacion, "pack")

    def test_foreign_resources_and_foreign_keys_are_rejected_without_changes(self):
        before = self._catalog_snapshot()
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        create = client.post(
            "/api/catalogo/productos/",
            {
                "name": "Producto con FK ajena",
                "categoryId": self.category_b.pk,
                "proveedorId": self.provider_b.pk,
            },
            format="json",
        )
        update = client.patch(
            f"/api/catalogo/productos/{self.product_a.pk}/",
            {"categoryId": self.category_b.pk},
            format="json",
        )
        self.assertEqual(create.status_code, 400, create.data)
        self.assertEqual(update.status_code, 400, update.data)
        for base_url, foreign_id in (
            ("/api/catalogo/categorias/", self.category_b.pk),
            ("/api/catalogo/proveedores/", self.provider_b.pk),
            ("/api/catalogo/productos/", self.product_b.pk),
        ):
            patch_response = client.patch(
                f"{base_url}{foreign_id}/", {"status": "Inactivo"}, format="json"
            )
            delete_response = client.delete(f"{base_url}{foreign_id}/")
            self.assertEqual(patch_response.status_code, 404, patch_response.data)
            self.assertEqual(delete_response.status_code, 404, delete_response.data)
            self.assertEqual(self._context(), "")

        self._leave_restricted_role()
        self.assertEqual(self._catalog_snapshot(), before)

    def test_uniqueness_is_scoped_per_tenant_and_rejects_own_duplicates(self):
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        cases = (
            (
                "/api/catalogo/categorias/",
                {
                    "name": self.shared_category_b.Nombre,
                    "profitPercentage": "10.00",
                },
            ),
            (
                "/api/catalogo/proveedores/",
                {"name": self.shared_provider_b.Nombre},
            ),
            (
                "/api/catalogo/productos/",
                {
                    "name": self.shared_product_b.Nombre,
                    "categoryId": self.category_a.pk,
                    "proveedorId": self.provider_a.pk,
                },
            ),
        )
        for url, payload in cases:
            created = client.post(url, payload, format="json")
            self.assertEqual(created.status_code, 201, created.data)
            duplicate_payload = dict(payload)
            duplicate_payload["name"] = payload["name"].swapcase()
            duplicate = client.post(url, duplicate_payload, format="json")
            self.assertEqual(duplicate.status_code, 400, duplicate.data)
            self.assertEqual(self._context(), "")

    def test_admin_deletes_eligible_own_records_only(self):
        category = Categoria.objects.create(
            Miscelanea=self.tenant_a,
            Nombre="Categoría eliminable",
            PorcentajeGanancia=Decimal("5.00"),
        )
        provider = Proveedor.objects.create(
            Miscelanea=self.tenant_a, Nombre="Proveedor eliminable"
        )
        product = Producto.objects.create(
            Miscelanea=self.tenant_a,
            IdCategoria=category,
            IdProveedor=provider,
            Nombre="Producto eliminable",
        )
        foreign_before = self._catalog_snapshot()
        self._enter_restricted_role()
        client = self._client(self.admin_a)

        for base_url, pk in (
            ("/api/catalogo/productos/", product.pk),
            ("/api/catalogo/categorias/", category.pk),
            ("/api/catalogo/proveedores/", provider.pk),
        ):
            response = client.delete(f"{base_url}{pk}/")
            self.assertEqual(response.status_code, 204, response.data)
            self.assertEqual(self._context(), "")

        self._leave_restricted_role()
        self.assertFalse(Producto.objects.filter(pk=product.pk).exists())
        self.assertFalse(Categoria.objects.filter(pk=category.pk).exists())
        self.assertFalse(Proveedor.objects.filter(pk=provider.pk).exists())
        self.assertTrue(Producto.objects.filter(pk=self.product_b.pk).exists())
        self.assertTrue(Categoria.objects.filter(pk=self.category_b.pk).exists())
        self.assertTrue(Proveedor.objects.filter(pk=self.provider_b.pk).exists())
        self.assertNotEqual(self._catalog_snapshot(), foreign_before)

    def test_dependent_catalog_records_reject_delete_without_changes(self):
        before = self._catalog_snapshot()
        audit_before = LogAuditoria.objects.filter(
            accion="ELIMINAR",
            resultado="EXITOSO",
        ).count()
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        client.raise_request_exception = False
        responses = {
            "category": client.delete(
                f"/api/catalogo/categorias/{self.category_a.pk}/"
            ),
            "provider": client.delete(
                f"/api/catalogo/proveedores/{self.provider_a.pk}/"
            ),
            "product": client.delete(
                f"/api/catalogo/productos/{self.product_a.pk}/"
            ),
        }
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertEqual(self._catalog_snapshot(), before)
        self.assertEqual(
            LogAuditoria.objects.filter(
                accion="ELIMINAR",
                resultado="EXITOSO",
            ).count(),
            audit_before,
        )
        for label, response in responses.items():
            with self.subTest(resource=label):
                self.assertEqual(
                    response.status_code,
                    409,
                    getattr(response, "data", None),
                )
                self.assertEqual(
                    response.json(),
                    {
                        "detail": (
                            "No se puede eliminar porque el registro tiene "
                            "datos relacionados."
                        )
                    },
                )

    def test_seller_reads_but_cannot_write(self):
        before = self._catalog_snapshot()
        self._enter_restricted_role()
        client = self._client(self.seller_a)
        for url in (
            "/api/catalogo/categorias/",
            "/api/catalogo/proveedores/",
            "/api/catalogo/productos/",
        ):
            self.assertEqual(client.get(url).status_code, 200)
            self.assertEqual(client.post(url, {}, format="json").status_code, 403)
        self.assertEqual(
            client.patch(
                f"/api/catalogo/productos/{self.product_a.pk}/",
                {"status": "Inactivo"},
                format="json",
            ).status_code,
            403,
        )
        self.assertEqual(
            client.delete(
                f"/api/catalogo/productos/{self.product_a.pk}/"
            ).status_code,
            403,
        )
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertEqual(self._catalog_snapshot(), before)

    def test_tenantless_user_sees_nothing_and_cannot_write(self):
        before = self._catalog_snapshot()
        audit_before = LogAuditoria.objects.filter(
            accion="CREAR",
            resultado="EXITOSO",
        ).count()
        self._enter_restricted_role()
        client = self._client(self.tenantless_admin)
        client.raise_request_exception = False
        for url in (
            "/api/catalogo/categorias/",
            "/api/catalogo/proveedores/",
            "/api/catalogo/productos/",
        ):
            listing = client.get(url)
            self.assertEqual(listing.status_code, 200, listing.data)
            self.assertEqual(listing.json(), [])
            self.assertEqual(self._context(), "")
        writes = {
            "category": client.post(
                "/api/catalogo/categorias/",
                {"name": "Sin tenant", "profitPercentage": "10.00"},
                format="json",
            ),
            "provider": client.post(
                "/api/catalogo/proveedores/",
                {"name": "Sin tenant"},
                format="json",
            ),
            "product": client.post(
                "/api/catalogo/productos/",
                {
                    "name": "Sin tenant",
                    "categoryId": self.category_a.pk,
                    "proveedorId": self.provider_a.pk,
                },
                format="json",
            ),
        }
        self.assertEqual(self._context(), "")
        self._leave_restricted_role()
        self.assertEqual(self._catalog_snapshot(), before)
        self.assertEqual(
            LogAuditoria.objects.filter(
                accion="CREAR",
                resultado="EXITOSO",
            ).count(),
            audit_before,
        )
        for label, response in writes.items():
            with self.subTest(resource=label):
                self.assertEqual(
                    response.status_code,
                    403,
                    getattr(response, "data", None),
                )
                self.assertEqual(
                    response.json(),
                    {"detail": "La cuenta no tiene una miscelánea asignada."},
                )

    def test_calculated_fields_exclude_foreign_and_inconsistent_rows(self):
        self._enter_restricted_role()
        client = self._client(self.admin_a)
        category = client.get(
            f"/api/catalogo/categorias/{self.category_a.pk}/"
        )
        provider = client.get(
            f"/api/catalogo/proveedores/{self.provider_a.pk}/"
        )
        product = client.get(
            f"/api/catalogo/productos/{self.product_a.pk}/"
        )

        self.assertEqual(category.status_code, 200, category.data)
        self.assertEqual(provider.status_code, 200, provider.data)
        self.assertEqual(product.status_code, 200, product.data)
        self.assertEqual(category.json()["productCount"], 1)
        self.assertEqual(provider.json()["pedidos_recientes"], 2)
        self.assertIs(provider.json()["activo"], True)
        self.assertIs(provider.json()["tiene_productos"], True)
        self.assertEqual(product.json()["stock"], 5)
        self.assertEqual(product.json()["salePrice"], 25)
        self.assertEqual(self._context(), "")
