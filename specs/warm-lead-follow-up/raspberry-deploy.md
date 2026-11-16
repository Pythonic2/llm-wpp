# Publicação no Raspberry — 2026-11-16

## Versões e banco

- Autorização específica recebida para aplicar `saas.0018` e `saas.0019` e publicar backend e frontend.
- Backend, worker e Celery Beat: `ghcr.io/pythonic2/llm-wpp:sha-9b222917b5d5a0df6b3ed77aaf961c4f614ef287`.
- Frontend: `ghcr.io/pythonic2/llm-wpp-frontend:sha-9b077ea33001eb4ddb4cc56e50b7fe3edb30dc05`.
- Backup anterior às migrations: `/home/pi/gesttech-chat-20261116/backup/crm-before-0018-0019.dump`, validado com `pg_restore --list` (333 KiB, modo 600). Os Compose anteriores estão na mesma pasta.
- `saas.0018` e `saas.0019` aplicadas com a imagem nova, antes da troca de containers. `showmigrations` confirmou ambas como aplicadas.

## Verificação

- Backend, worker e agendador atualizados pelo Portainer; frontend atualizado com `docker compose -p front-saas` a partir de `/home/pi/gesttech-chat-20261116/frontend-compose.yml`, pois o container anterior apontava para um Compose temporário. As tags correspondentes também foram enviadas ao GitHub.
- Redis saudável; HTTP 200 no endpoint local `/api/v1/auth/csrf/`, na página frontend local e em `https://crm.gestcloud.com.br/`.
- HTML local e público servem o mesmo asset frontend `index-D0Wfxh4f.js`. Nenhuma linha de erro ou exceção nos logs recentes dos quatro serviços atualizados.
- Nenhuma mensagem com `raw_payload.timestamp` permaneceu sem `sent_at`; nenhuma empresa tinha o follow-up automático habilitado e não havia tentativas de envio. Nenhuma mensagem real foi enviada na validação.

## Recuperação

Se a nova versão falhar, desativar `warm_follow_up_enabled` nas empresas que a tenham ativado e restaurar as tags anteriores dos Compose guardados. As tabelas e colunas da `0019` são aditivas e podem permanecer; o histórico de tentativas deve ser preservado. A `0018` usa reversão de dados vazia: qualquer restauração do banco exige avaliar mensagens criadas depois do backup antes de recuperar dados seletivamente.
