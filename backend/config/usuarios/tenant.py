from rest_framework.permissions import IsAuthenticated
from rest_framework import serializers


class TenantScopedViewSet:
    """Restrict model viewsets to the authenticated user's miscelanea."""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated or user.Miscelanea_id is None:
            return self.queryset.none()
        return self.queryset.filter(Miscelanea_id=user.Miscelanea_id)

    def perform_create(self, serializer):
        serializer.save(Miscelanea_id=self.request.user.Miscelanea_id)


class TenantPrimaryKeyRelatedField(serializers.PrimaryKeyRelatedField):
    """Expose only foreign-key choices owned by the current miscelánea."""

    def get_queryset(self):
        queryset = super().get_queryset()
        request = self.context.get('request') if self.context else None
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated or user.Miscelanea_id is None:
            return queryset.none()
        return queryset.filter(Miscelanea_id=user.Miscelanea_id)
