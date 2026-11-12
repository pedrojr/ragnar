# Segurança

[English](SECURITY.md)

## Como relatar uma vulnerabilidade

Não abra issue pública. Use o [relato privado de vulnerabilidades do GitHub](https://github.com/pedrojr/ragnar/security/advisories/new) com os passos para reproduzir o problema.

Conte com uma primeira resposta em até 7 dias. O Ragnar é mantido por melhor esforço, e só a versão mais recente recebe correções.

## O que o Ragnar protege

- **Segredos no código.** O indexador roda o gitleaks no texto normalizado antes de indexar, e um achado interrompe a execução. O índice fica no último commit bom. O servidor pode escolher `index.secret_action = "skip"`, que deixa de fora os arquivos com achados.
- **Acesso de escrita.** O servidor MCP conecta com um papel que só lê, nas réplicas, e nunca recebe a senha de escrita.
- **Acesso a repositórios.** Tokens são guardados como hash. Cada um lista os repositórios que pode ler.
- **Configuração vinda do repositório.** Um repositório só muda como é dividido em chunks, então não desvia embeddings nem desliga a varredura de segredos.
- **Injeção de prompt pelo código.** O conteúdo recuperado vai entre delimitadores, tags de fechamento nele são escapadas, e ele é marcado como dado. Isso reduz o risco, mas não o elimina. Um agente ainda pode seguir instruções plantadas num repositório, então revise o que seus agentes fazem.

## Antes de expor um servidor

O [exemplo](example/README.pt-BR.md) não é endurecido. No Compose ele fica em `127.0.0.1`. No Kubernetes, o Docker Desktop publica o `LoadBalancer` do MCP em todas as interfaces de rede. Em rede compartilhada:

1. Coloque um proxy TLS na frente do servidor MCP. Tokens trafegam num cabeçalho.
2. Mantenha Postgres, embedder e `/metrics` fora da rede.
3. Emita um token por time, com o mínimo de repositórios. Libere um repositório só para quem já pode lê-lo.
4. Trate o banco e os logs de auditoria como código-fonte. O índice guarda código, e os logs guardam o texto das buscas.
5. Troque os tokens quando pessoas saírem. Altere o `acl.json` e reinicie o servidor.

## Provedores hospedados

Embeddings, resumos de arquivo, reranking, recuperação contextual e expansão de consulta enviam código ou perguntas ao endpoint configurado. A montagem padrão mantém tudo nas suas máquinas. Antes de apontar qualquer um deles para um provedor hospedado, confirme que ele atende à sua política.
