from django.db import migrations


def assign_existing_data(apps, schema_editor):
    Miscelanea = apps.get_model('usuarios', 'Miscelanea')
    tenant, _ = Miscelanea.objects.get_or_create(
        Codigo='LEGACY',
        defaults={'Nombre': 'Miscelanea principal', 'Estado': 'Activo'},
    )

    for app_label, model_names in {
        'usuarios': ['Usuario'],
        'catalogo': ['Categoria', 'Proveedor', 'Producto'],
        'inventario': [
            'EntradaInventario', 'DetalleEntradaInventario', 'Inventario',
            'Perdida', 'DetallePerdida', 'SolicitudDevolucion',
            'DetalleSolicitudDevolucion',
        ],
        'ventas': ['Venta', 'DetalleVenta'],
        'auditoria': ['LogAuditoria'],
    }.items():
        for model_name in model_names:
            Model = apps.get_model(app_label, model_name)
            Model.objects.filter(Miscelanea__isnull=True).update(Miscelanea=tenant)


def reverse_assign_existing_data(apps, schema_editor):
    for app_label, model_names in {
        'usuarios': ['Usuario'],
        'catalogo': ['Categoria', 'Proveedor', 'Producto'],
        'inventario': [
            'EntradaInventario', 'DetalleEntradaInventario', 'Inventario',
            'Perdida', 'DetallePerdida', 'SolicitudDevolucion',
            'DetalleSolicitudDevolucion',
        ],
        'ventas': ['Venta', 'DetalleVenta'],
        'auditoria': ['LogAuditoria'],
    }.items():
        for model_name in model_names:
            Model = apps.get_model(app_label, model_name)
            Model.objects.update(Miscelanea=None)


class Migration(migrations.Migration):
    dependencies = [
        ('usuarios', '0007_miscelanea_alter_usuario_rol_usuario_miscelanea'),
        ('catalogo', '0005_categoria_miscelanea_producto_miscelanea_and_more'),
        ('inventario', '0009_detalleentradainventario_miscelanea_and_more'),
        ('ventas', '0003_detalleventa_miscelanea_venta_miscelanea'),
        ('auditoria', '0002_logauditoria_miscelanea'),
    ]

    operations = [
        migrations.RunPython(assign_existing_data, reverse_assign_existing_data),
    ]
