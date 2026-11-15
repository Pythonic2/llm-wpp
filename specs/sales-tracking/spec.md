# Acompanhamento de vendas — proposta

Status: especificação para avaliação; nenhum código de aplicação foi alterado.

## Objetivo

Permitir que cada empresa registre vendas confirmadas com valor, data/hora, responsável, contato, conversa e pedido opcional. Medir separadamente a participação anterior da IA, sem usar a IA, a aprovação do pedido ou `mark-won` como confirmação de venda. Exibir indicadores auditáveis no dashboard.

## Fatos observados

- `Contact` pertence a `Company` e pode ter várias `Conversation`. Não existe entidade `Lead`: qualificação (`lead_temperature`, `purchase_intent_score`, `lead_stage`, `lead_data`) e oportunidade (`open/won/lost`) ficam em `Conversation`.
- Há no máximo uma conversa por empresa, número WhatsApp e contato; a mesma conversa pode durar muito tempo. `mark-won` apenas muda `opportunity_status` e limpa o follow-up, sem valor, data de fechamento ou evento de auditoria.
- `Order` já aponta para conversa e, opcionalmente, mensagem de origem; aprovação humana registra autor, data e evento. `approved` confirma o pedido no CRM, mas não prova pagamento ou venda realizada.
- `Message` distingue respostas da IA por `response_to` e `ai_status`; o envio bem-sucedido marca `SENT`, mas ainda não guarda o instante exato do envio em campo próprio. O dashboard atual conta conversas atendidas pela IA e leads/follow-ups, sem vendas ou receita.

## Regras propostas para a primeira versão

1. Criar uma entidade `Sale` por venda, com UUID, empresa, contato, conversa, pedido opcional, valor `Decimal` positivo em BRL, data/hora da venda informada pelo usuário (`sold_at`, sem data futura), responsável, autor e instante da confirmação no servidor, status `confirmed/cancelled`, referência externa opcional, chave e impressão do payload de idempotência e evidência de assistência da IA. Separar responsável pela venda de usuário que a registrou. Preservar o nome do responsável como snapshot caso a conta seja removida.
2. A conversa e o contato são obrigatórios e devem pertencer à empresa autenticada; `conversation.contact == sale.contact`. Se houver pedido, ele deve pertencer à mesma conversa/empresa e estar aprovado. Nenhuma venda é criada automaticamente pela aprovação do pedido, pela análise da IA ou por `mark-won`.
3. Apenas owner/admin confirma ou cancela vendas na versão inicial. O responsável pode ser qualquer membro válido da empresa. Os valores financeiros são visíveis apenas para owner/admin, inclusive nos novos cartões do dashboard, até existir decisão explícita sobre visibilidade de agentes. Usar autenticação por sessão e CSRF existentes.
4. `mark-won` continua como sinal de oportunidade ganha. A UI oferece a ação separada **Registrar venda confirmada**, com campos e confirmação humana. Registros antigos `won` e pedidos `approved` não viram vendas automaticamente. Confirmar uma venda pode sugerir marcar a oportunidade como ganha, sem exigir nem executar isso silenciosamente.
5. Assistência da IA é calculada no servidor no momento da confirmação, nunca enviada como booleano confiável pelo navegador. Evidência mínima: mensagem de saída da mesma conversa, gerada como resposta da IA (`response_to`), enviada com sucesso antes de `sold_at` e dentro de uma janela definida (recomendação inicial: 30 dias). Guardar `ai_assisted`, ID da mensagem de evidência e versão da regra. Análise interna de lead, resposta falha e resposta posterior à venda não contam. O rótulo é **assistida pela IA**, sem atribuir causalidade ou confirmação à IA.
6. Acrescentar timestamp explícito de envio das respostas da IA para novas mensagens. Para histórico anterior sem esse dado, não atribuir automaticamente quando a ordem temporal não puder ser comprovada; exibir `não verificado` em vez de presumir assistência.
7. Cancelamento exige motivo, usuário e data/hora; manter a venda e evento de auditoria, sem exclusão física. Correção de valor/data é cancelamento seguido de novo registro vinculado ao anterior. Cancelar não reabre automaticamente a oportunidade nem muda pedido.
8. `Idempotency-Key` obrigatório no registro: repetição com o mesmo payload retorna a mesma venda; mesma chave com payload diferente retorna conflito. Um índice único parcial permite somente uma venda **ativa** por pedido; venda cancelada pode ser substituída com nova chave. Referência externa, quando informada, é única por empresa e origem. Vendas sem pedido podem se repetir para o mesmo contato; coincidência de contato/valor/data gera aviso, não bloqueio automático.

