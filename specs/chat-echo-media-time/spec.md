# Conversas: ecos do aplicativo, horário original e download de mídias

## Evidências

O aplicativo Meta está inscrito no WABA, mas a assinatura de Webhooks do aplicativo contém somente `messages`; falta `smb_message_echoes`. O número em coexistência continua em `syncing` e não há ecos recentes. O CRM ordena por `created_at` (momento de gravação), apesar de receber `timestamp` da Meta. Vídeo recebido não recebe `source_media_id`; imagem é salva no servidor antes de ficar disponível para download. Há 228 mensagens, 219 sem `sent_at` apesar de `timestamp` no payload, e 11 imagens históricas já armazenadas.

## Objetivo

Exibir mensagens de aplicativo e de clientes na ordem e no horário originais. Permitir que um usuário autenticado baixe foto ou vídeo recebido diretamente para o computador, por um proxy do CRM que não persiste o arquivo novo no servidor. Trocar a confirmação nativa de remoção da equipe por modal acessível.

## Regras e cenários

- Dado `smb_message_echoes` inscrito na Meta, quando chega um eco válido do número em coexistência, gravar uma mensagem enviada na conversa do destinatário. Repetições do mesmo ID não criam outra mensagem nem pausam IA novamente.
- Dado um eco sem telefone mas com identificador de usuário, associar pelo identificador estável aceito no CRM. Se não houver destinatário, ignorar sem criar conversa cruzada.
- Dado `timestamp` Unix válido no webhook, gravar `sent_at` em UTC; exibir no fuso do navegador e ordenar por `sent_at` com desempate por ID. Se ausente/inválido, usar o momento de recebimento.
- Dado histórico antigo com `raw_payload.timestamp` válido e `sent_at` vazio, preencher `sent_at` em migração de dados aditiva. Preservar `created_at` e mensagens que já têm `sent_at`.
- Dada imagem ou vídeo recebido com ID de mídia, a listagem oferece download imediato; somente ao clicar, o backend busca a mídia na Meta, limita tamanho e entrega por resposta autenticada com cabeçalhos de anexo. Não salvar novas imagens/vídeos no storage do servidor.
- Dada mídia removida/expirada na Meta, mostrar erro de download; arquivos antigos que já estão armazenados permanecem acessíveis até uma limpeza separada e autorizada.
- Dado usuário de outra empresa ou não autenticado, não entregar mídia.
- Dado clique de remover membro, abrir modal de confirmação; cancelar mantém a equipe inalterada; confirmar chama a API e exibe erros.

## Aceite e verificação

1. Echo autenticado, idempotente e isolado por número/empresa: testes de webhook.
2. Horários de entrada, eco e histórico; ordenação/paginação estáveis: testes de API e migração.
3. Imagem/vídeo sem persistência nova no servidor, download autenticado, limitação de tamanho e falha segura: testes de API.
4. Modal com confirmação e cancelamento por botão/teclado: testes de interface.
5. Meta App Dashboard com `smb_message_echoes` inscrito e primeira entrega real observada após usuário salvar: verificação operacional.

## Limites

O servidor participa como proxy temporário da mídia para não expor token Meta ao navegador. A disponibilidade de arquivos antigos depende da retenção da Meta. Não apagar arquivos históricos neste incremento.
