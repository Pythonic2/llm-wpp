import hashlib
import hmac
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
        if getattr(number, "connection_mode", "manual") != "manual":
            raise MetaConfigurationError("Este número não possui credencial ativa.")
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


def verify_embedded_signup_assets(token, waba_id, phone_number_id, coexistence):
    if (not waba_id.isdecimal() or len(waba_id) > 64 or
            (phone_number_id and (not phone_number_id.isdecimal() or len(phone_number_id) > 64))):
        raise ValueError("Identificadores da conexão inválidos.")
    headers = {"Authorization": f"Bearer {token}"}
    account_response = httpx.get(graph_url(waba_id), headers=headers, params={"fields": "id,name"}, timeout=20)
    account_response.raise_for_status()
    account = account_response.json()
    if str(account.get("id")) != waba_id:
        raise ValueError("A conta WhatsApp não foi autorizada.")
    phones = []
    cursor = None
    for _ in range(10):
        params = {"fields": "id,display_phone_number,verified_name", "limit": 100}
        if cursor:
            params["after"] = cursor
        response = httpx.get(graph_url(f"{waba_id}/phone_numbers"), headers=headers, params=params, timeout=20)
        response.raise_for_status()
        page = response.json()
        phones.extend(page.get("data", []))
        next_cursor = page.get("paging", {}).get("cursors", {}).get("after")
        if not page.get("paging", {}).get("next"):
            break
        if not next_cursor or next_cursor == cursor:
            raise MetaConfigurationError("Não foi possível conferir todos os números da conta.")
        cursor = next_cursor
    else:
        raise MetaConfigurationError("A conta possui números demais para validação automática.")
    candidates = [item for item in phones if not phone_number_id or str(item.get("id")) == phone_number_id]
    if not candidates:
        raise ValueError("O número não pertence à conta WhatsApp autorizada.")
    verified = []
    for item in candidates:
        phone_id = str(item.get("id", ""))
        if not phone_id.isdecimal() or len(phone_id) > 64:
            continue
        detail_response = httpx.get(
            graph_url(phone_id), headers=headers,
            params={"fields": "id,display_phone_number,verified_name,is_on_biz_app,platform_type"}, timeout=20,
        )
        detail_response.raise_for_status()
        detail = detail_response.json()
        if str(detail.get("id")) != phone_id or not detail.get("display_phone_number"):
            continue
        if coexistence and detail.get("is_on_biz_app") is True and detail.get("platform_type") == "CLOUD_API":
            verified.append(detail)
        if not coexistence and detail.get("is_on_biz_app") is False and detail.get("platform_type") == "CLOUD_API":
            verified.append(detail)
    if len(verified) != 1:
        raise ValueError("A Meta não confirmou um número único e elegível para esta conexão.")
    return account, verified[0]


def register_cloud_api_number(token, phone_number_id):
    # Coexistência nunca chama este método. O PIN permanece recuperável com a chave estável do servidor.
    if not settings.WHATSAPP_TOKEN_ENCRYPTION_KEY:
        raise MetaConfigurationError("WHATSAPP_TOKEN_ENCRYPTION_KEY não configurada.")
    digest = hmac.new(
        settings.WHATSAPP_TOKEN_ENCRYPTION_KEY.encode(),
        f"whatsapp-register:{phone_number_id}".encode(), hashlib.sha256,
    ).digest()
    pin = f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"
    response = httpx.post(
        graph_url(f"{phone_number_id}/register"),
        headers={"Authorization": f"Bearer {token}"},
        json={"messaging_product": "whatsapp", "pin": pin}, timeout=20,
    )
    response.raise_for_status()
    if not response.json().get("success"):
        raise MetaConfigurationError("A Meta não confirmou o registro do número.")


def request_coexistence_sync(token, phone_number_id, sync_type):
    if sync_type not in {"smb_app_state_sync", "history"}:
        raise ValueError("Tipo de sincronização inválido.")
    response = httpx.post(
        graph_url(f"{phone_number_id}/smb_app_data"),
        headers={"Authorization": f"Bearer {token}"},
        json={"messaging_product": "whatsapp", "sync_type": sync_type}, timeout=20,
    )
    response.raise_for_status()
    request_id = str(response.json().get("request_id", ""))
    if not request_id:
        raise MetaConfigurationError("A Meta não confirmou a solicitação de sincronização.")
    return request_id


def subscribe_app_to_waba(token, waba_id):
    response = httpx.post(
        graph_url(f"{waba_id}/subscribed_apps"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    response.raise_for_status()
    if not response.json().get("success"):
        raise MetaConfigurationError("A Meta não confirmou a inscrição dos webhooks.")
