# Revisão de segurança: funcionários

- **Autorização:** cada empresa enviada é verificada no servidor para papel de administrador ou proprietário e disponibilidade de cobrança antes da transação de criação. Testes cobrem acesso cruzado e ausência de conta parcial.
- **Correção após uso real:** o superusuário podia selecionar uma empresa com cobrança pendente por causa do bypass administrativo em `_company_for_request`. A criação de funcionário agora valida disponibilidade de cobrança separadamente para todos os papéis; conta com vínculo antigo continua sem acesso até pagamento ou atribuição a empresa disponível.
- **Credenciais:** a senha inicial é definida pelo administrador, armazenada com hash pelo Django, omitida das respostas e listagens e exigida para troca no primeiro acesso. Login possui limite de taxa. Senhas simples ainda criam risco de tomada da conta antes do primeiro acesso; orientar o administrador a escolher uma senha temporária não óbvia.
- **Bloqueio:** middleware recusa APIs de dados até a troca; somente sessão, CSRF, logout e troca de senha ficam disponíveis. O endpoint exige a senha inicial e valida a nova senha; a sessão é preservada com `update_session_auth_hash`.
- **E-mail:** só é enviado quando informado e há configuração SMTP. O texto contém credenciais iniciais, conforme solicitado; isso implica que elas permanecerão na caixa de e-mail do destinatário. Falha de envio é registrada apenas pelo tipo de erro, sem senha. A conta permanece criada, e o administrador pode entregar a senha diretamente.
- **Duplicidade e concorrência:** usuário/e-mail são conferidos sem diferenciar maiúsculas; no PostgreSQL, locks de transação por identidade serializam cadastros concorrentes. A conta e os vínculos são criados na mesma transação.

Verificação executada em PostgreSQL local descartável: 115 testes backend passaram; 27 testes frontend, lint e build passaram. Nenhum teste ou migration foi executado no banco de produção.
