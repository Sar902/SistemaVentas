from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from usuarios.models import Usuario


class Command(BaseCommand):
    help = 'Crea o actualiza la cuenta técnica permitida en el panel de Django.'

    def handle(self, *args, **options):
        email = settings.DJANGO_ADMIN_EMAIL
        password = settings.DJANGO_ADMIN_PASSWORD
        if not password:
            self.stdout.write(
                self.style.WARNING(
                    'DJANGO_ADMIN_PASSWORD no está configurada; no se modificó la cuenta técnica.'
                )
            )
            return

        user, _ = Usuario.objects.get_or_create(
            Email=email,
            defaults={
                'Nombre': 'Administrador del sistema',
                'Estado': 'Activo',
                'Rol': 'admin',
                'Miscelanea': None,
            },
        )
        user.Nombre = user.Nombre or 'Administrador del sistema'
        user.Estado = 'Activo'
        user.Rol = 'admin'
        user.Miscelanea = None
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save(update_fields=[
            'Nombre', 'Estado', 'Rol', 'Miscelanea',
            'is_staff', 'is_superuser', 'password',
        ])
        self.stdout.write(self.style.SUCCESS(f'Cuenta técnica lista: {email}'))
