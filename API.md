# API administrativa Gest Tech

Todos os endpoints administrativos estão sob `/api/v1/` e usam autenticação por sessão Django. Chamadas de alteração exigem o cookie CSRF e o cabeçalho `X-CSRFToken`. O cliente deve chamar `GET /api/v1/auth/csrf/` antes do login. Cookies de sessão e CSRF devem ser enviados com credenciais.

O webhook da Meta continua separado em `/api/webhooks/whatsapp/` e não faz parte da API do navegador.

## Cadastro público e assinatura Gest Tech

`POST /api/v1/public/signup/` recebe `email`, `password`, `full_name`, `cpf_cnpj`, `mobile_phone`, `postal_code`, `address`, `address_number`, `complement` opcional, `province`, `city_name`, `state` e `company_name`. Cria conta/empresa pendentes e devolve `status_token`, `status`, `amount` e `checkout_url` do Asaas. Repetir com o mesmo e-mail e senha enquanto a conta estiver pendente retorna a tentativa existente; outra senha recebe erro. O frontend chama `GET /api/v1/public/checkouts/{status_token}/` até confirmação. O redirecionamento do Asaas não ativa acesso. `POST /api/v1/public/verify-email/` confirma um token assinado de 48 horas; `POST /api/v1/public/resend-email/` reenvia o link com resposta genérica. Pagamento e confirmação do e-mail são necessários para ativar a conta.

`POST /api/webhooks/asaas/` exige `asaas-access-token` com token independente da API Key. `CHECKOUT_PAID` autenticado ativa a primeira empresa e a conta. Eventos de pagamento/assinatura e a conciliação diária mantêm o estado de cobrança; após sete dias corridos de vencimento, o acesso ou a vaga adicional é suspensa até confirmação.

| Método e rota | Uso |
|---|---|
| `GET /api/v1/billing/profile/status/` | Indica se o usuário já tem cadastro de cobrança. |
| `POST /api/v1/billing/extra-company/` | Owner compra empresa adicional; recebe `company_name`, opcional `billing_profile` e cabeçalho `Idempotency-Key`. |
| `GET /api/v1/companies/{company_id}/billing/quota/` | Mostra capacidade e números ativos da empresa. |
| `POST /api/v1/companies/{company_id}/billing/extra-number/` | Owner compra vaga adicional; recebe `billing_profile` se necessário e `Idempotency-Key`. |

As compras adicionais usam cartão recorrente. Valores são definidos no servidor. O checkout pode entrar em `review` se a resposta do Asaas for ambígua, caso em que a tentativa exige conciliação antes de nova cobrança.

## Instruções da IA por empresa

`GET /api/v1/companies/{company_id}/` retorna `ai_instructions`. Proprietários e administradores podem atualizar o campo com:

```http
PATCH /api/v1/companies/{company_id}/
Content-Type: application/json

{"ai_instructions":"Atenda como especialista do meu nicho, com linguagem clara e acolhedora."}
```

O limite é de 8.000 caracteres. Regras de isolamento, anti-injeção e uso exclusivo do contexto factual não são editáveis pelo cliente.

## Sessão

| Método e rota | Contrato |
|---|---|
| `GET /auth/csrf/` | Define cookie CSRF e responde `{ "csrfToken": "..." }`. |
| `POST /auth/login/` | Recebe `{ "username": "...", "password": "..." }` (o campo username também aceita e-mail). Inicia a sessão e responde `{ "user": User, "companies": Company[] }`. |
| `POST /auth/logout/` | Encerra a sessão; responde `204`. |
| `POST /auth/refresh/` | Renova a chave da sessão e responde `{ "user": User }`. |
| `GET /auth/me/` | Responde usuário e empresas disponíveis para a sessão. |

`User` inclui `id`, `username`, `email`, `first_name` e `last_name`. `Company` inclui `id` (UUID), `name`, `slug`, `created_at` e `role`. No detalhe da empresa, administradores podem configurar `hot_lead_notifications_enabled` e `hot_lead_notification_phone` (com DDI e DDD); quando uma conversa passa a quente, esse WhatsApp recebe o resumo comercial e a próxima ação.

## Empresas e dashboard

- `GET /companies/` lista as empresas da associação atual (superusuário: todas).
- `GET /companies/{company_id}/` retorna a empresa e o estado booleano das integrações, nunca as credenciais.
- `GET /companies/{company_id}/dashboard/` responde `total_conversations`, `open_conversations`, `human_conversations`, `ai_conversations`, `inbound_messages`, `outbound_messages`, `documents_processing`, `documents_failed` e `recent_conversations`.

## Equipe

- `GET /companies/{company_id}/members/`: lista `{ id, user, role }`.
- `POST /companies/{company_id}/members/`: recebe `{ "user_id": 1, "role": "agent" }` ou `{ "username": "...", "role": "admin" }` para associar uma conta já existente.
- `PATCH /companies/{company_id}/members/{membership_id}/`: recebe `{ "role": "admin" | "agent" }`.
- `DELETE /companies/{company_id}/members/{membership_id}/`: remove a associação; responde `204`.

Membros podem ser listados por qualquer integrante; somente owner/admin (e superusuário) podem alterá-los. Só superusuário concede papel `owner`; o owner atual não pode ser removido ou rebaixado por esta API.

## Atendimento e contatos

