# Publicação no Raspberry — 16/11/2026

## Estado publicado

- Backend, worker e agendador: imagem `ghcr.io/pythonic2/llm-wpp:sha-b92a3d5ae9f4a3fdfa39462c331676a040ba720d`.
- Frontend: imagem `ghcr.io/pythonic2/llm-wpp-frontend:sha-d219e0d56e0564f54744fac3fde5dce78d49109c`.
- Compose backend em Git `6e27bc4`; frontend em Git `5ae4e47`.
- Migration aditiva `saas.0017_asaas_environment` aplicada após backup do schema CRM.
- Chave de produção e token do webhook no arquivo privado `/home/pi/gesttech-secrets/whatsapp.env`, montado em `/app/.env` nos três processos Django/Celery. O Compose não contém os valores.
- Webhook Asaas de produção cadastrado para `/api/webhooks/asaas/` com fila ativa e autenticação por token. Nenhuma cobrança real de teste foi criada.

## Verificações realizadas

Suíte Python: 118 testes passaram no PostgreSQL/pgvector local, incluindo 16 de faturamento após a correção final. Frontend: 29 testes, lint e build passaram. `manage.py check` sem erro. O Compose novo foi validado no Raspberry; um container descartável confirmou que o Django lê a chave e o token privados. A chamada técnica autenticada ao webhook público retornou HTTP 200; sem token, HTTP 403. Frontend local e público retornaram HTTP 200; backend, worker, agendador e Redis estavam ativos. O Portainer guardou os dois Compose com as novas tags.

## Incidente e recuperação

O primeiro Compose usou `env_file` apontando para um arquivo visível no host, mas não ao processo do Portainer. A tentativa de publicação falhou e retirou os containers backend do ar. Eles foram restaurados com o Compose anterior salvo em `/home/pi/gesttech-asaas-20261115/backup/backend-compose.yml`; depois foi publicado o Compose corrigido com bind mount privado. O backup do banco é `/home/pi/gesttech-asaas-20261115/backup/crm-before-0017.dump`; os Compose anteriores do backend e frontend também estão nessa pasta. A migration 0017 é aditiva e não exige reversão para usar temporariamente a imagem anterior.

## Pendência de segurança

O aviso do Portainer expôs a chave de produção porque o valor com `$` foi interpolado como variável. O proprietário deve gerar uma nova chave no Asaas e remover a chave antiga do Portainer. Ao receber a nova chave por canal privado local, atualizar o arquivo montado, confirmar o acesso à API de produção e só então revogar a antiga. Não registrar o valor em Git, chat ou logs. A cobrança real está ativada, mas a rotação da chave é urgente.
