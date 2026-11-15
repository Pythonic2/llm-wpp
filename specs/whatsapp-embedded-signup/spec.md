# Embedded Signup V4 do WhatsApp

## Objetivo e limites

Conectar números de uma empresa autenticada à Cloud API pelo Facebook Login for Business V4, incluindo coexistência para números elegíveis do WhatsApp Business app. Reutilizar Django/DRF, `WhatsAppNumber`, webhook e frontend React existentes. Não modificar contas Meta ou dados reais durante desenvolvimento; não migrar números do Business app.

## Fatos observados

- Há fluxo parcial de coexistência. O frontend exige `phone_number_id` no evento, embora a Meta documente `FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING` com apenas `waba_id`.
- O backend troca o code e consulta a WABA, mas não vincula a autorização a uma tentativa transitória nem confirma `is_on_biz_app`.
- Há isolamento por membership e número único, token criptografado e validação de assinatura no webhook. Não há processamento de ecos ou histórico.
- A configuração V4 com `featureType=whatsapp_business_app_onboarding` e `sessionInfoVersion=3` foi validada pelo solicitante. V5 existe, mas a configuração de produção não será alterada.

## Cenários e critérios

1. Dado um administrador autenticado, quando iniciar o fluxo, então recebe configuração pública e estado transitório único com validade curta, vinculado a usuário e empresa.
2. Dado o resultado `FINISH`, quando o code e os IDs de sessão forem enviados com estado válido, então o backend troca o code, consulta a Meta, verifica propriedade dos recursos, associa somente à empresa autenticada e assina o webhook.
3. Dado `FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING`, quando a Meta confirmar um número elegível sob a WABA, então a conexão fica em coexistência sem registro ou migração. Se houver vários números candidatos sem ID confiável, rejeitar sem associar.
4. Dado cancelamento, estado vencido ou reutilizado, erro da Meta, recurso de outra empresa ou número não elegível, então não conectar e retornar erro genérico sem token.
5. Dado webhook assinado para número conhecido, então rotear por `phone_number_id`, gravar eventos idempotentes e nunca disparar IA a partir de eco, histórico ou sincronização de contatos.
6. Dado número conectado, administrador pode ver status, reconectar e desativar localmente com isolamento por empresa; a UI não exibe credenciais.
7. O fluxo normal e a coexistência mantêm números existentes funcionais; pedidos gerados por IA continuam sujeitos à aprovação humana.

## Segurança e recuperação

- Limites de confiança: browser e `postMessage` fornecem pistas; somente consultas autenticadas à Meta determinam recursos e elegibilidade. Sessão Django, CSRF e estado de uso único (40 minutos) protegem a conclusão. Consentimento explícito é registrado para o fluxo normal.
- Tokens ficam criptografados em repouso, ausentes de serializadores/logs. IDs de WABA/número são globais e não podem ser reassociados a outra empresa.
- Eventos duplicados são ignorados por IDs externos estáveis. Falha de processamento retorna status adequado para retentativa; eventos desconhecidos não disparam IA.
- Desativação local é reversível. Revogação externa e mudanças de produção exigem operação específica e não fazem parte do desenvolvimento local.

## Verificação

Testes com mocks da Meta: cenários 1–6, CSRF, acesso entre empresas, replay, token inválido, assinatura e duplicidade. Build/typecheck do frontend e `manage.py check` quando ambiente seguro estiver disponível. Nenhum teste usa número real.

## Fontes oficiais consultadas

- https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/version-4
- https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users
- https://developers.facebook.com/docs/graph-api/webhooks/getting-started/
