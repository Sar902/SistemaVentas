from rest_framework_simplejwt.authentication import JWTAuthentication

from .tenant_context import set_tenant_context


class TenantJWTAuthentication(JWTAuthentication):
    """Set the PostgreSQL tenant context after validating a JWT."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result:
            user, _ = result
            set_tenant_context(user.Miscelanea_id)
        return result