- `GET|POST /companies/{company_id}/whatsapp-numbers/`; `GET|PATCH|DELETE /companies/{company_id}/whatsapp-numbers/{id}/`.
- `GET /companies/{company_id}/whatsapp/embedded-signup/config/` retorna configuração pública V4 e `state` transitório, ligado ao usuário e à empresa por 40 minutos. `POST /companies/{company_id}/whatsapp/embedded-signup/complete/` recebe `code`, `waba_id`, `event`, `state`, opcionalmente `phone_number_id` na coexistência, e `standard_migration_consent: true` no fluxo normal. O backend valida os ativos na Meta, troca o código e retorna o número sem credenciais.
- `POST /companies/{company_id}/whatsapp-numbers/{id}/disconnect/` desativa a integração local, elimina a credencial e preserva o histórico. Na coexistência, a desconexão da Cloud API no aplicativo WhatsApp Business é uma etapa separada.
- `GET|POST /companies/{company_id}/contacts/`; o POST recebe `name`, `phone` e o `wa_id` obrigatório. O `wa_id` não pode ser alterado depois da criação.
- `GET|PATCH|DELETE /companies/{company_id}/contacts/{id}/`.
- `GET /companies/{company_id}/contacts/{id}/conversations/` lista as conversas do contato.
- `GET /companies/{company_id}/conversations/` aceita `status`, `assigned_to_id`, `whatsapp_number_id`, `ai_paused` e `search`.
- `GET /companies/{company_id}/follow-ups/` lista oportunidades quentes ou mornas, ainda abertas, cujo prazo de retorno venceu.
- `GET|PATCH /companies/{company_id}/conversations/{id}/`. A atualização permite `status`, `lead_stage`, `lead_data` e `assigned_to_id`.
- `GET /companies/{company_id}/conversations/{id}/messages/` responde uma página cronológica de mensagens. Por padrão, começa na página mais recente; informe `page` para percorrer as anteriores.
- `POST /companies/{company_id}/conversations/{id}/messages/`: recebe `{ "text": "..." }`. Aceita um `Idempotency-Key` único para permitir tentativas seguras; quando o cabeçalho não é enviado, o servidor gera uma chave. Pausa a IA sob o mesmo lock Redis por conversa, persiste o status `pending`, `sent` ou `failed`, responde `409` se outra resposta da IA estiver finalizando e `502` se a Cloud API falhar.
- Ações `POST /companies/{company_id}/conversations/{id}/{pause-ai|resume-ai|assign|close|reopen}/`. `assign` recebe `{ "user_id": 1 }` ou `null`.
- Ações comerciais no mesmo endpoint: `analyze`, `mark-won`, `mark-lost`, `reopen-opportunity`, `complete-follow-up` e `snooze-follow-up` (recebe `{ "days": 3 }`).

Cada conversa expõe `lead_temperature` (`hot`, `warm`, `cold` ou `unknown`), `purchase_intent_score`, `analysis_summary`, `buying_signals`, `objections`, `recommended_action`, `opportunity_status` e `next_follow_up_at`. A análise usa os itens efetivamente conversados e é atualizada em segundo plano.

Todos os endpoints operacionais permitem acesso a `agent`, `admin` e `owner`. A lista de números pode ser consultada por qualquer membro; criar, alterar ou excluir integrações exige `admin` ou `owner`.

## Pedidos com confirmação humana

- `GET /companies/{company_id}/orders/` lista pedidos da empresa e aceita filtro `status`.
- `POST /companies/{company_id}/orders/{order_id}/approve/` e `reject/` exigem administrador ou proprietário.
- Pedidos começam em `awaiting_approval`. A decisão usa lock no banco, registra usuário, horário e transição e não pode ser repetida.
- A confirmação ao cliente é criada pelo backend somente depois da aprovação; uma resposta do Gemini não tem autoridade para confirmar pedidos.

## Conhecimento

- `GET|POST /companies/{company_id}/knowledge-bases/`; `GET|PATCH|DELETE /companies/{company_id}/knowledge-bases/{id}/`.
- `GET|POST /companies/{company_id}/knowledge-bases/{base_id}/documents/`.
- `GET|DELETE /companies/{company_id}/knowledge-bases/{base_id}/documents/{id}/`.
- `POST /companies/{company_id}/knowledge-bases/{base_id}/documents/{id}/reprocess/`.

Upload multipart requer `title` e `file`; são aceitos `.pdf`, `.txt` e `.csv`. O upload e o reprocessamento enfileiram `process_document` via Celery. O serializer do documento retorna `id`, `title`, `file_name`, `status`, `error` e `created_at`, sem expor o caminho interno do arquivo. Bases e documentos exigem papel `admin` ou `owner`.

## Paginação e erros

Listas paginadas usam o envelope DRF `{ "count": 0, "next": null, "previous": null, "results": [] }`. O parâmetro `page_size` aceita valores até 100. Empresas, membros e conversas por contato são listas diretas. Os endpoints retornam erros DRF com status HTTP apropriado; IDs de registros de outra empresa resultam em `404`, sem revelar a existência do recurso.
## Biblioteca de mídias

- `GET|POST /api/v1/companies/{company_id}/media-categories/`
- `PATCH|DELETE /api/v1/companies/{company_id}/media-categories/{category_id}/`
- `GET|POST /api/v1/companies/{company_id}/media-assets/`
- `GET|PATCH|DELETE /api/v1/companies/{company_id}/media-assets/{asset_id}/`
- `GET /api/v1/companies/{company_id}/media-assets/{asset_id}/download/`

Criação e alteração exigem papel `admin`; leitura e download exigem associação à
empresa. O upload usa `multipart/form-data`. Campos principais: `name`, `description`,
`category`, `tags`, `keywords`, `product_service`, `search_text`, `is_active`,
`allow_ai_send` e `file`. O download é autenticado e enviado com cache privado.
