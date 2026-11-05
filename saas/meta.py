import httpx
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class MetaConfigurationError(RuntimeError):
    pass


def encrypt_access_token(token):
    if not settings.WHATSAPP_TOKEN_ENCRYPTION_KEY:
        raise MetaConfigurationError("WHATSAPP_TOKEN_ENCRYPTION_KEY não configurada.")
    return Fernet(settings.WHATSAPP_TOKEN_ENCRYPTION_KEY.encode()).encrypt(token.encode()).decode()


def access_token_for_number(number):
    if not getattr(number, "access_token_encrypted", ""):
        return settings.WHATSAPP_ACCESS_TOKEN
    try:
        return Fernet(settings.WHATSAPP_TOKEN_ENCRYPTION_KEY.encode()).decrypt(
            number.access_token_encrypted.encode()
        ).decode()
    except (ValueError, InvalidToken) as exc:
        raise MetaConfigurationError("Não foi possível abrir a credencial do número.") from exc


def graph_url(path):
    return f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{path.lstrip('/')}"


def exchange_embedded_signup_code(code):
    if not settings.META_APP_ID or not settings.WHATSAPP_APP_SECRET:
        raise MetaConfigurationError("Credenciais do aplicativo Meta não configuradas.")
    response = httpx.get(
        graph_url("oauth/access_token"),
        params={
            "client_id": settings.META_APP_ID,
            "client_secret": settings.WHATSAPP_APP_SECRET,
            "code": code,
        },
        timeout=20,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise MetaConfigurationError("A Meta não retornou uma credencial de acesso.")
    return token


def verify_coexistence_assets(token, waba_id, phone_number_id):
    headers = {"Authorization": f"Bearer {token}"}
    response = httpx.get(
        graph_url(f"{waba_id}/phone_numbers"),
        headers=headers,
        params={"fields": "id,display_phone_number,verified_name"},
        timeout=20,
    )
    response.raise_for_status()
    phone = next(
        (item for item in response.json().get("data", []) if str(item.get("id")) == str(phone_number_id)),
        None,
    )
    if phone is None:
        raise ValueError("O número informado não pertence à conta WhatsApp autorizada.")
    return phone


def subscribe_app_to_waba(token, waba_id):
    response = httpx.post(
        graph_url(f"{waba_id}/subscribed_apps"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    response.raise_for_status()
    if not response.json().get("success"):
        raise MetaConfigurationError("A Meta não confirmou a inscrição dos webhooks.")
