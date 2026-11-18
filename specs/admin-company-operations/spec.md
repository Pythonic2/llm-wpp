# Operações manuais de empresas no Django Admin

Status: implementação local. Risco alto por controlar acesso e cobrança. Sem migração real, exclusão de dados ou chamada ao Asaas nesta etapa.

## Contexto e decisão

- O Django bloqueia a exclusão de `Company` porque vendas e checkouts têm relações `PROTECT`.
- O usuário escolheu arquivar a empresa e preservar o histórico financeiro. Arquivar não cancela automaticamente checkouts ou assinaturas no Asaas; a operação externa deve ser feita separadamente.
- `Company.billing_status` e checkouts continuam a representar o estado de cobrança. O teste temporário concede acesso próprio, sem criar pagamento fictício.

## Atores e regras

- Somente superusuário pode conceder ou encerrar teste, arquivar ou restaurar empresa pelo Admin.
- Teste dura exatamente 7 ou 14 dias desde a concessão. Nova concessão reinicia o prazo; não acumula dias. Expira por comparação de data/hora, sem tarefa agendada.
- Empresa arquivada não aparece para usuários comuns nem pode usar CRM, IA ou envio de WhatsApp, mesmo com pagamento ou teste vigente. Superusuário mantém acesso de administração.
- Teste não altera a situação do checkout nem confirma e-mail. Uma conta inativa ainda precisa ser ativada no Admin antes do login.
- A tela de criação no Admin sugere `pending`. Conceder teste a empresa `legacy` a converte para `pending`, para que o prazo seja efetivo. Empresas `legacy` sem teste mantêm as regras existentes.
- Ações do Admin registram a mudança no histórico do Django. Checkouts/eventos financeiros ficam visíveis para inspeção, sem edição manual de status de pagamento.

## Cenários e critérios de aceitação

1. Dada empresa pendente e usuário ativo com vínculo, quando superusuário concede 7 ou 14 dias, a empresa aparece para o usuário, APIs e envio permitem acesso até o instante de vencimento. Verificar por teste de serviço e Admin.
2. Dado teste vencido, quando o acesso é consultado, a empresa deixa de aparecer e APIs/envio são bloqueados. Verificar com tempo controlado.
3. Dada empresa com vendas e checkouts, quando superusuário arquiva, os registros persistem e a empresa perde acesso. Restaurar reavalia cobrança/teste. Verificar por teste com relações protegidas.
4. Dado usuário não superusuário, ações de concessão/arquivamento não ficam disponíveis nem podem ser executadas por POST. Verificar no Admin HTTP.
5. Dado checkout pago ou em análise, ações manuais não alteram seu status nem acionam o Asaas. Verificar em teste e revisão do diff.

## Falhas e recuperação

- Arquivar não encerra cobranças externas. O Admin avisa para conferir o Asaas, inclusive antes de restaurar.
- Restaurar empresa não concede pagamento ou teste; se ambos expiraram, continua inacessível.
- Migração adiciona apenas campos opcionais; rollback de código requer preservar campos até remover a funcionalidade.

## Uso no Admin

1. Crie a empresa (estado inicial sugerido: `pending`), crie ou escolha um usuário ativo e vincule-o como `owner` em **Memberships**.
2. Em **Companys**, selecione a empresa e escolha **Conceder teste por 7 dias** ou **14 dias**. A coluna `teste até` mostra o vencimento. Se o usuário estiver inativo, ative-o separadamente em **Usuários** depois de conferir sua identidade.
3. Para retirar acesso preservando dados, escolha **Arquivar empresas selecionadas**. Consulte vendas e checkouts no Admin e trate assinaturas externas no Asaas. **Restaurar** reavalia o acesso; não cria pagamento nem novo teste.
