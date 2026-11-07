# TASK: TASK-01: Instalação e Fundamentos do Oracle Database

> **Formação / Curso:** Imersão Oracle Database com IA
> **Módulo:** Instalação e Fundamentos do Oracle Database
> **Origem Técnica:** `aula-midia-dbaocm-hotmart-club.md`
> **Padrão Arquitetural:** Issue-as-a-Prompt (Executável por Agentes / TDD)

---

## 1. Contexto & Visão de Arquitetura
Esta tarefa traduz o conhecimento da aula em um pacote de entrega executável.

### Objetivos Específicos:
- **Compreender a Filosofia "AI First":** Assimilar a mudança de paradigma no trabalho de tecnologia, onde a Inteligência Artificial atua como copiloto ou piloto de etapas do desenvolvimento e operação de bancos de dados.
- **Dominar a Configuração de Ambientes Híbridos (On-Premise & Cloud):** Preparar ambientes de desenvolvimento e teste, instalando o Oracle Database 19c localmente e provisionando recursos na Oracle Cloud (OCI Free Tier), incluindo a configuração de ferramentas como Cloud Code e VS Code com extensões OCI/SQL.
- **Executar Instalação e Configuração Essencial:** Instalar o Oracle Database 19c em máquinas virtuais Linux (VirtualBox) e criar contas na Oracle Cloud Free Tier, contornando pré-requisitos e validando logs para garantir um ambiente operacional.
- **Entender a Integração de IA com Oracle Database:** Identificar os métodos e ferramentas para conectar modelos de IA (via MCP) ao Oracle Database, explorando capacidades como SELECT AI (linguagem natural), Skills de IA, Agentes de IA e o Agent Factor (no-code).
- **Analisar o Impacto e Potencial da IA na Engenharia de Dados:** Avaliar como a IA pode acelerar tarefas de DBA e desenvolvimento (10x a 20x mais rápido), otimizar performance, auxiliar na modelagem de dados e expandir a aplicação do Oracle Database para demandas de negócio através de agentes inteligentes.
- **Reconhecer a Importância dos Dados Estruturados para IA:** Compreender que a eficácia da IA é intrinsecamente ligada à qualidade e organização dos dados no Oracle Database, utilizando recursos como Vector Search e Grafos.

Implementar a lógica descrita com base no modelo de referência:
```sql
-- Exemplo de uma consulta SELECT AI (conceitual)
-- O usuário faria uma pergunta em linguagem natural, e a IA geraria este SQL:

-- Pergunta do usuário: "Quero saber os 10 clientes com maior volume de vendas no último mês."

SELECT
    c.nome_cliente,
    SUM(v.valor_total) AS volume_vendas
FROM
    clientes c
JOIN
    vendas v ON c.id_cliente = v.id_cliente
WHERE
    v.data_venda >= TRUNC(SYSDAT
...
```

---

## 2. Contrato de Entrada e Saída
### Entrada (Inputs)
- Especificação técnica e conceitos definidos em `aulas/aula-midia-dbaocm-hotmart-club.md`.
- Dependências e ambiente de execução.

### Saída Esperada (Outputs)
- Código fonte funcional cobrindo o tema `Instalação e Fundamentos do Oracle Database`.
- Suíte de testes automatizados com assertivas de conformidade.

---

## 3. Restrições Rígidas & Anti-Padrões
- Proibido hardcode de credenciais ou chaves de API (usar `.env`).
- Manter convenções de nomenclatura e tipagem explícita (Python/TypeScript/SQL).
- NUNCA efetuar commits diretos na branch padrão (`main`/`master`); todo trabalho deve ser executado em branch de feature.
- Testes automatizados obrigatórios antes de concluir a entrega.

---

## 4. Roteiro de Implementação Guiado por Testes (TDD)
1. **RED:** Criar a suíte de testes unitários ou de integração especificando os comportamentos esperados.
2. **GREEN:** Implementar a lógica nuclear mínima e suficiente para aprovar todos os testes.
3. **REFACTOR:** Refatorar o código isolando responsabilidades, melhorando tipagem e documentação técnica.

---

## 5. Critérios de Aceite & Comando de Validação Factual
- [ ] Código fonte implementado e documentado.
- [ ] Suíte de testes automatizada verde (100% passing).
- [ ] Comando de verificação factual:
```bash
pytest tests/ -q  # ou comando de teste específico da stack
```
