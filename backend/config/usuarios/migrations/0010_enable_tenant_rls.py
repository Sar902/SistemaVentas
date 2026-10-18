from django.db import migrations


TENANT_TABLES = [
    'Categoria',
    'Proveedor',
    'Producto',
    'EntradaInventario',
    'DetalleEntradaInventario',
    'Inventario',
    'Perdida',
    'DetallePerdida',
    'SolicitudDevolucion',
    'DetalleSolicitudDevolucion',
    'Venta',
    'DetalleVenta',
]


def enable_rls(apps, schema_editor):
    for table in TENANT_TABLES:
        policy = f'tenant_isolation_{table}'
        schema_editor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        schema_editor.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        schema_editor.execute(
            f'''CREATE POLICY "{policy}" ON "{table}"
                USING ("IdMiscelanea" = NULLIF(current_setting('app.miscelanea_id', true), '')::integer)
                WITH CHECK ("IdMiscelanea" = NULLIF(current_setting('app.miscelanea_id', true), '')::integer)'''
        )


def disable_rls(apps, schema_editor):
    for table in reversed(TENANT_TABLES):
        policy = f'tenant_isolation_{table}'
        schema_editor.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"')
        schema_editor.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        schema_editor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [
        ('usuarios', '0009_alter_usuario_rol'),
        ('catalogo', '0005_categoria_miscelanea_producto_miscelanea_and_more'),
        ('inventario', '0009_detalleentradainventario_miscelanea_and_more'),
        ('ventas', '0003_detalleventa_miscelanea_venta_miscelanea'),
    ]

    operations = [
        migrations.RunPython(enable_rls, disable_rls),
    ]
