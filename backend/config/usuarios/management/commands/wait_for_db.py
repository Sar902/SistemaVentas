"""
wait_for_db — Comando de gestión Django para esperar a que la BD esté disponible.

Se usa en el startCommand de Railway ANTES de correr migrate, evitando el crash
por condición de carrera cuando el contenedor del backend inicia antes de que
PostgreSQL esté listo para aceptar conexiones.

Uso:
    python manage.py wait_for_db
"""

import time
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = "Espera a que la base de datos esté disponible antes de continuar."

    def add_arguments(self, parser):
        parser.add_argument(
            '--timeout',
            type=int,
            default=60,
            help='Tiempo máximo de espera en segundos (default: 60)',
        )
        parser.add_argument(
            '--interval',
            type=int,
            default=2,
            help='Segundos entre cada intento de conexión (default: 2)',
        )

    def handle(self, *args, **options):
        timeout = options['timeout']
        interval = options['interval']
        elapsed = 0

        self.stdout.write(self.style.WARNING(
            f"[wait_for_db] Esperando conexión con la base de datos..."
        ))

        while elapsed < timeout:
            try:
                # Intenta abrir y cerrar una conexión real a la BD
                conn = connections['default']
                conn.ensure_connection()
                self.stdout.write(self.style.SUCCESS(
                    f"[wait_for_db] ✓ Base de datos disponible tras {elapsed}s."
                ))
                return  # Éxito — sale del comando
            except OperationalError:
                self.stdout.write(
                    f"[wait_for_db] BD no disponible, reintentando en {interval}s... "
                    f"({elapsed}/{timeout}s)"
                )
                time.sleep(interval)
                elapsed += interval

        # Si llegamos aquí, se agotó el tiempo
        self.stderr.write(self.style.ERROR(
            f"[wait_for_db] ✗ La BD no respondió en {timeout}s. Abortando."
        ))
        raise SystemExit(1)
