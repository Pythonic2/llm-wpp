# Plano

1. Marcar ambiente nos clientes e checkouts Asaas, classificando registros legados como sandbox.
2. Corrigir geração/retorno dos links e bloquear configuração incoerente antes de chamadas externas.
3. Tratar retomada de cadastro pendente e impedir reuso de cliente sandbox na produção.
4. Isolar webhook, concessões de acesso e reconciliação por ambiente; adicionar testes de regressão.
5. Preparar imagens, backup e webhook autenticado de produção. Validar configuração ativa e domínio antes de publicar backend e frontend.
6. Após autorização explícita, ativar configuração de produção, publicar, verificar health e fluxo de checkout sem efetuar pagamento real de teste. Reverter imagens/configuração em caso de falha, preservando cobranças externas que exijam conciliação.
