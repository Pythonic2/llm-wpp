# WhatsApp Embedded Signup V4

## Preparação

Este repositório contém o backend Django/DRF. O painel React está em `../frontend-llm-wpp`. A integração usa a configuração de Facebook Login for Business **V4** já validada pelo projeto. Não altere a configuração em produção automaticamente. A [Meta informa](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users#step-2-customize-embedded-signup) que V5 ativa o onboarding do Business app por padrão, sem exigir `featureType`; por isso a migração de V4 para V5 requer nova configuração e validação do launcher, sem reaproveitar os parâmetros às cegas.

Variáveis necessárias no backend (valores secretos ficam apenas no `.env` local ou no gerenciador de segredos do ambiente):

| Variável | Uso |
| --- | --- |
| `META_APP_ID` | App ID público entregue ao SDK. |
| `META_EMBEDDED_SIGNUP_CONFIG_ID` | ID da configuração V4 de Facebook Login for Business. |
| `WHATSAPP_APP_SECRET` | Troca do code e verificação HMAC dos webhooks. |
| `WHATSAPP_TOKEN_ENCRYPTION_KEY` | Chave Fernet estável para tokens e derivação do PIN de registro normal. A rotação exige plano de migração de credenciais. |
| `WHATSAPP_VERIFY_TOKEN` | Verificação inicial do callback no App Dashboard. |
| `WHATSAPP_API_VERSION` | Versão da Graph API já adotada pelo ambiente. Não determina a versão do Embedded Signup. |

`WHATSAPP_ACCESS_TOKEN` permanece como fallback apenas para números manuais. Números oficiais usam token por número, criptografado no banco. Nenhum token, App Secret ou PIN retorna pela API.

No App Dashboard da Meta, confirme a configuração **Gest Tech — Cadastro WhatsApp** com Cloud API, Embedded Signup V4 e session info version 3. Mantenha `whatsapp_business_messaging` e `whatsapp_business_management` com Advanced Access. Configure no produto Webhooks o callback `GET/POST /api/webhooks/whatsapp/`, o mesmo verify token do ambiente e os campos `messages`, `history`, `smb_message_echoes`, `smb_app_state_sync` e `account_update`. A assinatura `X-Hub-Signature-256` é validada antes de processar eventos. A inscrição da WABA no app é feita pelo backend depois da autorização.

## Fluxo

1. O painel busca configuração pública e um `state` de uso único para o usuário e empresa autenticados, e carrega o SDK oficial antecipadamente.
2. O cliente lê e aceita o aviso de riscos. O clique em **Conectar WhatsApp** chama `FB.login` diretamente. O launcher envia `config_id`, `response_type: "code"`, `override_default_response_type: true` e `extras` com `setup`, `featureType: "whatsapp_business_app_onboarding"`, `sessionInfoVersion: "3"` e `version: "v4"`.
3. O painel aceita `FINISH` ou `FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING` de origem `facebook.com`. Em coexistência, o evento pode conter somente `waba_id`; o código chega no callback do SDK.
4. O backend confere autenticação, papel admin, CSRF e `state` válido/único, troca o code, consulta a WABA e seus números na Meta e verifica `is_on_biz_app` e `platform_type` no número escolhido. Sem ID no evento de coexistência, somente um candidato elegível é aceito.
5. O número é associado apenas à empresa autenticada, com unicidade global de Phone Number ID. O token é criptografado. O backend assina a WABA nos webhooks.
6. Na conexão normal, após consentimento registrado e confirmação de que o número **não** está no Business app, o backend chama `register` com PIN derivado da chave estável do servidor. Na coexistência, **não chama `register` ou API de migração**.
7. Na coexistência, o backend inicia uma vez as sincronizações `smb_app_state_sync` e `history` e guarda seus `request_id` para suporte. A Meta permite uma solicitação de cada e exige a sincronização dentro de 24 horas. Falhas ambíguas ficam em `sync_failed` e exigem análise operacional; não há retry automático de chamada única.

O painel mostra estado, número, nome da WABA e última sincronização. A IA começa desativada em números de coexistência. O administrador pode ativá-la e configurar a pausa após uma resposta manual pelo aplicativo; um eco pausa a conversa por padrão. Pedidos identificados pela IA continuam exigindo aprovação humana.

## Webhooks e idempotência

`messages` conserva o processamento existente, por Phone Number ID, e usa o ID de mensagem como chave única. `smb_message_echoes` registra resposta manual como saída e nunca chama IA. `smb_app_state_sync` atualiza contatos. `history` é persistido em `CoexistenceWebhookBatch` por impressão SHA-256 e processado pelo worker Celery; mensagens históricas são gravadas com IA ignorada. Payload repetido é seguro. `account_update` com `PARTNER_REMOVED` desativa a integração local.

Quando um webhook identifica o cliente somente por BSUID, sem número de telefone utilizável, a mensagem é guardada e a IA não envia resposta automática para um destinatário incerto.

Uma falha ao enfileirar histórico faz o webhook responder erro, para que a Meta tente novamente. Mantenha backend, banco e worker disponíveis durante a janela de sincronização. A conclusão da sincronização é indicada pelo `progress: 100` no histórico; se o cliente não compartilhar o histórico, o estado fica `history_declined`.

## Desconexão e recuperação

**Desconectar** no CRM desativa o número e remove a credencial local, preservando conversas. Para coexistência, o cliente pode usar **WhatsApp Business > Configurações > Conta > Plataforma de Negócios > Desconectar conta** para retirar a Cloud API; a Meta então envia `PARTNER_REMOVED`. A API de deregistro não é usada. Para reconectar, iniciar novo Embedded Signup. Não execute migrations, deploy ou chamadas de teste com número real sem autorização específica.

## Teste seguro

Use empresa e número de teste, banco local separado e mocks da Meta para testes automatizados. O `.env` deste workspace aponta para banco remoto; os testes de desenvolvimento usam um módulo de settings temporário com SQLite, sem consultar esse banco. Para teste manual, configure App ID/Config ID de ambiente de teste, acesso de administrador no CRM, SDK liberado no domínio, callback HTTPS e worker. Conclua o fluxo com número de teste elegível, confira estado e webhooks no painel, depois desconecte localmente. Não reutilize número real de cliente no teste.

Validação local em 15/11/2026: suíte Django com **79 aprovados e 1 falha antiga** em `test_admin_can_enable_hot_lead_notifications` (o teste espera telefone formatado; o serializador já existente salva formato normalizado). Testes React **19 aprovados**; TypeScript, ESLint, build Vite, `manage.py check` e `makemigrations --check --dry-run` passaram. Os testes Django usaram SQLite temporário. Após autorização específica, a migration aditiva `saas.0013` foi aplicada ao banco de produção e o deploy no Raspberry foi validado por HTTP 200 local e público; ver [registro do deploy](../specs/whatsapp-embedded-signup/raspberry-deploy.md). Não houve teste real de cadastro com a Meta.

## Limitações

- A Meta pode negar coexistência por elegibilidade; o painel não promete a modalidade. A [documentação oficial de coexistência](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users) descreve mudanças em recursos do aplicativo e dispositivos vinculados.
- A configuração V4 e o token do ambiente precisam existir; sem eles o botão não inicia. O App Dashboard ainda precisa dos campos de webhook configurados externamente.
- A API guarda o histórico recebido, mas não faz download retroativo dos anexos de mídia histórica. A Meta envia IDs de mídia em webhooks separados.
- O status da sincronização depende dos webhooks efetivamente recebidos. Uma resposta ambígua à solicitação única de sincronização requer intervenção operacional, sem repetir a chamada automaticamente.
- Números manuais antigos ainda podem usar `WHATSAPP_ACCESS_TOKEN` global. Para isolamento de credenciais por empresa, prefira o novo fluxo oficial; remover esse fallback exige plano próprio de migração dos números manuais.

Fontes: [Embedded Signup V4](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/version-4), [Business app onboarding e sincronização](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users), [verificação de webhooks](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/).
