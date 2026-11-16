# Plano

1. Prazo e canal definidos: 20 horas, 9h–18h em Brasília, painel. Confirmado que não há template aprovado; não usar template nesta versão.
2. Criar campos de configuração por empresa e consentimento/descadastro por contato; registrar tentativa em tabela com restrição de idempotência e índices por vencimento.
3. Ajustar agendamento para usar última mensagem recebida do cliente. Criar varredura Celery Beat em lotes limitados e task por conversa com revalidação sob bloqueio transacional.
4. Gerar mensagem contextual curta e enviar texto livre somente na janela de 20 a 23 horas após a última mensagem do cliente. Fora da janela, não enviar. Persistir estado e ID externo; atualizar entrega e resposta pelo webhook.
5. Expor configuração, consentimento, fila, histórico e resumo no CRM. Começar desativado; incluir controles de pausa e prévia de mensagem.
6. Testes de matriz, concorrência, isolamento, janela de 24 horas, limites, dashboard e erros Meta. Revisão de segurança e plano de recuperação antes de qualquer publicação.
