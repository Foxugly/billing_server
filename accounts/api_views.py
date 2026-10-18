"""Authentification de la console d'exploitation.

Ce service n'a **pas d'utilisateurs publics** : il n'y a ni inscription, ni mot de
passe oublié, ni page de création de compte. Les seuls comptes sont ceux des
opérateurs, créés par `createsuperuser`. Le login refuse donc explicitement tout
compte non-staff, plutôt que de délivrer un jeton qui ne servirait à rien — un
refus clair vaut mieux qu'un jeton inerte qu'on croira valide.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenObtainSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
)

from . import refresh_cookie
from .refresh_cookie import PERSIST_CLAIM, deliver_tokens

User = get_user_model()


class StaffTokenObtainPairSerializer(TokenObtainPairSerializer):
    # « Se souvenir de moi » : en mode cookie, `false` donne un cookie de session
    # (claim `persist=false`, conservé par la rotation). Sans effet en mode corps.
    remember_me = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs):
        # Authentification seule (TokenObtainSerializer) : le refresh est émis
        # ici, une seule fois, avec son claim `persist` éventuel.
        TokenObtainSerializer.validate(self, attrs)
        if not self.user.is_staff:
            raise serializers.ValidationError(
                {"detail": "Ce service est réservé aux opérateurs."}, code="not_staff"
            )
        refresh = self.get_token(self.user)
        request = self.context.get("request")
        if request is not None and refresh_cookie.wants_cookie(request) and not attrs["remember_me"]:
            refresh[PERSIST_CLAIM] = False
        if api_settings.UPDATE_LAST_LOGIN:
            update_last_login(None, self.user)
        return {"refresh": str(refresh), "access": str(refresh.access_token)}


class LoginThrottle(AnonRateThrottle):
    """Ce compte pilote Stripe en live : pas de devinette de mot de passe à la
    chaîne (audit 2026-10-18, I1). Par IP, derrière nginx (NUM_PROXIES = 1)."""

    scope = "auth_token"
    rate = "5/min"


class StaffTokenObtainPairView(TokenObtainPairView):
    """Login. Avec `X-Auth-Mode: cookie`, le refresh part en cookie HttpOnly et
    sort du corps ; l'Origin doit alors être autorisée (403 `origin_not_allowed`)."""

    serializer_class = StaffTokenObtainPairSerializer
    throttle_classes = [LoginThrottle]

    def post(self, request, *args, **kwargs):
        if (rejected := refresh_cookie.origin_guard(request)) is not None:
            return rejected
        return deliver_tokens(request, super().post(request, *args, **kwargs))


def _token_from_body_or_cookie(request) -> str:
    body_token = request.data.get("refresh") if hasattr(request.data, "get") else None
    return body_token or refresh_cookie.read_refresh_cookie(request)


class CookieTokenRefreshView(TokenRefreshView):
    """Refresh + rotation. Mode corps : `{refresh}` -> `{access, refresh}`, inchangé.

    Mode cookie : le `refresh` du corps est facultatif ; à défaut, le cookie
    HttpOnly sert (401 `refresh_cookie_missing` s'il manque). Le jeton roté est
    posé dans le cookie et retiré du corps. Un jeton présenté dans le corps avec
    l'en-tête est la migration unique d'une console qui le gardait en localStorage.
    """

    def post(self, request, *args, **kwargs):
        if not refresh_cookie.wants_cookie(request):
            return super().post(request, *args, **kwargs)
        if (rejected := refresh_cookie.origin_guard(request)) is not None:
            return rejected
        token = _token_from_body_or_cookie(request)
        if not token:
            return Response(
                {"code": "refresh_cookie_missing", "detail": "Aucun cookie de session : reconnectez-vous."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        serializer = TokenRefreshSerializer(data={"refresh": token})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0]) from exc
        return deliver_tokens(request, Response(serializer.validated_data, status=status.HTTP_200_OK))


class CookieTokenBlacklistView(TokenBlacklistView):
    """Logout. Mode corps : `{refresh}` -> 200, inchangé.

    Mode cookie (aucun Bearer requis : l'accès en mémoire peut avoir expiré) : met
    sur liste noire le jeton du corps, à défaut celui du cookie, **efface toujours
    le cookie** et répond 204 — idempotent, le navigateur finit déconnecté.
    """

    def post(self, request, *args, **kwargs):
        if not refresh_cookie.wants_cookie(request):
            return super().post(request, *args, **kwargs)
        if (rejected := refresh_cookie.origin_guard(request)) is not None:
            return rejected
        token = _token_from_body_or_cookie(request)
        if token:
            try:
                RefreshToken(token).blacklist()
            except TokenError:
                pass  # déjà révoqué ou expiré : le but (déconnecté) est atteint
        response = Response(status=status.HTTP_204_NO_CONTENT)
        refresh_cookie.clear_refresh_cookie(response)
        return response


def revoke_all_refresh_tokens(user):
    """Met sur liste noire tous les refresh encore valides de cet utilisateur."""
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


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
    (refusé par les validateurs Django, messages dans `messages`).

    Succès : 200 `{access, refresh}`. Tous les refresh existants sont révoqués —
    une session volée ne survit pas au changement — et la console garde la
    nouvelle paire, sans quoi l'opérateur serait éjecté à l'expiration de l'accès
    (OPERATIONS.md §3.25). En mode cookie (`X-Auth-Mode: cookie`), le refresh neuf
    est posé dans le cookie (même choix « se souvenir de moi » que la session
    courante) et retiré du corps ; l'Origin doit être autorisée.
    """

    permission_classes = [permissions.IsAdminUser]
    throttle_classes = [PasswordChangeThrottle]

    @extend_schema(request=PasswordChangeSerializer, responses={200: None, 400: None})
    def post(self, request):
        if (rejected := refresh_cookie.origin_guard(request)) is not None:
            return rejected
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

        persist = _cookie_session_persists(request)
        user.set_password(new)
        user.save(update_fields=["password"])
        revoke_all_refresh_tokens(user)
        refresh = RefreshToken.for_user(user)
        if not persist:
            refresh[PERSIST_CLAIM] = False
        return deliver_tokens(
            request, Response({"access": str(refresh.access_token), "refresh": str(refresh)})
        )


def _cookie_session_persists(request) -> bool:
    """Reprend le choix « se souvenir de moi » de la session cookie courante."""
    if not refresh_cookie.wants_cookie(request):
        return True
    current = refresh_cookie.read_refresh_cookie(request)
    return refresh_cookie.token_persists(current) if current else True
