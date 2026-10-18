"""Authentification de la console d'exploitation.

Ce service n'a **pas d'utilisateurs publics** : il n'y a ni inscription, ni mot de
passe oublié, ni page de création de compte. Les seuls comptes sont ceux des
opérateurs, créés par `createsuperuser`. Le login refuse donc explicitement tout
compte non-staff, plutôt que de délivrer un jeton qui ne servirait à rien — un
refus clair vaut mieux qu'un jeton inerte qu'on croira valide.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import generics, permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema
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


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)


class PasswordChangeThrottle(UserRateThrottle):
    """Un jeton volé ne doit pas servir à deviner le mot de passe actuel."""

    scope = "password_change"
    rate = "5/min"


def _password_error(code, detail, **extra):
    return Response(
        {"code": code, "detail": detail, **extra}, status=status.HTTP_400_BAD_REQUEST
    )


class PasswordChangeView(APIView):
    """POST /api/v1/auth/password/change/ — l'opérateur change son mot de passe.

    Corps : {current_password, new_password}. Les erreurs portent un `code`
    stable que la console traduit : `invalid` (champ manquant),
    `current_password_invalid`, `password_unchanged`, `password_invalid`
    (refusé par les validateurs Django, messages dans `messages`). Aucun jeton
    n'est réémis : l'accès en cours reste valable jusqu'à son expiration.
    """

    permission_classes = [permissions.IsAdminUser]
    throttle_classes = [PasswordChangeThrottle]

    @extend_schema(request=PasswordChangeSerializer, responses={204: None, 400: None})
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        if not serializer.is_valid():
            return _password_error("invalid", "Champs manquants.", fields=serializer.errors)
        current = serializer.validated_data["current_password"]
        new = serializer.validated_data["new_password"]
        user = request.user

        if not user.check_password(current):
            return _password_error("current_password_invalid", "Mot de passe actuel incorrect.")
        if new == current:
            return _password_error(
                "password_unchanged", "Le nouveau mot de passe doit être différent."
            )
        try:
            validate_password(new, user=user)
        except DjangoValidationError as exc:
            return _password_error(
                "password_invalid", "Mot de passe refusé.", messages=list(exc.messages)
            )

        user.set_password(new)
        user.save(update_fields=["password"])
        return Response(status=status.HTTP_204_NO_CONTENT)
