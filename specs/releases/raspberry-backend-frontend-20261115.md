# Publicação conjunta no Raspberry — 15/11/2026

## Objetivo e estado inicial

Publicar o backend versionado que contém a correção de cadastro de funcionários e manter o frontend com o scroll de chat e as telas já publicadas. A chave Fernet do WhatsApp deve continuar no arquivo privado montado em `/app/.env`. Asaas permanece no sandbox. Não há Cloud Run neste CRM.

Antes da publicação, o backend, worker e agendador executavam `sha-51c9e4b5111ab03b4c04bcdaeee42a56e87869cc`; o frontend já executava `sha-72af0fae2e9294b25deadd4d69242214e32971f2`. A migration `saas.0016` já está aplicada. O arquivo GitOps do backend ainda permitia que a variável `APP_IMAGE_TAG` do Portainer escolhesse a imagem antiga.

## Critérios e plano

1. Fixar no Compose do backend a imagem ARM64 imutável `sha-9572d188f38c6b57243951cbafb744908c49da51`, publicada com sucesso no GitHub Actions, para backend/worker/agendador. Preservar a montagem privada da chave e as demais variáveis.
2. Confirmar que o Compose do frontend e a imagem em execução permanecem na revisão `72af0fae2e9294b25deadd4d69242214e32971f2`.
3. Guardar o Compose anterior no Raspberry; validar `docker compose config` e `manage.py check` com a nova imagem antes de recriar serviços.
4. Enviar o Compose ao GitHub, aplicar as stacks via GitOps e confirmar que o Portainer guarda as mesmas imagens que os containers executam.
5. Verificar migrations, três processos backend e frontend, HTTP local/público, leitura da chave e descriptografia do token existente. Não enviar mensagem real nem alterar vínculos de usuários.

## Matriz de falhas e recuperação

| Falha | Verificação | Recuperação |
| --- | --- | --- |
| Imagem ARM64 indisponível ou incompatível | Pull e `manage.py check` antes do deploy | Não recriar containers; manter tag anterior |
| Redis/Neon indisponível durante reinício | Estado de backend/worker/agendador e HTTP da API | Reaplicar Compose anterior e investigar conexão; não reverter migration 0016 |
| Chave WhatsApp ausente após GitOps | Montagem read-only e abertura de token existente sem imprimi-lo | Restaurar montagem/arquivo privado original; não gerar chave nova |
| Portainer reverter tags por configuração antiga | Comparar Compose em cache e imagem em execução | Acionar webhook GitOps da revisão versionada; se necessário, restaurar Compose anterior |
| Interface indisponível | HTTP local/público e imagem OCI do frontend | Reaplicar Compose frontend anterior salvo em `/home/pi/gesttech-scroll-20261115/backup` |

## Limites

O vínculo da conta `xico` continua dependendo da escolha da empresa pelo proprietário. A nova versão impede cadastro de funcionário em empresa com cobrança indisponível, mas não altera o vínculo anterior nem libera uma assinatura pendente. Mensagens manuais que falharam antes da correção da chave permanecem como `failed` e não são reenviadas automaticamente.
