"""Authentification de la console d'exploitation.

Ce service n'a **pas d'utilisateurs publics** : il n'y a ni inscription, ni mot de
passe oublié, ni page de création de compte. Les seuls comptes sont ceux des
opérateurs, créés par `createsuperuser`. Le login refuse donc explicitement tout
compte non-staff, plutôt que de délivrer un jeton qui ne servirait à rien — un
refus clair vaut mieux qu'un jeton inerte qu'on croira valide.
"""
from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

User = get_user_model()


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


class MeSerializer(serializers.ModelSerializer):
    """Profil de l'opérateur connecté.

    Seuls le prénom et le nom sont modifiables : l'email est l'identifiant de
    connexion (USERNAME_FIELD) et les drapeaux staff/superuser relèvent du Django
    admin, pas d'un écran en libre-service.
    """

    firstName = serializers.CharField(
        source="first_name", max_length=150, allow_blank=True, required=False
    )
    lastName = serializers.CharField(
        source="last_name", max_length=150, allow_blank=True, required=False
    )
    displayName = serializers.CharField(source="get_full_name", read_only=True)
    isStaff = serializers.BooleanField(source="is_staff", read_only=True)
    isSuperuser = serializers.BooleanField(source="is_superuser", read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "firstName", "lastName", "displayName", "isStaff", "isSuperuser"]
        read_only_fields = ["id", "email"]

    def validate_firstName(self, value):
        return value.strip()

    def validate_lastName(self, value):
        return value.strip()


class MeView(generics.RetrieveUpdateAPIView):
    """Identité de l'opérateur connecté (en-tête de la console) et page Profil.

    GET lit le profil ; PATCH modifie prénom et nom. PUT n'est pas exposé : il n'y a
    rien d'autre à remplacer, et un PUT partiel serait trompeur.
    """

    permission_classes = [permissions.IsAdminUser]
    serializer_class = MeSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user
