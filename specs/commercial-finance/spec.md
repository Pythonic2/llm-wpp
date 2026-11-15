# CRM comercial, catálogo e indicadores financeiros

Status: proposta de implementação. Amplia `specs/sales-tracking/`; requisitos mais recentes deste documento prevalecem, especialmente venda manual sem conversa.

## Escopo e fatos

- Reutilizar `Company`, `Membership`, `Contact`, `Conversation`, `Order`, `Message`, `MediaAsset`, base de conhecimento, sessão/CSRF e dashboard Django/React existentes.
- Não há modelos de produto, serviço, catálogo, venda, pagamento ou taxa. `Order.items` é JSON gerado pela análise da IA; `MediaAsset` é arquivo, não item comercial. Qualificação e oportunidade ficam em `Conversation`, sem modelo `Lead` separado.
- `Contact.wa_id` hoje é obrigatório e único por empresa: para venda sem WhatsApp, permitir contato manual sem `wa_id` e manter unicidade apenas dos IDs não vazios.
- `mark-won` e `Order.approved` não registram venda nem pagamento. Não inferir vendas antigas desses estados.

## Regras de produto

1. `Company.business_profile` é opcional (`products`, `services`, `both` ou vazio), editável por admin/owner e usado só para rótulos e sugestões. Nunca bloqueia o outro tipo.
2. Catálogo único por empresa: categoria e item de tipo produto/serviço; nome, descrição, unidade, modo de preço (`fixed`, `from`, `quote`), preço opcional conforme modo, status, custo opcional, SKU, preço promocional, disponibilidade, duração, variações e observações internas. Mídias existentes podem ser vinculadas ao item; nenhuma regra de estoque é exigida.
3. Venda (`Sale`) pertence à empresa e a um contato, pode vincular conversa e pedido, e tem linhas (`SaleLine`) de produto/serviço do catálogo ou item avulso. Guardar quantidade, nome/tipo/categoria, preço praticado, desconto e custo opcional como snapshots. Valor negociado prevalece sobre preço de catálogo. Total é calculado no servidor com `Decimal`, nunca aceito cegamente do navegador.
4. Oportunidade ganha, pedido aprovado, venda confirmada e pagamento recebido são estados distintos. Somente admin/owner confirma/cancela venda ou registra/reembolsa recebimento na primeira versão; atendentes continuam podendo operar o fluxo de oportunidades existente. `mark-won` oferece atalho para formulário, sem criar venda.
5. Pagamento registrado (`SalePayment`) representa lançamento manual de recebimento ou reembolso, não executa cobrança. Permitir pagamentos parciais; impedir reembolso acima do recebido e pagamento líquido acima do total da venda, salvo decisão futura. Cancelamento de venda não apaga recebimentos nem cria reembolso automático.
6. Taxas por meio de pagamento e impostos estimados são opcionais. `NULL` significa desconhecido; `0` só quando informado explicitamente. Snapshots de custo, taxa e imposto aplicados ficam na venda/pagamento. Sem dados, mostrar venda, valor e ticket, mas não lucro. Com dados parciais, rotular resultado parcial; só calcular resultado estimado completo se todos os componentes relevantes forem informados ou declarados não aplicáveis.
7. Assistência da IA exige evidência de resposta da IA enviada na mesma conversa antes da venda, dentro de janela definida (proposta: 30 dias). Guardar evidência, regra/versionamento e decisão; permitir correção auditada por admin. Venda sem conversa é `external`; com conversa sem resposta IA elegível é `human` se houve atendimento humano, ou `unknown` se não houver evidência suficiente. Participação não significa causalidade ou confirmação.
8. Criar vendas idempotentes: chave de requisição única por empresa; pedido opcional com no máximo uma venda ativa; referência externa única quando presente. Não bloquear duas vendas genuínas do mesmo contato. Cancelar por evento auditado, sem `DELETE` físico.
9. IA pode consultar somente campos comerciais públicos de itens ativos e autorizados da empresa. Não disponibilizar custo, margem, taxa, imposto ou notas internas no prompt. Preço sob orçamento e disponibilidade desconhecida devem ser ditos como tais, sem inventar valor ou estoque. IA não altera finanças nem confirma pagamento/pedido/venda.

## Indicadores e relatórios

- Por empresa e período: vendas confirmadas ativas, valor das vendas, ticket médio, recebido líquido registrado, vendas/valor assistidos pela IA, cancelamentos; agregados por dia/semana/mês, item, tipo, categoria, atendente e meio de pagamento.
- Custos, taxas e imposto estimados só aparecem quando conhecidos; resultado completo não será chamado de lucro líquido real. Identificar cobertura dos dados financeiros em cada agregado.
- Taxa de conversão usa coorte definida e denominador distinto. Proposta inicial: conversas com primeiro atendimento **ao vivo** no período e venda confirmada em até 30 dias / conversas com primeiro atendimento ao vivo no período. Cada conversa entra uma vez; várias vendas contam separadamente nos outros indicadores. Excluir histórico importado e sinalizar coortes ainda em andamento.
- CSV com filtros e valores reais, isolamento por empresa e proteção contra fórmulas em planilhas. PDF fica para etapa futura.

## Cenários de aceitação

1. Empresas de produtos, serviços e mistas usam catálogo/vendas; nenhuma precisa de estoque, custo, taxa ou imposto.
2. Contato manual sem WhatsApp pode comprar; contato/conversa/pedido de outra empresa não pode ser vinculado.
3. Uma venda pode conter produto e serviço, com preço negociado, desconto e snapshots preservados após edição do catálogo/configuração.
4. Pedido aprovado e oportunidade ganha não geram venda; IA não confirma nem altera venda/pagamento.
5. Pagamento parcial, novo recebimento, reembolso parcial e cancelamento mantêm saldos e auditoria corretos.
6. Dados financeiros ausentes não viram zero e não produzem lucro fictício; dados parciais têm aviso explícito.
7. Resposta de IA falha, posterior ou fora da janela não gera assistência; correção autorizada deixa trilha de auditoria.
8. Retry/concorrência não duplicam venda, recebimento ou relatório; múltiplas vendas do mesmo contato funcionam.
9. Dashboard e CSV conferem com linhas de venda/pagamento, filtros e timezone; estados vazios não inventam números.

## Decisões assumidas para desenvolver

- BRL na primeira versão; outra moeda exige indicadores separados.
- Confirmação de venda e recebimento são declarações manuais de admin/owner. Não há integração bancária, fiscal ou cobrança real.
- Descontos por linha e na venda são valores monetários não negativos. Arredondamento `ROUND_HALF_UP` a centavos nos totais e componentes.
- Comportamento antigo permanece: sem backfill automático de vendas ou atribuição da IA.
