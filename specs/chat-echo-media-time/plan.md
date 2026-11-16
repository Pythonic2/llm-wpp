# Plano

1. Preservar backup e identificar assinatura Meta. O usuário ativa `smb_message_echoes` no App Dashboard; API oficial não publica assinaturas WhatsApp.
2. Unificar extração de horário da Meta e persistência de `sent_at`; ordenar por esse campo com desempate estável e expor no serializer.
3. Fazer migração de dados aditiva para mensagens existentes com timestamp confiável; verificar contagem antes/depois e guardar caminho de recuperação.
4. Substituir gravação automática de novas imagens/vídeos por proxy autenticado sob demanda, com limite de tamanho, URL Meta validada e sem gravação em storage.
5. Ajustar frontend para mostrar horário original, permitir download imediato de imagem/vídeo e usar modal na equipe.
6. Executar testes backend/frontend e revisão de segurança; publicar imagens ARM64 e aplicar a migração no Raspberry somente após autorização específica para a alteração de dados em produção.
