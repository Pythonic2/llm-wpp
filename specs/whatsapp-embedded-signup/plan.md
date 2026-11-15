# Plano

1. Endurecer o fluxo de autorização Django: estado transitório único, validação dos IDs/escopos pela Meta, armazenamento de credencial, status e endpoints administrativos.
2. Processar eventos de coexistência no webhook sem acionar IA; iniciar sincronização autorizada e registrar estado para recuperação dentro do prazo da Meta.
3. Ajustar o frontend React para iniciar o popup a partir do clique, aceitar eventos parciais, exibir status e erros claros, e oferecer desconexão local segura.
4. Testar com mocks e revisar isolamento, replay, assinatura, segredos e regressões dos números existentes.

## Decisões

- Persistir estado transitório em PostgreSQL para funcionar entre workers e impedir replay. Não usar cache em processo.
- Não confiar no `phone_number_id` do navegador sem confirmar que pertence à WABA e está acessível pelo token recebido.
- Para coexistência, confirmar `is_on_biz_app=true` e não chamar `register`.
- Manter a configuração V4 existente e expor seus parâmetros públicos pelo endpoint atual.

## Dependências e reversão

- Nova migration aditiva. Reversão da aplicação: desativar a UI e retornar ao endpoint anterior apenas após verificar os tokens e estados conectados; não apagar números ou mensagens. A migration `saas.0013` foi aplicada no Raspberry em 15/11/2026 após autorização específica do usuário; ver `raspberry-deploy.md`.
- Requer configuração dos campos de webhook no App Dashboard e variáveis de ambiente existentes. Nenhuma alteração externa é feita aqui.
