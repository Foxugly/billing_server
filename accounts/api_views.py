"""Authentification de la console d'exploitation.

Ce service n'a **pas d'utilisateurs publics** : il n'y a ni inscription, ni mot de
passe oublié, ni page de création de compte. Les seuls comptes sont ceux des
opérateurs, créés par `createsuperuser`. Le login refuse donc explicitement tout
compte non-staff, plutôt que de délivrer un jeton qui ne servirait à rien — un
refus clair vaut mieux qu'un jeton inerte qu'on croira valide.
"""
from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView


class StaffTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        if not self.user.is_staff:
            raise serializers.ValidationError(
                {"detail": "Ce service est réservé aux opérateurs."}, code="not_staff"
            )
        return data


class StaffTokenObtainPairView(TokenObtainPairView):
    serializer_class = StaffTokenObtainPairSerializer


class MeView(APIView):
    """Identité de l'opérateur connecté, pour l'en-tête de la console."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        user = request.user
        return Response(
            {
                "id": user.id,
                "email": user.email,
                "displayName": user.get_full_name(),
                "isStaff": user.is_staff,
                "isSuperuser": user.is_superuser,
            }
        )
