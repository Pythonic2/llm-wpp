# Revisão de segurança — follow-up automático

## Superfícies e limites

- A varredura Celery consulta somente empresas que habilitaram a automação. O padrão da migration `0019` é `false`, e contatos existentes começam sem consentimento.
- A concessão ou revogação do consentimento exige sessão de administrador da empresa, registra data e origem no servidor, e não aceita `true` sem origem. A listagem e o resumo usam o filtro de empresa da sessão.
- O worker revalida empresa, conversa, oportunidade, pausa humana, consentimento, número e assinatura, idade da última mensagem recebida e horário local antes de enviar. Texto livre só é permitido entre 20 e 23 horas da última mensagem do cliente, deixando margem para a janela Meta de 24 horas.
- Há limite de 20 tentativas aceitas/em andamento por dia por empresa, sob lock da empresa, e um envio por contato a cada 7 dias. A restrição única `(conversation, trigger_message)` impede repetição do mesmo ciclo. Falhas não são reenviadas automaticamente.
- O webhook marca frases de descadastro como `opted_out` e não agenda resposta da IA para esse texto. Uma nova mensagem do cliente durante a geração cancela o envio.
- A IA recebe só o histórico da conversa e dados da mesma empresa, tem instrução para ignorar comandos no texto do cliente e é impedida de afirmar algumas confirmações comerciais. O texto final é limitado a 350 caracteres; falha de geração bloqueia o envio.

## Riscos residuais e recuperação

- A Meta não fornece idempotência para POST de mensagens. Após a reserva local, uma interrupção pode deixar resultado incerto. O sistema marca a tentativa como `unknown` após 15 minutos e não tenta novamente; o operador confere o WhatsApp antes de qualquer ação manual. Isso evita duplicação automática, mas a contagem pode subestimar envios incertos.
- Entre a última revalidação e a chamada Meta, uma nova mensagem ainda pode chegar à plataforma. A janela é curta, mas não há transação distribuída com a Meta. O webhook e os testes cobrem respostas recebidas antes da última revalidação.
- O campo de origem de consentimento é uma declaração do administrador, sem prova anexada. A empresa deve guardar a evidência da autorização e atender pedidos de descadastro não reconhecidos pela lista curta de frases.
- A migração `0019` é aditiva. Desativar `warm_follow_up_enabled` interrompe novos envios. Para rollback do app, retornar a imagem anterior após desativar a automação; manter a tabela de auditoria para preservar histórico.
- Nenhum envio real foi executado em desenvolvimento ou teste. Ativar em produção e aplicar `0019` exige autorização específica; `0018` pendente é pré-requisito.

## Testes

- Consentimento, isolamento de empresa, janela e horário, idempotência, resposta concorrente, limite diário, descadastro, resumo e frontend.
