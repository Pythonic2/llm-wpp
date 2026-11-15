# Recuperação da chave WhatsApp no Raspberry — 15/11/2026

## Objetivo e diagnóstico

Restaurar o envio manual e pela IA para números conectados pelo Embedded Signup após um redeploy GitOps do Portainer. O token de pelo menos um número está criptografado; `saas.meta.access_token_for_number` exige a mesma chave Fernet usada na conexão. O arquivo privado de deploy no Raspberry contém a chave válida, mas a configuração persistida da stack GitOps não a injeta no container. O Compose atual injeta `WHATSAPP_TOKEN_ENCRYPTION_KEY` vazia, impedindo `load_dotenv` de carregar um valor de `/app/.env`.

## Cenários e critérios de aceitação

1. O arquivo privado no host contém somente `WHATSAPP_TOKEN_ENCRYPTION_KEY`, tem acesso restrito ao usuário `app` no container e não entra no Git ou na imagem.
2. Backend, worker e agendador montam o mesmo arquivo em `/app/.env` somente para leitura. A stack falha ao subir se o arquivo não existir, sem criar um diretório no lugar.
3. A configuração vazia da chave é retirada de `environment`, permitindo que `load_dotenv` carregue o arquivo. As outras variáveis permanecem como estavam.
4. O processo consegue abrir o token criptografado existente. Números manuais continuam usando `WHATSAPP_ACCESS_TOKEN` e não exigem a chave.
5. O envio manual e o processamento da IA podem obter a credencial correta. Nenhuma mensagem de teste é enviada a clientes reais.

## Plano e matriz de verificação

| Etapa | Verificação | Falha/recuperação |
| --- | --- | --- |
| Preparar arquivo privado no Raspberry | Formato Fernet válido; conteúdo limitado à variável; permissões `0400` para UID/GID do app | Preservar `stack.env` original; não trocar ou gerar chave |
| Atualizar Compose versionado | `docker compose config` contém montagem nos três serviços e não contém a variável vazia | Reverter somente o Compose se houver incompatibilidade |
| Publicar no GitHub e aplicar a stack GitOps | Confirmar imagem atual, montagem read-only e presença da chave sem mostrar valor | Reverter o commit do Compose; manter arquivo privado para recuperação |
| Validar funcionalidade | Abrir token existente em processo da stack, confirmar `/api/v1/auth/csrf/` e workers saudáveis | Voltar à versão anterior da stack e investigar antes de qualquer envio |

## Riscos e limites

- Perder ou substituir a chave torna os tokens criptografados ilegíveis. O arquivo original em `stack.env` fica preservado.
- O arquivo montado é legível pelo processo `app` de backend/worker/agendador, como necessário para envio. Não expor seu conteúdo em logs, `docker inspect` ou respostas.
- Esta correção não muda o banco nem o token global e não concede acesso a empresas pendentes.
- A tag da imagem mantida pelo Portainer continua sendo um assunto separado do bug de configuração; o conserto da chave deve funcionar com a imagem atual.

## Resultado da publicação

- Commit `e9373cf` enviado ao GitHub; o Portainer GitOps recriou backend, worker e agendador com a montagem `/app/.env` somente leitura. A imagem de backend permanece `sha-51c9e4b5111ab03b4c04bcdaeee42a56e87869cc`.
- O arquivo no host foi criado a partir da chave já presente no `stack.env` privado, sem rotação. Permissões verificadas: `0400`, UID 100 e GID 101. O teste isolado com a imagem atual confirmou leitura via `load_dotenv` e formato Fernet válido.
- No backend em produção, a consulta somente de leitura abriu o token criptografado existente (`1` número verificado); a consulta autenticada à Meta confirmou acesso a esse número. O token global dos números manuais também está configurado. Worker e agendador carregaram a mesma chave.
- Containers `running`, API local `/api/v1/auth/csrf/` e frontend local responderam HTTP 200. Nenhuma mensagem real foi enviada; a entrega ponta a ponta depende de uma próxima mensagem normal do usuário ou de teste com destinatário autorizado.
