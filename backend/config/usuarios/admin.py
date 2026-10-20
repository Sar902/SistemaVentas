from django.contrib import admin
from django import forms
from django.conf import settings
from .models import Miscelanea, Usuario


from django.utils.html import format_html


def _technical_admin_only(self, request):
	return bool(
		request.user
		and request.user.is_active
		and request.user.is_staff
		and request.user.Email.lower() == settings.DJANGO_ADMIN_EMAIL
	)


admin.AdminSite.has_permission = _technical_admin_only


@admin.register(Miscelanea)
class MiscelaneaAdmin(admin.ModelAdmin):
	list_display = ('IdMiscelanea', 'Nombre', 'Codigo', 'preview_logo_claro', 'preview_logo_oscuro', 'Estado')
	search_fields = ('Nombre', 'Codigo')
	fields = ('Nombre', 'Codigo', 'TextoReciboSuperior', 'TextoRecibo', 'Logo', 'preview_logo_claro', 'LogoOscuro', 'preview_logo_oscuro', 'Estado')
	readonly_fields = ('preview_logo_claro', 'preview_logo_oscuro')

	def preview_logo_claro(self, obj):
		if obj and obj.Logo:
			return format_html(
				'<div style="display:inline-block; padding:4px; background:#f8fafc; border:1px solid #cbd5e1; border-radius:50%;">'
				'<img src="{}" style="width:50px; height:50px; object-fit:cover; border-radius:50%; display:block;" />'
				'</div>',
				obj.Logo.url
			)
		return "Sin logo claro"
	preview_logo_claro.short_description = "Vista previa (Claro)"

	def preview_logo_oscuro(self, obj):
		if obj and obj.LogoOscuro:
			return format_html(
				'<div style="display:inline-block; padding:4px; background:#0f172a; border:1px solid #334155; border-radius:50%;">'
				'<img src="{}" style="width:50px; height:50px; object-fit:cover; border-radius:50%; display:block;" />'
				'</div>',
				obj.LogoOscuro.url
			)
		return "Sin logo oscuro"
	preview_logo_oscuro.short_description = "Vista previa (Oscuro)"


class UsuarioAdminForm(forms.ModelForm):
	password = forms.CharField(
		label='Contraseña',
		required=False,
		widget=forms.PasswordInput(render_value=False),
	)

	class Meta:
		model = Usuario
		fields = '__all__'

	def save(self, commit=True):
		old_password = self.instance.password if self.instance.pk else None
		user = super().save(commit=False)
		raw_password = self.cleaned_data.get('password')

		if raw_password:
			user.set_password(raw_password)
		elif old_password:
			user.password = old_password

		if commit:
			user.save()
			self.save_m2m()
		return user


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
	form = UsuarioAdminForm
	list_display = ('IdUsuario', 'Nombre', 'Email', 'Miscelanea', 'Rol', 'Estado')
	list_filter = ('Miscelanea', 'Rol', 'Estado')
	search_fields = ('Nombre', 'Email')
