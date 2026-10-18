from django.contrib import admin
from django import forms
from .models import Miscelanea, Usuario


@admin.register(Miscelanea)
class MiscelaneaAdmin(admin.ModelAdmin):
	list_display = ('IdMiscelanea', 'Nombre', 'Codigo', 'Estado')
	search_fields = ('Nombre', 'Codigo')


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
