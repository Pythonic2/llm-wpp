# Administração manual, confirmações e publicação Raspberry

Classificação: alto risco. Pedido de 17/11/2026 autoriza publicação no Raspberry após validação. Correção final do usuário: preços editados valem apenas para novas contratações; assinaturas existentes preservam o valor contratado nas renovações.

## Objetivo e critérios

1. Substituir confirmações nativas de exclusão/desconexão no CRM por modal consistente com o de remoção de membro, contendo nome do alvo, efeito claro, cancelar e ação destacada. Cancelar não chama API.
2. Superusuário pode editar no Django Admin os preços mensais da primeira empresa, empresa adicional e número adicional, por versão do plano. A mudança exige motivo e gera trilha de auditoria. Staff sem superuser não pode editar.
3. Novos checkouts usam os preços da versão de plano da empresa. Checkouts anteriores mantêm o valor original como registro histórico. Nenhuma assinatura Asaas existente é atualizada quando o preço do plano muda.
4. Proteger status de pagamento, IDs e histórico de eventos de edição direta. Ajustes nos preços não podem simular pagamento nem alterar renovações contratadas.
5. Regras anteriores de arquivamento e teste de 7/14 dias continuam válidas; exclusão de empresa preserva histórico.
6. Antes de publicar: testes, build, lint, revisão de segurança, backup e ensaio das migrations 0020/0021/0022 em PostgreSQL isolado. Publicar imagens ARM64 no Raspberry e verificar versão efetiva, saúde, migrações e HTTP local/público. Recuperação por imagens anteriores, sem apagar dados.

## Matriz de falhas

- Cancelamento do modal: nenhuma mutação.
- Erro de API: o usuário vê a falha; modal pode ser reaberto.
- Correção financeira inválida: formulário rejeitado antes de salvar.
- Edição por staff não superuser: 403.
- Falha de migration ou container: preservar backup e restaurar versão anterior compatível, sem rollback destrutivo de dados.
