# Follow-up automático de conversas mornas

## Estado observado

- `analyze_conversation` classifica `warm`, agenda `next_follow_up_at` 3 dias após `last_message_at` e o dashboard mostra pendências; não há envio automático nem registro de tentativas.
- O agendador Celery Beat existe para cobrança; `send_whatsapp_text` envia texto pela Cloud API e o webhook atualiza status.
- O CRM já possui empresa, contato, conversa, número, status de oportunidade, pausa humana e notificações de lead quente para um telefone configurado.

## Objetivo

Permitir que empresas optem por retomar automaticamente, com IA, contatos de conversas mornas elegíveis e mostrem ao usuário quantas pessoas foram contatadas, com resultado auditável.

## Decisões do proprietário

- Primeira versão: tentativa até 20 horas após a última mensagem do cliente, dentro da janela de 24 horas.
- Enviar somente entre 9h e 18h no horário de Brasília; se a janela fechar fora desse horário, não enviar.
- Resumo no painel do CRM, com lista e resultado; sem mensagem ao telefone do proprietário.

## Regras comuns propostas

- Recurso desligado por padrão por empresa; administrador habilita depois de configurar consentimento de retorno por contato, com data e origem, e de escolher número ativo.
- Só considerar conversas `warm`, oportunidade aberta, não encerradas, sem IA pausada e sem atendimento humano em andamento. Contato com descadastro ou sem consentimento explícito é excluído.
- Usar a hora da última mensagem **recebida do cliente** para janela Meta e inatividade; revalidar imediatamente antes de enviar para evitar corrida com nova resposta, fechamento, cancelamento de consentimento ou pausa humana.
- Um envio automático por ciclo de inatividade e no máximo um por contato em 7 dias, com limite diário por empresa; sem rajadas de mensagens ao ativar o recurso.
- Gerar texto curto e contextual pela IA, com opção clara de atendimento humano; nunca prometer preço, desconto ou estoque não confirmados. Falha de IA não gera mensagem genérica não revisada.
- Registrar tentativa, horário, texto enviado, ID externo, estado `enviado/entregue/falhou/incerto`, resposta e descadastro, sempre isolado por empresa. A contagem do resumo é de contatos distintos com mensagem aceita pela API Meta; entregas, respostas, descadastros e falhas aparecem separadamente.
- Resposta do cliente cancela agendamento anterior e volta ao atendimento normal. Operador pode desativar por conversa e empresa.
- Não disparar mensagens reais em testes, desenvolvimento ou migração; ativação em produção exige autorização específica para envio automático.

## Cenários e aceite

1. Dado lead morno elegível, consentido e dentro da política Meta, quando vence o prazo configurado, uma única tentativa é criada e uma única mensagem é aceita pela Meta. Teste de concorrência/idempotência.
2. Dado lead sem consentimento, pausado, encerrado, ganho/perdido, com número indisponível ou resposta recente, nenhum envio ocorre. Teste de matriz de exclusão.
3. Dada janela de 23 horas expirada ou horário fora de 9h–18h, texto livre nunca é enviado nesta versão. Teste de bloqueio.
4. Dado envio aceito, o CRM mostra contagem distinta de contatos alcançados hoje; entregas, respostas, descadastros e erros são contados à parte. Teste de API e interface.
5. Dado usuário de outra empresa, ele não vê tentativas nem altera configurações. Teste de isolamento e permissão.
6. Dado contato que responde antes de o worker executar, a tentativa é cancelada sem mensagem. Teste de corrida.

## Limites

A regra de 24 horas e de template vem da [Política de Mensagens do WhatsApp Business](https://business.whatsapp.com/policy/preview?lang=pt_BR). A política também exige consentimento e respeito ao descadastro. Não há média universal comprovada para o melhor horário de follow-up; 20 horas é uma hipótese de produto a validar com taxa de resposta e reclamações.
