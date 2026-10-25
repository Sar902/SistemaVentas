from django.db import migrations, models
from django.db.models import Q

import catalogo.codigos


class Migration(migrations.Migration):
    dependencies = [
        ('catalogo', '0006_alter_categoria_nombre_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='producto',
            name='Codigo',
            field=models.CharField(
                blank=True,
                max_length=64,
                null=True,
                validators=[catalogo.codigos.validar_codigo_producto],
            ),
        ),
        migrations.AddConstraint(
            model_name='producto',
            constraint=models.UniqueConstraint(
                condition=Q(Codigo__isnull=False),
                fields=('Miscelanea', 'Codigo'),
                name='unique_codigo_producto_por_miscelanea',
            ),
        ),
    ]
