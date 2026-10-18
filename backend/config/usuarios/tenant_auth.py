from django.db import connection
from rest_framework_simplejwt.authentication import JWTAuthentication


class TenantJWTAuthentication(JWTAuthentication):
    """Set the PostgreSQL tenant context after validating a JWT."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result:
            user, _ = result
            tenant_id = str(user.Miscelanea_id or '')
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config(%s, %s, false)",
                    ['app.miscelanea_id', tenant_id],
                )
        return result
