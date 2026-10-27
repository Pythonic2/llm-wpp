# API administrativa Gest Tech

Todos os endpoints administrativos estão sob `/api/v1/` e usam autenticação por sessão Django. Chamadas de alteração exigem o cookie CSRF e o cabeçalho `X-CSRFToken`. O cliente deve chamar `GET /api/v1/auth/csrf/` antes do login. Cookies de sessão e CSRF devem ser enviados com credenciais.

O webhook da Meta continua separado em `/api/webhooks/whatsapp/` e não faz parte da API do navegador.

## Sessão

| Método e rota | Contrato |
|---|---|
| `GET /auth/csrf/` | Define cookie CSRF e responde `{ "csrfToken": "..." }`. |
| `POST /auth/login/` | Recebe `{ "username": "...", "password": "..." }` (o campo username também aceita e-mail). Inicia a sessão e responde `{ "user": User, "companies": Company[] }`. |
| `POST /auth/logout/` | Encerra a sessão; responde `204`. |
| `POST /auth/refresh/` | Renova a chave da sessão e responde `{ "user": User }`. |
| `GET /auth/me/` | Responde usuário e empresas disponíveis para a sessão. |

`User` inclui `id`, `username`, `email`, `first_name` e `last_name`. `Company` inclui `id` (UUID), `name`, `slug`, `created_at` e `role`.

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

## Conhecimento

- `GET|POST /companies/{company_id}/knowledge-bases/`; `GET|PATCH|DELETE /companies/{company_id}/knowledge-bases/{id}/`.
- `GET|POST /companies/{company_id}/knowledge-bases/{base_id}/documents/`.
- `GET|DELETE /companies/{company_id}/knowledge-bases/{base_id}/documents/{id}/`.
- `POST /companies/{company_id}/knowledge-bases/{base_id}/documents/{id}/reprocess/`.

Upload multipart requer `title` e `file`; são aceitos `.pdf`, `.txt` e `.csv`. O upload e o reprocessamento enfileiram `process_document` via Celery. O serializer do documento retorna `id`, `title`, `file_name`, `status`, `error` e `created_at`, sem expor o caminho interno do arquivo. Bases e documentos exigem papel `admin` ou `owner`.

## Paginação e erros

Listas paginadas usam o envelope DRF `{ "count": 0, "next": null, "previous": null, "results": [] }`. O parâmetro `page_size` aceita valores até 100. Empresas, membros e conversas por contato são listas diretas. Os endpoints retornam erros DRF com status HTTP apropriado; IDs de registros de outra empresa resultam em `404`, sem revelar a existência do recurso.
