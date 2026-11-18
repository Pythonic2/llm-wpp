# Business Units e limites comerciais

Status: implementação local; alto risco. Não autoriza migração de dados reais, deploy ou alteração de assinaturas existentes.

## Fatos e decisões

- `Company` é o tenant e a assinatura; `WhatsAppNumber` já comporta vários números por empresa.
- RAG, mídia, catálogo e instruções da IA hoje usam o escopo da empresa. Conversas são vinculadas a número; pedidos e vendas podem derivar delas.
- O adicional de número custa R$ 49,90 no código atual. O novo plano básico usa R$ 30,00 para novas empresas; empresas existentes preservam R$ 49,90 por plano versionado. Checkouts existentes retêm seu valor gravado.
- O plano básico inclui duas unidades e dois números. Uma unidade pode ter vários números. A terceira unidade é recusada; não há checkout de unidade.
- Unidade inativa continua contabilizada no limite. O operador pode reativá-la; exclusão de unidade com histórico é proibida.
- Clientes `legacy` preservam a capacidade de números ilimitada até migração comercial. O limite de duas unidades é novo e vale para empresas `legacy` também.
- Unidades são acessíveis aos membros da mesma empresa segundo papéis atuais; escopo de equipe por unidade fica para evolução comercial e não é inferido dos dados atuais.

## Cenários e critérios

1. Nova empresa recebe unidade padrão, inclusive via Admin; backfill cria uma por empresa existente e associa registros sem tocar em tokens/arquivos.
2. Administrador cria até duas unidades sob bloqueio transacional; a terceira é recusada mesmo se outra estiver inativa. Usuário de outra empresa não administra unidade alheia.
3. Números novos e Embedded Signup escolhem unidade da empresa; sem escolha usam padrão. Alterar unidade do número não altera conversas anteriores.
4. RAG, catálogo e mídia usados na resposta têm escopo da unidade da conversa. Documentos compartilhados explicitamente entre unidades da mesma empresa usam os embeddings existentes. Compartilhamento entre empresas é recusado.
5. Pedido herda unidade da conversa. Venda com conversa herda unidade; venda manual escolhe unidade quando há duas e usa a padrão quando há uma.
6. Dashboard e CSV suportam unidade ou consolidado, mantendo totais existentes. APIs e arquivos verificam empresa e unidade no servidor.
7. Quota de números deriva do plano versionado e dos adicionais pagos. Novo plano usa R$ 30,00; empresas existentes seguem R$ 49,90. Redução de limite não exclui dados.
8. Sinalização de uso indevido exige revisão humana, justificativa e auditoria; não bloqueia automaticamente.

## Falhas, segurança e recuperação

- `company_id` continua obrigatório em todas as consultas sensíveis. IDs de unidade são aceitos apenas após validação de pertencimento.
- O vínculo da conversa é histórico e não muda quando o número muda de unidade.
- A migração é aditiva e idempotente, com verificação de contagens; rollback de código após ativar múltiplas unidades requer versão que preserve o filtro do RAG ou suspensão temporária da IA nessas empresas.
- Nenhuma credencial Meta, arquivo físico, assinatura Asaas ou dado de produção será modificado nesta etapa.
