from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0016_miscelanea_textorecibo'),
    ]

    operations = [
        migrations.AddField(
            model_name='miscelanea',
            name='TextoReciboSuperior',
            field=models.TextField(
                blank=True,
                default='',
                max_length=500,
                verbose_name='Mensaje superior del recibo',
            ),
        ),
        migrations.AlterField(
            model_name='miscelanea',
            name='TextoRecibo',
            field=models.TextField(
                blank=True,
                default='',
                max_length=500,
                verbose_name='Mensaje inferior del recibo',
            ),
        ),
    ]
