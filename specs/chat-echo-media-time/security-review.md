# Revisão de segurança e publicação

## Acesso e isolamento

- O webhook exige assinatura HMAC da Meta e identifica o número pelo `phone_number_id`; ecos são aceitos somente para modo de coexistência. O ID externo é único e repetição não cria outra mensagem.
- O endpoint de anexo exige sessão autenticada, vínculo com a empresa, conversa e mensagem. O teste cobre acesso anônimo e outra empresa. O token Meta fica no backend.
- A URL de mídia retornada pela Meta é restrita a HTTPS e domínios Meta permitidos, sem redirecionamento. O corpo é lido em blocos com limite de `WHATSAPP_ATTACHMENT_MAX_BYTES` (25 MiB por padrão).
- O frontend normaliza a rota de download para a origem da API; não envia o cookie de sessão a uma origem externa.

## Dados e falhas

- Novas fotos e vídeos não são gravados no storage. Anexos históricos já gravados continuam servidos pelo arquivo existente. A busca sob demanda pode falhar quando a Meta expirar ou remover a mídia; a interface exibe o erro.
- A migração `saas.0018` altera somente `sent_at` vazio com `raw_payload.timestamp` válido; não altera `created_at` nem exclui registros. Sua reversão é no-op, pois o valor original pode não ser reconstruído após produção. Antes de aplicar, fazer backup PostgreSQL e registrar contagem de linhas elegíveis; recuperar via backup se necessário.
- Alterar a ordenação pode mover mensagens entre páginas do histórico; desempate por chave primária mantém a ordem estável para timestamps iguais.
- A captura real de mensagens enviadas pelo aplicativo depende de `smb_message_echoes` no App Dashboard Meta. O usuário informou que ativará o campo; só verificar entrega real após a confirmação.

## Verificação antes do deploy

- Testes backend e frontend, `makemigrations --check`, build e lint.
- Confirmar assinatura Meta `messages` e `smb_message_echoes` sem registrar IDs/tokens.
- Backup do banco e autorização específica para a migração de dados `0018` antes de aplicá-la no Raspberry.
- Depois de publicar, testar foto/vídeo em uma conversa da própria empresa, horário/ordem e uma nova resposta do aplicativo; verificar saúde de backend, worker e frontend.