## Indicadores propostos

Todos os agregados são calculados no backend por empresa e período na timezone da empresa; valores monetários são `Decimal`/string na API, sem `float`.

| Indicador | Definição inicial |
| --- | --- |
| Vendas confirmadas | Quantidade de `Sale` atualmente `confirmed` com `sold_at` no período. Inclui várias vendas do mesmo contato. |
| Faturamento registrado | Soma dos valores dessas vendas; rótulo deve explicar que é valor comercial registrado, não pagamento recebido nem faturamento fiscal. |
| Vendas assistidas pela IA | Subconjunto das vendas confirmadas com evidência de participação anterior da IA. |
| Receita associada à IA | Soma dos valores do subconjunto acima; associação, não receita causada pela IA. |
| Taxa de conversão | Proposta: conversas com primeiro atendimento elegível no período que tiveram ao menos uma venda confirmada em até 30 dias, divididas pelas conversas com primeiro atendimento elegível no período. Cada conversa conta uma vez, mesmo com várias vendas. A coorte ainda em andamento deve ser identificada. |

Mostrar também cancelamentos e valor cancelado para explicar mudanças nos totais. Ao cancelar, a venda sai das métricas de vendas ativas do seu período original; o evento de cancelamento permanece auditável. Registrar `first_eligible_inbound_at` para a coorte de conversão, excluindo importação histórica; não inventar datas para conversas antigas. Se o produto quiser **conversão de leads qualificados** em vez de conversas atendidas, será preciso definir qualificação e gravar o primeiro evento de qualificação, pois `analyzed_at` hoje é sobrescrito.

## Cenários e critérios verificáveis

1. Dado admin de uma empresa, quando confirma venda válida com contato/conversa e pedido opcional coerentes, então uma venda única é gravada com autor, responsável, valor e datas.
2. Dado usuário sem papel ou vínculo suficiente, ou IDs de outra empresa, quando tenta criar, ler ou cancelar venda, então a API não revela nem altera o registro.
3. Dado pedido aprovado, análise da IA ou oportunidade `won`, quando não há confirmação humana de venda, então indicadores de venda permanecem inalterados.
4. Dada resposta da IA enviada antes da venda na janela definida, então a venda guarda evidência e entra no subconjunto assistido; se a resposta falhou, veio depois ou foi apenas análise interna, não entra.
5. Dadas duas vendas reais do mesmo contato, então ambas são registradas; duas tentativas concorrentes para o mesmo pedido ativo ou com a mesma chave de idempotência produzem apenas uma venda.
6. Dada venda cancelada, então motivo/ator/data ficam auditáveis e os indicadores ativos excluem seu valor, sem apagar pedido, conversa ou histórico.
7. Dado filtro de período e empresa, então contagem, soma e taxa seguem as definições acima, com zero/divisão por zero tratados e sem vazamento entre empresas.

## Decisões a confirmar antes da implementação

- O que a empresa considera **venda confirmada**: acordo comercial, pagamento recebido ou outro evento? A primeira versão propõe declaração manual por admin; não verifica pagamento.
- BRL apenas no início? Se houver outras moedas, indicadores devem ser separados por moeda, nunca somados sem conversão explícita.
- Agentes podem registrar propostas para confirmação por admin, ou a criação fica restrita a admin/owner como proposto?
- A taxa de conversão deve usar conversas atendidas ou um critério de lead qualificado? A janela recomendada de 30 dias é adequada ao ciclo de vendas dos clientes?
- Uma venda por pedido ativo atende ao negócio, ou há vendas parciais que exigem mais de uma venda vinculada ao mesmo pedido?
