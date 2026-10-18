from django.contrib import admin
from .models import Miscelanea, Usuario


@admin.register(Miscelanea)
class MiscelaneaAdmin(admin.ModelAdmin):
	list_display = ('IdMiscelanea', 'Nombre', 'Codigo', 'Estado')
	search_fields = ('Nombre', 'Codigo')


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
	list_display = ('IdUsuario', 'Nombre', 'Email', 'Miscelanea', 'Rol', 'Estado')
	list_filter = ('Miscelanea', 'Rol', 'Estado')
	search_fields = ('Nombre', 'Email')
