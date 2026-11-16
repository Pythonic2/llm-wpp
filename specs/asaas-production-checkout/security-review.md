# Revisão de segurança

## Limites de confiança

O formulário público fornece CPF, endereço e senha; o Asaas recebe dados de cobrança; o webhook informa pagamentos; apenas o backend concede acesso. Um callback de navegador não confirma pagamento.

## Achados e medidas

- **Alto — ambiente misturado:** chave de produção salva no Portainer, mas container sem chave e URL sandbox. Um ID de checkout sandbox era aberto no site de produção. A mudança valida prefixo da chave/URL, registra ambiente em cliente e checkout e gera link pelo ambiente da compra. Verificado em testes de cadastro e migração.
- **Alto — webhook ausente na conta de produção:** sem ele, um pagamento real não ativa o CRM. A implantação exige webhook de produção com token aleatório separado, URL final HTTPS, eventos de checkout, cobrança e assinatura; só ativar a fila após backend pronto. Validar API e resposta HTTP 200 com evento não financeiro de teste.
- **Alto — confusão entre eventos sandbox e produção:** a rotina anteriormente não marcava ambiente. Eventos de outro ambiente agora são rejeitados antes de alterar status. A chave de autenticação do webhook de produção fica em arquivo privado fora do Git. Teste cobre evento antigo após a troca.
- **Médio — checkout pago por referência isolada:** o processamento agora exige também o ID de checkout externo conhecido para `CHECKOUT_PAID`. O índice único de evento e o bloqueio transacional preservam idempotência.
- **Médio — cliente sandbox na produção:** o cadastro retomado exige dados de cobrança novamente e cria cliente da conta correta; não reaproveita ID antigo. Compra previamente paga requer análise humana.

## Riscos residuais

Um pagamento real só é confirmado de ponta a ponta após uma compra autorizada e efetiva; não foi feita cobrança de teste. Se o Asaas aceitar a compra mas não enviar o webhook, a concessão de acesso fica pendente e exige conciliação operacional. A tarefa periódica não ativa uma compra que nunca recebeu `CHECKOUT_PAID`.
