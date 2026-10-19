from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0015_delegacionadmin_codigoprefijo_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='miscelanea',
            name='TextoRecibo',
            field=models.TextField(
                blank=True,
                default='',
                max_length=500,
                verbose_name='Texto adicional del recibo',
            ),
        ),
    ]
