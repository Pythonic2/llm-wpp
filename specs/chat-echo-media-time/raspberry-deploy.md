# Publicação no Raspberry — plano de execução

## Estado e artefatos

- Versão atual: backend/worker/agendador `sha-b92a3d5ae9f4a3fdfa39462c331676a040ba720d`; frontend `sha-d219e0d56e0564f54744fac3fde5dce78d49109c`.
- Versão preparada: backend `sha-a9e8edaa774ff63139771615b0cfd926b511c1e8`; frontend `sha-9a85466e7c9b57a329bc405bd41473e3b30b56fb`. Actions passaram e as imagens foram baixadas no Raspberry.
- Backup do schema CRM verificado por `pg_restore --list`: `/home/pi/gesttech-chat-20261116/backup/crm-before-0018.dump` (modo 600). Contagem anterior: 342 mensagens com `sent_at` vazio e `raw_payload.timestamp` presente.
- A Meta confirmou `messages` e `smb_message_echoes`; o usuário confirmou recebimento de um novo eco no CRM. A nova imagem passou em `manage.py check` com a configuração atual do Raspberry.

## Sequência após autorização específica

1. Confirmar imagens e backup. Aplicar `saas.0018` na imagem nova por container descartável com as variáveis atuais da stack; conferir `showmigrations` e contagem posterior. A migration altera somente `sent_at` nulo e preserva o resto.
2. Enviar os Compose com tags fixas ao GitHub. Portainer GitOps atualizará `saas` (backend, worker, agendador) e `front-saas`; não adicionar `env_file` nem alterar segredos.
3. Conferir a imagem efetiva dos containers, saúde Redis, HTTP local e público, webhook, lista ordenada e download de foto/vídeo autorizado. Verificar ausência de nova gravação de imagem/vídeo no storage.

## Recuperação

Guardar cópias dos dois Compose anteriores no Raspberry antes da atualização. Se um serviço falhar, restaurar as tags anteriores e publicar novamente via GitOps. A migration é aditiva; a imagem anterior continua compatível. A reversão de dados de `sent_at` requer restaurar o backup, pois a operação reversa é intencionalmente vazia. Evitar restaurar o banco inteiro sem avaliar mensagens criadas após o backup.
