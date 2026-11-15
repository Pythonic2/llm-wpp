# Convites de equipe — 15/11/2026

Risco alto: autenticação, acesso multiempresa e envio de e-mail.

## Objetivo

Permitir que proprietário ou administrador convide um funcionário novo pelo e-mail, sem exigir que ele pague uma assinatura própria. Manter a inclusão imediata de contas existentes.

## Cenários e critérios de aceitação

- E-mail de conta existente: a associação à empresa é criada com o papel solicitado.
- E-mail novo: cria conta inativa com senha inutilizável, associa à empresa e envia link assinado válido por 48 horas. A conta não acessa o CRM antes de definir a senha.
- Link válido: valida a senha, ativa a conta e permite login; link repetido, expirado, adulterado ou de associação removida é recusado.
- Apenas proprietário e administrador da empresa podem convidar. O papel `owner` continua reservado ao superusuário.
- Convite para membro pendente pode ser reenviado; duplicidade de membro ativo é recusada. Falha de SMTP fica registrada no servidor e não expõe detalhes ao cliente.
- O formulário informa que novos usuários precisam de e-mail. Nome de usuário continua aceito somente para contas existentes.
- O aviso de risco no WhatsApp tem checkbox e texto separados, com tamanho de leitura adequado.

## Plano e testes

1. Implementar token assinado e endpoint público de aceitação, com limite de tentativas, validação de senha e checagem de associação.
2. Ajustar endpoint de membros e interface de equipe, convite e aviso do WhatsApp.
3. Testar autorização entre empresas, conta inativa, aceitação e replay; rodar suíte backend, frontend, lint/build e smoke no Raspberry.

## Implantação e recuperação

Sem migration de banco. Construir novas imagens ARM64, trocar backend/worker/frontend com backup dos arquivos Compose. Em falha, retornar às imagens anteriores; convites já criados permanecem inativos e podem ser reenviados.

## Revisão de segurança

- Autorização: o servidor exige administrador da empresa para convidar; teste de acesso cruzado e teste de atendente sem permissão passaram. Papel `owner` segue restrito ao superusuário.
- Conta nova: nasce inativa e sem senha utilizável. Token assinado inclui usuário, e-mail, associação e empresa; expira em 48 horas. Associação removida, token adulterado/expirado e reutilização após ativação são rejeitados por testes.
- Abuso: limites de convites e aceitação usam cache Redis compartilhado entre workers na implantação. O cache foi testado entre processos do backend e worker.
- Entrada/segredos: e-mail validado, senha avaliada pelos validadores do Django; nenhum token ou senha é registrado em logs do aplicativo. SMTP recebe somente o e-mail do funcionário convidado.
- Risco residual baixo: se o SMTP falhar depois da transação, o membro permanece inativo. A falha é registrada no servidor e o administrador pode reenviar o convite pelo mesmo e-mail.
