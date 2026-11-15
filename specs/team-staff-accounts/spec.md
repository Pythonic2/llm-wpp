# Cadastro direto de funcionários

## Objetivo
O proprietário ou administrador cadastra um funcionário com usuário e senha inicial definidos por ele, e-mail opcional, papel (atendente ou administrador) e uma ou mais empresas às quais já tem acesso administrativo. O funcionário troca a senha no primeiro acesso antes de acessar dados do CRM.

## Regras e cenários
- O cadastro é atômico: se uma empresa for inválida ou não autorizada, nenhum usuário ou vínculo é criado.
- Empresas com cobrança pendente, expirada ou suspensa não podem ser atribuídas a novos funcionários, inclusive por superusuário. A seleção de empresas no frontend pode listar espaços administrativos ainda indisponíveis; o servidor rejeita o cadastro com mensagem clara.
- Nomes de usuário e e-mails são únicos sem diferenciar maiúsculas/minúsculas; conta existente não é sobrescrita.
- O e-mail é opcional. Quando presente e o SMTP configurado, a notificação lista as empresas e credenciais iniciais. Falha no SMTP não desfaz a conta e é registrada sem credenciais.
- A senha inicial pode ser simples, conforme definida pelo administrador; o login é limitado por taxa. Ela é guardada apenas com hash. A nova senha passa pelos validadores do Django e deve ser diferente da inicial.
- Até a troca, somente sessão, CSRF, logout e troca de senha são permitidos; endpoints de dados retornam 403.
- Convites antigos por e-mail continuam válidos.

## Critérios de aceitação e matriz de testes
1. Administrador cria funcionário em uma ou várias empresas autorizadas; os vínculos e papel estão corretos.
2. Atendente e administrador de outra empresa não conseguem conceder acesso; não há criação parcial.
3. Usuário e e-mail repetidos são rejeitados; e-mail inválido também.
4. Sem e-mail não há tentativa de envio; com e-mail há notificação. Erro de SMTP não revela senha em logs.
5. Login inicial sinaliza troca obrigatória e bloqueia dados; após trocar, a senha antiga falha e o acesso é liberado.
6. O frontend mostra campos claros, seleção de empresas e formulário de troca de senha.
7. Uma conta vinculada apenas a empresa indisponível recebe lista vazia, sem acesso a dados; a tela orienta a verificar a assinatura. O cadastro de novos funcionários impede esse vínculo.

## Impacto, falhas e recuperação
Migration aditiva `saas.0016` cria tabela de estado de senha inicial; não altera usuários existentes. Aplicar antes da nova versão da API. A reversão da aplicação usa a imagem anterior mantendo a tabela; remover a tabela apagaria a obrigação de troca e não é recomendado. Em falha do SMTP, o administrador entrega a senha inicial por canal próprio. Não registrar nem retornar a senha em listagens.

## Plano de publicação
1. Confirmar backup e versão atual do banco e da stack Raspberry.
2. Aplicar apenas `saas.0016` no banco do CRM; confirmar que nenhuma outra migration está pendente.
3. Publicar backend e frontend compatíveis na stack do Raspberry.
4. Testar cadastro sem e-mail, acesso limitado às empresas escolhidas, troca obrigatória e login posterior. Testar notificação com e-mail de teste sem registrar a senha em logs.
5. Se houver falha, restaurar as imagens anteriores. Manter a tabela aditiva; contas novas exigem a versão atualizada para o primeiro acesso.

A migration e a publicação em produção dependem de autorização específica, conforme as regras `AGENTS.md` fornecidas para este projeto.
