---
name: oracle-shadow-ai
description: Detecta e documenta uso de IA não homologado (Shadow AI) em ambientes Oracle Database — chamadas de saída do banco para APIs de LLM (OpenAI, Anthropic, Gemini, Bedrock, Azure OpenAI, OCI GenAI), ACLs de rede e credenciais que permitem essas chamadas, código PL/SQL que monta prompts ou consome LLM, recursos nativos de IA ligados sem governança (tipo VECTOR, índices vetoriais, modelos ONNX, OML, DBMS_VECTOR, DBMS_CLOUD_AI, Select AI), ferramentas e agentes de IA conectados ao banco (clientes MCP, Python/LangChain, IDEs com copiloto) e extração em massa de dados que alimenta ferramenta externa. Use sempre que o usuário falar em shadow AI, shadow IT, IA não autorizada, uso indevido de IA, governança de IA, LGPD e IA, vazamento de dados para IA, auditoria de IA, ou perguntar se alguém está usando ChatGPT/Copilot/LLM com os dados do banco, se o banco chama API externa, quem está conectando no banco, ou se existe vetor/embedding/modelo de IA no banco — mesmo sem usar a palavra "shadow". Use também ao interpretar DBA_NETWORK_ACLS, DBA_HOST_ACES, DBA_CREDENTIALS, DBA_SOURCE, DBA_MINING_MODELS, DBA_FEATURE_USAGE_STATISTICS, UNIFIED_AUDIT_TRAIL ou V$SESSION com esse objetivo. Complementa oracle-inventario e oracle-operacional — em health check ou documentação completa, use as três.
---

# Shadow AI em ambiente Oracle

Levantamento de uso de IA não homologado a partir do que o banco de dados enxerga.

Todo o SQL está neste arquivo, nas seções **Vetor 1** a **Vetor 4**, em forma executável direta
— sem `SPOOL`, sem `SET`, sem concatenação de colunas. Funciona igual pelo MCP do SQLcl, colado
no SQLcl/SQL\*Plus ou em qualquer cliente.

Os Passos 1 a 5 são o roteiro. Os Vetores são a coleta. O Anexo A é a lista de indicadores
usada pelos Vetores 1 e 2 — leia antes de montar qualquer `REGEXP_LIKE`.

**Somente leitura.** Nenhuma consulta aqui cria, altera ou remove nada. Três proibições
específicas desta skill:

- **Nunca execute `UTL_HTTP`, `DBMS_CLOUD.SEND_REQUEST`, `APEX_WEB_SERVICE` ou `DBMS_VECTOR`
  "para testar" se a chamada externa funciona.** Isso transforma a auditoria em um vazamento
  real e gera tráfego de saída em nome do cliente. Constatar que a ACL existe é suficiente.
- **Nunca execute `DBMS_NETWORK_ACL_ADMIN`** (qualquer procedure). Parece administração de
  segurança, é DDL de privilégio.
- **Nunca reproduza segredos.** Chaves de API, tokens e senhas aparecem com frequência
  hardcoded em `DBA_SOURCE`. Reporte a localização (owner, objeto, linha) e mascare o valor.
  Ver a regra de mascaramento no Vetor 2.

Privilégio necessário: `CREATE SESSION` + `SELECT_CATALOG_ROLE`. **Não exige SYSDBA.** Para a
trilha de auditoria unificada é preciso também `AUDIT_VIEWER` (12c+) — se não houver, pule
essa seção e registre a lacuna.

---

## Índice

**Roteiro**
- [O que o banco vê e o que não vê](#o-que-o-banco-vê-e-o-que-não-vê)
- [Passo 1 — Três perguntas obrigatórias](#passo-1--três-perguntas-obrigatórias)
- [Passo 2 — Conexão](#passo-2--conexão)
- [Passo 3 — Coleta](#passo-3--coleta)
- [Passo 4 — Triagem](#passo-4--triagem)
- [Passo 5 — Relatório](#passo-5--relatório)
- [Como analisar](#como-analisar) · [Cuidados operacionais](#cuidados-operacionais) · [Diferenças por versão](#diferenças-por-versão)

**Coleta**
- [Vetor 1 — Saída de rede](#vetor-1--saída-de-rede) — ACL, credencial, privilégio, job externo, Java, db link, diretório
- [Vetor 2 — Código e SQL](#vetor-2--código-e-sql) — `DBA_SOURCE`, segredos, objetos recentes, cursor cache
- [Vetor 3 — Recursos nativos de IA do Oracle](#vetor-3--recursos-nativos-de-ia-do-oracle) — descoberta de superfície, feature usage, VECTOR, ONNX, Select AI, APEX
- [Vetor 4 — Acesso e extração](#vetor-4--acesso-e-extração) — sessões, ferramentas conectadas, auditoria, volume
- [Anexo A — Catálogo de indicadores](#anexo-a--catálogo-de-indicadores) — hosts, endpoints, pacotes, regex prontos

---

## O que o banco vê e o que não vê

Diga isso ao usuário **antes de coletar**, em uma ou duas linhas. Prometer mais do que o dado
entrega é o principal risco de credibilidade deste levantamento.

O banco detecta bem:

- o **próprio banco** chamando serviço de IA (PL/SQL → API de LLM);
- **recursos de IA nativos** do Oracle ligados e em uso;
- **ferramentas e agentes de IA conectados** ao banco, inclusive servidores MCP;
- **extração em massa** compatível com alimentar ferramenta externa;
- **os habilitadores** — ACL, credencial, privilégio — que tornam tudo acima possível.

O banco **não** detecta a forma mais comum de shadow AI: alguém copiar um resultado da tela e
colar no navegador. Isso só aparece em CASB, proxy, DLP ou gestão de despesas. Este
levantamento cobre o vetor "dado sai pelo banco", não o vetor "dado sai pela pessoa".

---

## Passo 1 — Três perguntas obrigatórias

Sempre antes de executar qualquer coisa. Faça as três de uma vez, com opções clicáveis quando
a interface permitir, e espere a resposta. Se a conversa já respondeu alguma, confirme em uma
linha e siga.

**Pergunta 1 — Linha de base de homologação**

> "O que já é homologado hoje? (para eu separar shadow do que foi aprovado)"
> `Nada de IA é aprovado` · `Tem ferramenta aprovada — eu listo` · `Não sei / não existe política`

Sem essa resposta **não existe "shadow"**, só "uso". Um profile de Select AI apontando para o
OCI GenAI contratado é arquitetura; o mesmo profile apontando para uma chave pessoal de OpenAI
é achado. O dado coletado é idêntico nos dois casos — o que muda é a linha de base.

Em "Não sei", siga assim mesmo, mas rotule o relatório como **inventário de uso de IA**, não
como "shadow AI", e trate a ausência de política como o primeiro achado.

**Pergunta 2 — Arquitetura multitenant**

> "O banco é CDB (multitenant, com PDBs) ou não-CDB?"
> `CDB com PDBs` · `Não-CDB` · `Não sei`

ACL, credencial, código e profiles de IA vivem **por container**. Coletar só no root devolve a
configuração da Oracle e nenhuma aplicação — exatamente o oposto do que se procura.

- **CDB** → colete no root e repita o Passo 3 **conectado a cada PDB**.
- **Não-CDB** → uma coleta só.
- **Não sei** → `SELECT NVL(MAX(value),'FALSE') AS multitenant FROM v$parameter WHERE name = 'enable_pluggable_database'`

**Pergunta 3 — Licença de Diagnostic Pack**

> "O ambiente tem licença de Diagnostic Pack?"
> `Sim` · `Não` · `Não sei`

O histórico de longo prazo (quem conectou nos últimos meses, qual SQL rodou e sumiu do cache)
sai de `DBA_HIST_*` e de `V$ACTIVE_SESSION_HISTORY` — **ASH é licenciado mesmo sendo view V$**.

- **Sim** → pode usar a seção AWR do Vetor 4.
- **Não** / **Não sei** → **não toque em `DBA_HIST_*` nem em `V$ACTIVE_SESSION_HISTORY`**. Todo
  o resto funciona sem pack. Em "Não sei", rode antes
  `SELECT name, detected_usages, currently_used FROM dba_feature_usage_statistics WHERE name LIKE '%Diagnostic%'`
  e trate como "Não" até haver confirmação.

---

## Passo 2 — Conexão

### Com MCP do SQLcl

Inspecione a lista de ferramentas MCP disponíveis antes de agir; os nomes variam por versão do
SQLcl. Tipicamente há uma ferramenta para listar conexões salvas, uma para conectar por nome e
uma para executar SQL. Use as que realmente existirem, não presuma nomes.

1. Listar as conexões salvas.
2. Pedir ao usuário **o nome da conexão** — nunca IP, usuário ou senha.
3. Confirmar onde você está antes de coletar:
   `SELECT sys_context('USERENV','DB_NAME') AS banco, sys_context('USERENV','CON_NAME') AS container, user AS usuario FROM dual`

- **Uma consulta por chamada.** Não junte comandos separados por `;`.
- **Só SELECT.** Se uma chamada falhar por permissão, relate e siga para a próxima seção em vez
  de contornar com outro usuário ou privilégio.

Há uma ironia útil aqui, e vale dizer ao usuário: **se você está rodando esta skill via MCP,
você é uma ferramenta de IA conectada ao banco.** Sua própria sessão vai aparecer no Passo 3.
Marque-a como tal no relatório em vez de deixá-la passar como achado — e, se o cliente MCP não
estiver homologado, esse é um achado legítimo sobre o próprio processo.

### Sem MCP

Entregue as mesmas consultas para o DBA executar, envolvidas em:

```sql
SET SQLFORMAT CSV
SPOOL shadowai_<banco>.csv
-- consultas aqui
SPOOL OFF
```

`SET SQLFORMAT CSV` é do SQLcl. Em SQL\*Plus 12.2+ use `SET MARKUP CSV ON`; em versões
anteriores, peça saída em texto.

---

## Passo 3 — Coleta

Execute na ordem. As seções são independentes: se uma falhar por permissão ou por view
inexistente, registre e continue.

| Ordem | O que | Seção | Peso |
|---|---|---|---|
| 1º | Descoberta de superfície (o que existe nesta versão) | Vetor 3, item 3.0 | pré-requisito |
| 2º | Saída de rede: ACL, credencial, db link, diretório, job externo | Vetor 1 | alto |
| 3º | Código e SQL: PL/SQL, jobs, cursor cache | Vetor 2 | alto |
| 4º | Recursos nativos: VECTOR, ONNX, OML, Select AI, feature usage | Vetor 3 | médio |
| 5º | Acesso e extração: sessões, programas, auditoria, volume | Vetor 4 | médio |

Comece **sempre** pelo item 3.0. Ele descobre quais views de IA existem nesta versão e evita
dezenas de `ORA-00942` nas seções seguintes — `DBA_CLOUD_AI_PROFILES`, `DBA_VECTOR_INDEXES` e
afins só existem em 23ai/Autonomous.

A lista de indicadores usada pelos Vetores 1 e 2 (hosts de provedores, pacotes, palavras-chave,
prefixos de chave de API) está no **Anexo A**. Leia antes de montar qualquer `REGEXP_LIKE` — não
invente a lista de cabeça e não a reduza a "openai".

Se alguma seção retornar volume grande demais, colete o agregado e só desça ao detalhe quando a
análise pedir.

---

## Passo 4 — Triagem

Coletar é mecânico. A triagem é o valor. Classifique **cada achado** em um dos quatro níveis —
nunca entregue uma lista crua de linhas retornadas.

**1. Confirmado** — há evidência de que o banco conversou com um serviço de IA, ou de que um
recurso de IA está em uso. Exemplos: ACL para `api.openai.com` **mais** código que chama esse
host; `DBA_FEATURE_USAGE_STATISTICS` com `detected_usages > 0` em recurso vetorial; profile de
Select AI ativo; coluna `VECTOR` populada.

**2. Provável** — indício forte sem fechar o circuito. Exemplos: credencial chamada
`OPENAI_CRED` sem código correspondente encontrado; ACL para host de LLM sem uso registrado;
pacote com nome de IA e código ofuscado.

**3. Habilitador** — não é uso, é a porta aberta. Exemplos: `EXECUTE ON UTL_HTTP` concedido a
`PUBLIC`; ACL com host `*`; diretório apontando para caminho gravável por aplicação. Entra no
relatório como postura de segurança, **não** como shadow AI. Misturar os dois destrói a
credibilidade do documento inteiro.

**4. Ruído** — falso positivo identificado. Exemplos: coluna `PROMPT_TEXT` de um sistema de
atendimento anterior à IA generativa; `UTL_HTTP` usado para consultar a Receita; host
`*.amazonaws.com` que é o S3 do backup. **Liste o ruído descartado**, com o motivo. É o que
mostra que a triagem aconteceu.

Depois, cruze com a resposta da Pergunta 1 e marque cada achado como **homologado** ou **não
homologado**. Só o segundo grupo é shadow AI.

Regra que vale repetir: **um indicador isolado quase nunca é achado.** ACL sem código é
configuração esquecida. Código sem ACL não roda. Os dois juntos, e com uso registrado em
`DBA_FEATURE_USAGE_STATISTICS` ou no cursor cache, é um circuito fechado.

---

## Passo 5 — Relatório

Monte uma planilha com uma aba por vetor — Achados, Saída de rede, Código, Recursos nativos,
Acesso, Habilitadores, Ruído descartado — e um resumo executivo. Invoque a skill de planilha
disponível no ambiente (`xlsx`, que em alguns ambientes aparece como `anthropic-skills:xlsx`)
antes de gerar o arquivo. Na aba Achados, destaque por nível: vermelho para Confirmado não
homologado, laranja para Provável, cinza para Habilitador. Congele a primeira linha e ative
filtro em todas as abas.

```markdown
# Shadow AI — <BANCO>
Coletado em <data> · <host> · Oracle <versão> · container <nome> · usuário <user>

## Escopo e limites
O que foi verificado e o que este levantamento não alcança (vetor pessoa→navegador).
Linha de base de homologação usada.

## Achados
Tabela: nível · vetor · evidência · onde (owner/objeto/linha ou host) · homologado? · desde quando

## Habilitadores
Postura que permite chamada externa, independente de haver uso hoje.

## Ruído descartado
O que parecia e não era, com o motivo.

## Lacunas da coleta
Seções não executadas por falta de privilégio, licença ou view inexistente.

## Recomendações
```

Com mais de um banco, gere uma aba comparativa: banco, versão, container, nº de achados por
nível, ACLs para host de IA, recursos nativos em uso, ferramentas de IA conectadas. É essa
visão que responde "temos shadow AI no ambiente?" em vez de "neste banco".

---

## Como analisar

- **Data importa mais que existência.** `DBA_FEATURE_USAGE_STATISTICS.FIRST_USAGE_DATE`,
  `DBA_OBJECTS.CREATED` e `LAST_DDL_TIME` situam o achado no tempo. "Existe uma integração com
  LLM" é fraco; "uma integração com LLM foi criada em março e roda desde então" move decisão.
- **Siga a credencial.** Credencial é o elo mais informativo do circuito: liga um dono a um
  provedor. Quem criou, quando, e quais jobs a usam.
- **ACL com host `*` é sempre achado**, mesmo sem uso de IA. Significa que o banco pode chamar
  qualquer endereço da internet.
- **Chave de API hardcoded é um achado maior que o shadow AI em si.** Ela está em backup, em
  export, em ambiente de homologação e no repositório de código. Trate como incidente de
  credencial exposta e escale, sem reproduzir o valor.
- **Volume extraído** — uma consulta que devolve milhões de linhas de tabela sensível para um
  programa não homologado é o achado mais próximo de vazamento real, mesmo sem IA envolvida.
- **Recurso nativo sem governança é diferente de shadow.** Alguém usando `DBMS_VECTOR` no
  próprio banco não manda dado para fora — o dado fica lá dentro. Risco de conformidade e
  arquitetura, não de vazamento. Separe.
- **Ausência de evidência não é evidência de ausência.** Sem auditoria unificada ligada, sem
  Diagnostic Pack e com cursor cache reciclado, a janela de visão pode ser de horas. Diga qual
  foi a janela real de cada seção.

Separe **fato** de **recomendação**. Fatos saem das views; recomendações dependem de contexto
que você não tem (contrato com o provedor, política interna, criticidade do dado).

---

## Cuidados operacionais

- **Isto encosta em monitoramento de pessoas.** O relatório fala de sistemas, integrações e
  contas de aplicação. Quando um achado aponta para um usuário nominal, registre o fato sem
  julgamento de intenção e sugira que RH, jurídico ou segurança conduzam daí. Não escreva
  hipótese sobre motivação de ninguém.
- **`DBA_SOURCE` com `REGEXP_LIKE` faz full scan.** Em banco com muito PL/SQL pode pesar. Rode
  a passada de alta confiança primeiro; só rode a passada ampla fora do pico.
- **`UNIFIED_AUDIT_TRAIL` pode ter dezenas de milhões de linhas.** Sempre com filtro de data e
  sempre agregado. Nunca `SELECT *`.
- **`V$SQL` e `UNIFIED_AUDIT_TRAIL` contêm dados de negócio** dentro do texto do SQL e dos
  binds. Trunque, mascare e nunca cole o texto bruto no relatório.
- Em Data Guard, standby em `MOUNTED` não responde às views `DBA_*`. Colete do primary.
- Em RAC, `v$session` mostra só a instância corrente; use `gv$session`.
- Nunca escreva senhas, tokens ou strings de conexão no relatório. Nome de host, de schema, de
  objeto e de credencial são metadados e podem entrar.

## Diferenças por versão

| Recurso | 11g | 12c–19c | 21c+ / 23ai |
|---|---|---|---|
| `DBA_NETWORK_ACLS` (modelo XDB, com `ACL`) | sim | sim (compat.) | sim |
| `DBA_HOST_ACES`, `DBA_WALLET_ACES` | não | sim | sim |
| `UNIFIED_AUDIT_TRAIL` | não (`DBA_AUDIT_TRAIL`) | sim | sim |
| `DBA_CREDENTIALS` | não (`DBA_SCHEDULER_CREDENTIALS`) | sim | sim |
| `DBA_USERS.ORACLE_MAINTAINED` | não | sim | sim |
| Tipo `VECTOR`, `DBMS_VECTOR`, índice vetorial | não | não | 23ai |
| `DBMS_CLOUD_AI` / Select AI | não | Autonomous | 23ai / Autonomous |
| Modelo ONNX in-database | não | 21c parcial | sim |
| `FETCH FIRST n ROWS ONLY` | não | sim | sim |

As consultas usam `ROWNUM` e lista fixa de schemas Oracle para atender todo o parque com um
conjunto só, de 11g em diante.

---

## Vetor 1 — Saída de rede

O caminho pelo qual o dado sai do banco. É o vetor de maior peso: sem ACL não há chamada
externa, então **a ausência total de ACL para host de IA é uma resposta forte**, e não apenas
"nada encontrado".

Índice:
- [1.1 ACLs de rede](#11-acls-de-rede)
- [1.2 Quem tem permissão em cada ACL](#12-quem-tem-permissão-em-cada-acl)
- [1.3 Wallets](#13-wallets)
- [1.4 Credenciais](#14-credenciais)
- [1.5 Privilégios habilitadores](#15-privilégios-habilitadores)
- [1.6 Jobs com execução externa](#16-jobs-com-execução-externa)
- [1.7 Java no banco](#17-java-no-banco)
- [1.8 Database links](#18-database-links)
- [1.9 Diretórios](#19-diretórios)
- [1.10 DBMS_CLOUD fora do Autonomous](#110-dbms_cloud-fora-do-autonomous)

---

### 1.1 ACLs de rede

Comece listando **tudo**. Costuma ser dezenas de linhas, não milhares, e o panorama importa
mais que o filtro.

```sql
SELECT host, lower_port, upper_port, acl
  FROM dba_network_acls
 ORDER BY host
```

Se a versão for 12c+ e a consulta acima devolver pouco, verifique também o modelo novo, que
convive com o antigo:

```sql
SELECT host, lower_port, upper_port, ace_order, principal, principal_type,
       privilege, grant_type, inverted_principal,
       TO_CHAR(start_date,'YYYY-MM-DD') AS inicio,
       TO_CHAR(end_date,'YYYY-MM-DD')   AS fim
  FROM dba_host_aces
 ORDER BY host, ace_order
```

Filtro de alta confiança (padrão completo no Anexo A):

```sql
SELECT host, lower_port, upper_port, acl
  FROM dba_network_acls
 WHERE host = '*'
    OR REGEXP_LIKE(LOWER(host),
       'api\.openai\.com|openai\.azure\.com|cognitiveservices\.azure\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|aiplatform\.googleapis\.com|bedrock-runtime|inference\.generativeai|api\.cohere|api\.mistral\.ai|api\.deepseek\.com|api\.groq\.com|api\.x\.ai|api\.perplexity\.ai|openrouter\.ai|api\.together|api\.replicate\.com|huggingface\.co|ollama')
 ORDER BY host
```

Como ler:

- **`host = '*'`** — o banco pode chamar qualquer endereço da internet. Achado de segurança por
  si só, entra como Habilitador mesmo sem nenhum sinal de IA. É também o motivo pelo qual a
  ausência de ACL específica para OpenAI não prova ausência de uso: com `*`, não precisa.
- **Host genérico com porta 443 e sem `upper_port`** — permissivo; veja qual principal usa.
- **ACL criada recentemente** — cruze com `1.4` (credenciais) e com `LAST_DDL_TIME` do código.

---

### 1.2 Quem tem permissão em cada ACL

**12c+:** use `DBA_HOST_ACES` (consulta em 1.1) — já traz `PRINCIPAL` e `PRIVILEGE`.

**11g** e modelo XDB legado:

```sql
SELECT acl, principal, privilege, is_grant, invert,
       TO_CHAR(start_date,'YYYY-MM-DD') AS inicio,
       TO_CHAR(end_date,'YYYY-MM-DD')   AS fim
  FROM dba_network_acl_privileges
 ORDER BY acl, principal
```

Os privilégios que importam são `connect` (abre socket) e `resolve` (resolve DNS). `connect`
concedido a um schema de aplicação, para host de IA, é elo de circuito fechado — leve para a
seção 2 e procure o código desse mesmo owner.

---

### 1.3 Wallets

Chamada HTTPS para API de IA precisa de wallet com a CA do provedor (ou `DBMS_CLOUD`). A
presença de ACL de wallet junto de ACL de host é um par que raramente é acidental.

```sql
SELECT host, lower_port, upper_port, principal, principal_type, privilege, wallet_path
  FROM dba_wallet_aces
 ORDER BY wallet_path, principal
```

(12c+. Em 11g, procure `resolve`/`use-client-certificates` em `DBA_NETWORK_ACL_PRIVILEGES`.)

```sql
SELECT name, value FROM v$parameter
 WHERE name IN ('wallet_root','ssl_wallet','encryption_wallet_location')
```

---

### 1.4 Credenciais

O elo mais informativo do circuito: liga um dono a um provedor, com data.

```sql
SELECT * FROM dba_credentials
```

`SELECT *` é intencional — as colunas variam entre 12c, 19c e 23ai, e a tabela é pequena.
Nenhuma coluna exposta contém senha. Em 11g, use `DBA_SCHEDULER_CREDENTIALS`.

Procure no nome e no `username`: `OPENAI`, `AZURE`, `ANTHROPIC`, `GENAI`, `BEDROCK`, `LLM`,
`API`, `TOKEN`, `BEARER`. Nome de credencial é escolhido por gente com pressa e costuma
descrever exatamente o que faz.

Data de criação (a view de credenciais não traz, o dicionário traz):

```sql
SELECT owner, object_name, object_type,
       TO_CHAR(created,'YYYY-MM-DD HH24:MI')      AS criado_em,
       TO_CHAR(last_ddl_time,'YYYY-MM-DD HH24:MI') AS ultimo_ddl
  FROM dba_objects
 WHERE object_type = 'CREDENTIAL'
 ORDER BY created DESC
```

---

### 1.5 Privilégios habilitadores

Postura, não uso. Vai para a seção **Habilitadores** do relatório.

```sql
SELECT p.grantee, p.table_name AS pacote, p.privilege, p.grantor
  FROM dba_tab_privs p
 WHERE p.privilege = 'EXECUTE'
   AND p.table_name IN ('UTL_HTTP','UTL_TCP','UTL_SMTP','UTL_INADDR','UTL_URL',
                        'DBMS_NETWORK_ACL_ADMIN','DBMS_CLOUD','DBMS_CLOUD_AI',
                        'DBMS_VECTOR','DBMS_VECTOR_CHAIN','APEX_WEB_SERVICE',
                        'DBMS_JAVA','DBMS_LDAP','DBMS_SCHEDULER','DBMS_LOB')
 ORDER BY CASE WHEN p.grantee = 'PUBLIC' THEN 0 ELSE 1 END, p.table_name, p.grantee
```

`EXECUTE ON UTL_HTTP` para `PUBLIC` é o padrão histórico em bancos antigos e continua sendo o
achado mais comum desta seção. Reporte, mas não o confunda com evidência de IA.

---

### 1.6 Jobs com execução externa

Um job `EXECUTABLE` chamando `curl` contorna ACL, wallet e `UTL_HTTP` inteiros.

```sql
SELECT owner, job_name, job_type, enabled, credential_name, destination,
       TO_CHAR(last_start_date,'YYYY-MM-DD HH24:MI') AS ultima_exec,
       SUBSTR(job_action,1,400) AS acao
  FROM dba_scheduler_jobs
 WHERE job_type IN ('EXECUTABLE','EXTERNAL_SCRIPT','BACKUP_SCRIPT','SQL_SCRIPT')
 ORDER BY last_start_date DESC NULLS LAST
```

E os jobs PL/SQL cujo corpo cheira a IA:

```sql
SELECT owner, job_name, job_type, enabled, repeat_interval,
       TO_CHAR(last_start_date,'YYYY-MM-DD HH24:MI') AS ultima_exec,
       SUBSTR(job_action,1,400) AS acao
  FROM dba_scheduler_jobs
 WHERE REGEXP_LIKE(LOWER(job_action),
       'openai|anthropic|claude|gemini|bedrock|cohere|mistral|deepseek|ollama|curl|wget|utl_http|apex_web_service|dbms_cloud\.send_request|dbms_vector|dbms_cloud_ai|embedding|/v1/')
 ORDER BY last_start_date DESC NULLS LAST
```

Job desabilitado ainda é achado: mostra que existiu, e pode ser reativado.

---

### 1.7 Java no banco

`SocketPermission` concedida a um schema de aplicação é saída de rede irrestrita, sem ACL.

```sql
SELECT grantee, type_name, name, action, enabled
  FROM dba_java_policy
 WHERE type_name LIKE '%SocketPermission%'
    OR type_name LIKE '%RuntimePermission%'
 ORDER BY grantee, name
```

```sql
SELECT owner, COUNT(*) AS classes_java
  FROM dba_objects
 WHERE object_type LIKE 'JAVA%'
   AND owner NOT IN ('SYS','SYSTEM','ORDSYS','MDSYS','XDB','OJVMSYS','EXFSYS')
 GROUP BY owner
 ORDER BY 2 DESC
```

---

### 1.8 Database links

Dado pode sair para outro banco antes de ir para a IA — e o link pode apontar para fora da rede.

```sql
SELECT owner, db_link, username, host,
       TO_CHAR(created,'YYYY-MM-DD') AS criado_em
  FROM dba_db_links
 ORDER BY created DESC
```

---

### 1.9 Diretórios

Caminho clássico de extração: job escreve CSV num diretório, processo externo lê e envia.

```sql
SELECT owner, directory_name, directory_path
  FROM dba_directories
 ORDER BY directory_name
```

Quem escreve neles:

```sql
SELECT p.grantee, p.table_name AS diretorio, p.privilege
  FROM dba_tab_privs p
  JOIN dba_directories d ON d.directory_name = p.table_name
 WHERE p.privilege IN ('WRITE','READ')
 ORDER BY p.table_name, p.grantee
```

Diretório apontando para caminho compartilhado, montagem de rede, `/tmp` ou pasta de usuário é
o que interessa. Tabelas externas usando esses diretórios:

```sql
SELECT owner, table_name, default_directory_name, access_type
  FROM dba_external_tables
 ORDER BY owner, table_name
```

---

### 1.10 DBMS_CLOUD fora do Autonomous

`DBMS_CLOUD` não vem instalado em banco on-premises — se está lá, alguém instalou de propósito,
e ele faz REST para qualquer endpoint com `SEND_REQUEST`.

```sql
SELECT owner, object_name, object_type,
       TO_CHAR(created,'YYYY-MM-DD') AS criado_em, status
  FROM dba_objects
 WHERE object_name LIKE 'DBMS_CLOUD%'
   AND object_type IN ('PACKAGE','PACKAGE BODY','SYNONYM')
 ORDER BY object_name
```

Se aparecer em banco que não é Autonomous, é achado de configuração e merece a pergunta "quem
instalou e para quê" — mesmo antes de olhar IA.

---

## Vetor 2 — Código e SQL

Onde a integração com IA fica escrita. Fecha o circuito que a seção 1 abriu: ACL diz que
*pode*, o código diz que *faz*.

Índice:
- [Regra de mascaramento](#regra-de-mascaramento-leia-antes)
- [2.1 Filtro de schemas](#21-filtro-de-schemas)
- [2.2 Passada de alta confiança](#22-passada-de-alta-confiança)
- [2.3 Passada de contexto](#23-passada-de-contexto)
- [2.4 Passada ampla](#24-passada-ampla)
- [2.5 Detalhe de um objeto](#25-detalhe-de-um-objeto)
- [2.6 Segredos hardcoded](#26-segredos-hardcoded)
- [2.7 Objetos criados recentemente](#27-objetos-criados-recentemente)
- [2.8 Tabelas de configuração](#28-tabelas-de-configuração)
- [2.9 Cursor cache](#29-cursor-cache)
- [2.10 Views e triggers](#210-views-e-triggers)

---

### Regra de mascaramento (leia antes)

Chave de API hardcoded aparece com frequência nesta seção. **Nunca traga o valor para a
conversa nem para o relatório** — ele ficaria registrado em mais um lugar, e a conversa não é
cofre. Mascare na origem, dentro do próprio SQL:

```sql
REGEXP_REPLACE(
  REGEXP_REPLACE(text, '(sk-[a-z]*-?|gsk_|xai-|hf_|r8_|AIza|AKIA)[A-Za-z0-9_\-]{4,}', '\1********'),
  '(?i)(bearer|api[-_]?key|password|senha|token|secret)([''"[:space:]:=]+)[^''"[:space:]]+',
  '\1\2********')
```

Aplique esse envelope em toda consulta que devolva `text` de `DBA_SOURCE`, `job_action` ou
`sql_text`. Nas consultas abaixo ele está abreviado como `<MASCARA>(text)` — substitua pelo
bloco acima.

Ao reportar: diga **owner, objeto, tipo e linha**. Isso basta para o DBA localizar. Se
encontrar segredo, trate como incidente de credencial exposta e recomende rotação — vazou para
backup, export e ambiente de teste muito antes desta auditoria.

---

### 2.1 Filtro de schemas

**12c+** (preferível — acompanha a versão sozinho):

```sql
SELECT username FROM dba_users WHERE oracle_maintained = 'N' ORDER BY username
```

Use o resultado como filtro: `WHERE owner IN (SELECT username FROM dba_users WHERE oracle_maintained='N')`.

**11g** — lista fixa:

```sql
owner NOT IN ('SYS','SYSTEM','XDB','MDSYS','CTXSYS','ORDSYS','ORDDATA','WMSYS','OLAPSYS',
              'EXFSYS','DBSNMP','APPQOSSYS','OUTLN','ORACLE_OCM','SI_INFORMTN_SCHEMA',
              'LBACSYS','DVSYS','AUDSYS','GSMADMIN_INTERNAL','OJVMSYS','SYSBACKUP',
              'SYSDG','SYSKM','PUBLIC','FLOWS_FILES','RDSADMIN','GGSYS','REMOTE_SCHEDULER_AGENT')
  AND owner NOT LIKE 'APEX\_%' ESCAPE '\'
```

Schema APEX próprio de aplicação **não** deve ser excluído — só os schemas internos do produto
(`APEX_######`). Integração com LLM via `APEX_WEB_SERVICE` é caso comum.

---

### 2.2 Passada de alta confiança

Rode esta primeiro, sempre. É seletiva e raramente pesa.

```sql
SELECT owner, name, type, COUNT(*) AS ocorrencias,
       MIN(line) AS primeira_linha, MAX(line) AS ultima_linha
  FROM dba_source
 WHERE owner IN (SELECT username FROM dba_users WHERE oracle_maintained = 'N')
   AND REGEXP_LIKE(text,
       'api\.openai\.com|openai\.azure\.com|cognitiveservices\.azure\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|aiplatform\.googleapis\.com|bedrock-runtime|inference\.generativeai|api\.cohere|api\.mistral\.ai|api\.deepseek\.com|api\.groq\.com|api\.x\.ai|api\.perplexity\.ai|openrouter\.ai|api\.replicate\.com|huggingface\.co|/v1/chat/completions|/v1/embeddings|/v1/messages|/v1/responses|/openai/deployments|:generateContent|dbms_vector|dbms_cloud_ai|vector_embedding|vector_distance|ai_chat',
       'i')
 GROUP BY owner, name, type
 ORDER BY 4 DESC
```

Qualquer linha aqui é **Confirmado** ou **Provável** — nenhum desses padrões aparece por acaso.

---

### 2.3 Passada de contexto

Habilitadores no código. Sozinhos não provam nada; cruzados com 2.2 ou com a ACL do mesmo
owner, fecham o circuito.

```sql
SELECT owner, name, type, COUNT(*) AS ocorrencias
  FROM dba_source
 WHERE owner IN (SELECT username FROM dba_users WHERE oracle_maintained = 'N')
   AND REGEXP_LIKE(text,
       'utl_http|apex_web_service|dbms_cloud\.send_request|httpuritype|utl_tcp|dbms_java',
       'i')
 GROUP BY owner, name, type
 ORDER BY 4 DESC
```

Em banco com integração REST legítima isso devolve muita coisa. Não leve tudo para o relatório:
leve o **cruzamento** — owners que aparecem aqui **e** têm ACL para host de IA na seção 1.

---

### 2.4 Passada ampla

Ruidosa. Rode **fora do pico** e só depois das duas anteriores. Faz full scan de `DBA_SOURCE`.

```sql
SELECT owner, name, type, COUNT(*) AS ocorrencias
  FROM dba_source
 WHERE owner IN (SELECT username FROM dba_users WHERE oracle_maintained = 'N')
   AND REGEXP_LIKE(text,
       'openai|anthropic|claude|gemini|bedrock|cohere|mistral|deepseek|ollama|llm|gpt-|embedding|chatbot|copilot|rag_|prompt',
       'i')
 GROUP BY owner, name, type
 ORDER BY 4 DESC
```

Antes de rodar, meça o custo:

```sql
SELECT COUNT(*) AS linhas_de_codigo, COUNT(DISTINCT owner||'.'||name) AS objetos
  FROM dba_source
 WHERE owner IN (SELECT username FROM dba_users WHERE oracle_maintained = 'N')
```

Acima de alguns milhões de linhas, avise o usuário e sugira janela específica.

Espere falso positivo: `prompt` em sistema de atendimento, `claude` em nome de pessoa,
`assistant` em cargo. Todo descarte vai para a aba **Ruído descartado** com o motivo.

---

### 2.5 Detalhe de um objeto

Só para os objetos que a triagem selecionou — nunca em massa.

```sql
SELECT line, <MASCARA>(text) AS texto
  FROM dba_source
 WHERE owner = :owner AND name = :objeto AND type = :tipo
   AND line BETWEEN :inicio AND :fim
 ORDER BY line
```

Ou só as linhas que casaram, com contexto mínimo:

```sql
SELECT line, <MASCARA>(text) AS texto
  FROM dba_source
 WHERE owner = :owner AND name = :objeto
   AND REGEXP_LIKE(text, :padrao, 'i')
 ORDER BY line
```

---

### 2.6 Segredos hardcoded

```sql
SELECT owner, name, type, line
  FROM dba_source
 WHERE owner IN (SELECT username FROM dba_users WHERE oracle_maintained = 'N')
   AND REGEXP_LIKE(text,
       'sk-[a-z]*-?[a-z0-9_-]{16,}|gsk_[a-z0-9]{20,}|xai-[a-z0-9]{16,}|hf_[a-z0-9]{16,}|AIza[a-z0-9_-]{30,}|AKIA[a-z0-9]{12,}|(bearer|api[-_]?key)[''" :=]+[a-z0-9_-]{16,}',
       'i')
 ORDER BY owner, name, line
```

Note que a projeção **não inclui o texto**. É deliberado. Só `owner`, `name`, `type`, `line` —
o suficiente para o DBA ir olhar, sem replicar o segredo.

---

### 2.7 Objetos criados recentemente

Shadow AI é recente por definição. Um objeto de integração criado nos últimos meses, num schema
de aplicação antigo, destaca-se sozinho.

```sql
SELECT owner, object_name, object_type,
       TO_CHAR(created,'YYYY-MM-DD')       AS criado_em,
       TO_CHAR(last_ddl_time,'YYYY-MM-DD') AS ultimo_ddl, status
  FROM dba_objects
 WHERE owner IN (SELECT username FROM dba_users WHERE oracle_maintained = 'N')
   AND object_type IN ('PACKAGE','PACKAGE BODY','PROCEDURE','FUNCTION','JOB','CREDENTIAL','TABLE','VIEW')
   AND created > SYSDATE - 365
 ORDER BY created DESC
```

Cruze com os nomes: `%AI%`, `%GPT%`, `%LLM%`, `%CHAT%`, `%EMBED%`, `%VECTOR%`, `%PROMPT%`,
`%RAG%`, `%OPENAI%`, `%ASSIST%`.

```sql
SELECT owner, object_name, object_type, TO_CHAR(created,'YYYY-MM-DD') AS criado_em
  FROM dba_objects
 WHERE owner IN (SELECT username FROM dba_users WHERE oracle_maintained = 'N')
   AND REGEXP_LIKE(object_name, 'GPT|LLM|OPENAI|ANTHROPIC|GEMINI|BEDROCK|EMBED|VECTOR|PROMPT|CHATBOT|COPILOT|RAG|_IA_|_AI_|^IA_|^AI_', 'i')
 ORDER BY created DESC
```

---

### 2.8 Tabelas de configuração

Muita integração guarda o endpoint numa tabela de parâmetros em vez de no código — nesse caso
2.2 não acha nada. Localize as candidatas pelos nomes de coluna:

```sql
SELECT owner, table_name, column_name, data_type
  FROM dba_tab_columns
 WHERE owner IN (SELECT username FROM dba_users WHERE oracle_maintained = 'N')
   AND REGEXP_LIKE(column_name, 'ENDPOINT|API_KEY|APIKEY|TOKEN|BASE_URL|MODEL|PROMPT|EMBEDDING|TEMPERATURE|MAX_TOKENS', 'i')
 ORDER BY owner, table_name, column_name
```

Encontrando candidatas, consulte **só as colunas não sensíveis** (endpoint, modelo, data) —
nunca a coluna de chave. Se precisar confirmar que há chave preenchida, use
`CASE WHEN api_key IS NULL THEN 'vazio' ELSE 'preenchido' END`, não o valor.

---

### 2.9 Cursor cache

Mostra o que **rodou de fato**, e não apenas o que está escrito. Janela curta (o cache recicla),
mas é evidência de execução.

```sql
SELECT sql_id, parsing_schema_name, module,
       executions,
       TO_CHAR(last_active_time,'YYYY-MM-DD HH24:MI') AS ultima_exec,
       SUBSTR(<MASCARA>(sql_text),1,300) AS sql
  FROM v$sql
 WHERE REGEXP_LIKE(sql_text,
       'dbms_vector|dbms_cloud_ai|vector_distance|vector_embedding|to_vector|select ai |utl_http|apex_web_service|openai|anthropic|embedding',
       'i')
 ORDER BY last_active_time DESC
```

`V$SQL.SQL_TEXT` contém literais de negócio. Sempre truncado, sempre mascarado, nunca colado
bruto no relatório.

Com Diagnostic Pack, a mesma busca em `DBA_HIST_SQLTEXT` cobre meses em vez de horas — ver o
item 4.7. **Sem pack, não use.**

---

### 2.10 Views e triggers

`DBA_SOURCE` cobre package, procedure, function, type, trigger e java source. Views ficam de
fora — `DBA_VIEWS.TEXT` é `LONG` e não aceita `REGEXP_LIKE` direto. Contorne pelo tamanho:

```sql
SELECT owner, view_name, text_length
  FROM dba_views
 WHERE owner IN (SELECT username FROM dba_users WHERE oracle_maintained = 'N')
   AND REGEXP_LIKE(view_name, 'GPT|LLM|AI|EMBED|VECTOR|PROMPT|CHAT', 'i')
 ORDER BY owner, view_name
```

E confirme as poucas candidatas com `DBMS_METADATA.GET_DDL('VIEW', :nome, :owner)` — é função
de leitura, segura. Se `DBA_SOURCE` desta versão não trouxer `TRIGGER`, verifique:

```sql
SELECT DISTINCT type FROM dba_source
```

Se `TRIGGER` não aparecer, faça a mesma triagem por nome em `DBA_TRIGGERS` e confirme com
`DBMS_METADATA`.

---

## Vetor 3 — Recursos nativos de IA do Oracle

Contém também o **Passo 0 — descoberta de superfície**, que roda antes de tudo.

Distinção que o relatório precisa preservar: recurso nativo usado dentro do banco (vetor, ONNX,
OML) **não manda dado para fora** — o processamento é local. É risco de governança, licença e
arquitetura, não de vazamento. `DBMS_CLOUD_AI` / Select AI é a exceção: chama provedor externo.

Índice:
- [3.0 Descoberta de superfície](#30-descoberta-de-superfície)
- [3.1 Uso histórico de features](#31-uso-histórico-de-features)
- [3.2 Tipo VECTOR e índices vetoriais](#32-tipo-vector-e-índices-vetoriais)
- [3.3 Modelos in-database](#33-modelos-in-database)
- [3.4 Select AI](#34-select-ai)
- [3.5 Grants nos pacotes de IA](#35-grants-nos-pacotes-de-ia)
- [3.6 Text e busca semântica](#36-text-e-busca-semântica)
- [3.7 APEX e ORDS](#37-apex-e-ords)

---

### 3.0 Descoberta de superfície

**Rode isto primeiro, sempre.** Diz quais objetos de IA existem nesta versão e evita uma
sequência de `ORA-00942` nas seções seguintes.

```sql
SELECT object_name, object_type, status,
       TO_CHAR(created,'YYYY-MM-DD') AS criado_em
  FROM dba_objects
 WHERE owner = 'SYS'
   AND object_type IN ('PACKAGE','VIEW','SYNONYM','FUNCTION','TYPE')
   AND (object_name LIKE '%VECTOR%'
     OR object_name LIKE '%CLOUD_AI%'
     OR object_name LIKE 'DBMS_CLOUD%'
     OR object_name LIKE '%MINING_MODEL%'
     OR object_name LIKE '%ONNX%'
     OR object_name LIKE '%AI_PROFILE%')
 ORDER BY object_type, object_name
```

Versão exata, que determina o resto:

```sql
SELECT banner FROM v$version
```

```sql
SELECT parameter, value FROM v$option
 WHERE UPPER(parameter) LIKE '%MINING%'
    OR UPPER(parameter) LIKE '%VECTOR%'
    OR UPPER(parameter) LIKE '%MACHINE LEARNING%'
    OR UPPER(parameter) LIKE '%ADVANCED ANALYTICS%'
```

Se a versão for 19c ou anterior, as seções 3.2 e 3.4 tendem a voltar vazias — isso é resultado
esperado e vai para **Lacunas da coleta**, não para "nada encontrado".

---

### 3.1 Uso histórico de features

A view mais custo-benefício de todo o levantamento: histórico de meses ou anos, **sem exigir
Diagnostic Pack**, com data do primeiro e do último uso.

```sql
SELECT name, detected_usages, total_samples, currently_used, version,
       TO_CHAR(first_usage_date,'YYYY-MM-DD') AS primeiro_uso,
       TO_CHAR(last_usage_date,'YYYY-MM-DD')  AS ultimo_uso,
       SUBSTR(feature_info,1,300) AS detalhe
  FROM dba_feature_usage_statistics
 WHERE detected_usages > 0
   AND (UPPER(name) LIKE '%VECTOR%'
     OR UPPER(name) LIKE '%MINING%'
     OR UPPER(name) LIKE '%MACHINE LEARNING%'
     OR UPPER(name) LIKE '%ANALYTIC%'
     OR UPPER(name) LIKE '%JAVA%'
     OR UPPER(name) LIKE '%HTTP%'
     OR UPPER(name) LIKE '%CLOUD%'
     OR UPPER(name) LIKE '%JSON%'
     OR UPPER(name) LIKE '%TEXT%')
 ORDER BY last_usage_date DESC NULLS LAST
```

Vale rodar sem filtro também — a lista inteira tem algumas centenas de linhas e mostra o que a
instalação realmente exercita:

```sql
SELECT name, detected_usages, currently_used,
       TO_CHAR(first_usage_date,'YYYY-MM-DD') AS primeiro_uso,
       TO_CHAR(last_usage_date,'YYYY-MM-DD')  AS ultimo_uso
  FROM dba_feature_usage_statistics
 WHERE detected_usages > 0
 ORDER BY last_usage_date DESC NULLS LAST
```

Como ler:

- `FIRST_USAGE_DATE` responde **"desde quando"** — a coluna que transforma achado em linha do
  tempo. Use no relatório.
- `CURRENTLY_USED = TRUE` significa detectado na última amostragem (semanal). É o mais próximo
  de "está em uso agora" sem AWR.
- A amostragem é semanal: uso pontual entre duas coletas **não aparece**. Ausência aqui não é
  prova de ausência de uso.
- A mesma view é o que a Oracle olha em auditoria de licenciamento. Se algum recurso licenciado
  à parte estiver com `detected_usages > 0`, isso é exposição contratual e merece parágrafo
  próprio — independente de IA.

---

### 3.2 Tipo VECTOR e índices vetoriais

Só 23ai. Em versões anteriores retornam zero linhas sem erro.

```sql
SELECT owner, table_name, column_name, data_type, data_precision, data_scale
  FROM dba_tab_cols
 WHERE data_type = 'VECTOR'
 ORDER BY owner, table_name, column_name
```

Há dado guardado?

```sql
SELECT owner, table_name, num_rows,
       TO_CHAR(last_analyzed,'YYYY-MM-DD') AS ultima_analise
  FROM dba_tables
 WHERE (owner, table_name) IN (SELECT owner, table_name FROM dba_tab_cols WHERE data_type = 'VECTOR')
 ORDER BY num_rows DESC NULLS LAST
```

Coluna `VECTOR` existente e vazia é projeto iniciado; populada é sistema em produção. Distinga.

```sql
SELECT owner, index_name, table_owner, table_name, index_type, status
  FROM dba_indexes
 WHERE index_type LIKE '%VECTOR%'
 ORDER BY owner, index_name
```

Se o Passo 0 mostrou `DBA_VECTOR_INDEXES` ou `DBA_VECTOR_MODELS`, consulte com `SELECT *` — as
colunas variam entre releases do 23ai e não vale arriscar `ORA-00904`.

---

### 3.3 Modelos in-database

Cobre OML clássico e modelos ONNX importados (incluindo modelos de embedding rodando dentro do
banco).

```sql
SELECT owner, model_name, mining_function, algorithm,
       TO_CHAR(creation_date,'YYYY-MM-DD') AS criado_em
  FROM dba_mining_models
 ORDER BY creation_date DESC
```

Se a versão suportar ONNX, os modelos importados aparecem aqui com algoritmo indicando ONNX ou
função de embedding. Havendo colunas adicionais na sua versão (`model_size`, `algorithm_type`),
use `SELECT *` num modelo só para inspecionar.

Atributos e configuração de um modelo específico:

```sql
SELECT model_name, attribute_name, attribute_type, target
  FROM dba_mining_model_attributes
 WHERE model_name = :modelo
 ORDER BY attribute_name
```

**Licença:** Oracle Machine Learning / Advanced Analytics é licenciado à parte em várias
edições. Modelo existindo em banco sem a licença é achado de conformidade, e frequentemente o
de maior custo financeiro do relatório inteiro. Verifique em `V$OPTION` (Passo 0) e reporte o
fato sem afirmar a situação contratual — você não conhece o contrato.

---

### 3.4 Select AI

Só em Autonomous Database e 23ai. Confirme no Passo 0 que as views existem antes de rodar.

```sql
SELECT * FROM dba_cloud_ai_profiles
```

```sql
SELECT profile_name, attribute_name, attribute_value
  FROM dba_cloud_ai_profile_attributes
 ORDER BY profile_name, attribute_name
```

Esta é a evidência mais direta que existe neste levantamento: os atributos trazem **provedor,
modelo, credencial e lista de objetos expostos** ao LLM. Um profile ativo significa que
qualquer usuário com acesso pode rodar `SELECT AI ...` e enviar esquema e dados para fora.

Ao reportar, destaque três atributos: o provedor, a credencial (cruze com `1.4`) e o conjunto
de tabelas/views declarado — é o escopo exato do que está exposto.

Se as views não existirem, também tente `USER_CLOUD_AI_PROFILES` conectado a cada schema
relevante, e registre a limitação.

---

### 3.5 Grants nos pacotes de IA

Quem pode usar, mesmo que ainda não use.

```sql
SELECT grantee, table_name AS pacote, privilege, grantor
  FROM dba_tab_privs
 WHERE privilege = 'EXECUTE'
   AND table_name IN ('DBMS_VECTOR','DBMS_VECTOR_CHAIN','DBMS_CLOUD_AI',
                      'DBMS_DATA_MINING','DBMS_PREDICTIVE_ANALYTICS','DBMS_CLOUD')
 ORDER BY table_name, grantee
```

```sql
SELECT grantee, privilege, admin_option
  FROM dba_sys_privs
 WHERE privilege LIKE '%MINING MODEL%'
    OR privilege LIKE '%VECTOR%'
 ORDER BY grantee
```

---

### 3.6 Text e busca semântica

Oracle Text com índice sobre coluna de texto costuma preceder um projeto de RAG. Não é IA por
si, mas situa o contexto.

```sql
SELECT owner, index_name, table_owner, table_name, index_type, status
  FROM dba_indexes
 WHERE ityp_name IS NOT NULL
   AND (ityp_name LIKE '%CONTEXT%' OR ityp_name LIKE '%CTX%' OR ityp_name LIKE '%VECTOR%')
 ORDER BY owner, index_name
```

---

### 3.7 APEX e ORDS

APEX moderno tem configuração de serviços de IA generativa por aplicação, e ORDS expõe endpoints
REST que um agente externo consome com facilidade.

Descubra o que existe nesta instalação antes de consultar:

```sql
SELECT owner, object_name, object_type
  FROM dba_objects
 WHERE owner LIKE 'APEX%'
   AND object_type = 'VIEW'
   AND (object_name LIKE '%AI%' OR object_name LIKE '%GENERATIVE%' OR object_name LIKE '%WEB_SERVICE%')
 ORDER BY object_name
```

Havendo views de serviço de IA, consulte com `SELECT *` — os nomes de coluna variam bastante
entre versões do APEX. Procure o provedor, o endpoint e quais aplicações têm o serviço ligado.

Web services declarados nas aplicações:

```sql
SELECT * FROM apex_application_web_services
```

Módulos REST publicados por ORDS:

```sql
SELECT parsing_schema, name, uri_prefix, status
  FROM user_ords_modules
```

(`USER_ORDS_*` precisa estar conectado ao schema; há equivalentes `DBA_ORDS_*` em instalações
mais recentes — confira no Passo 0.)

Um endpoint REST público sobre tabela sensível é Habilitador de primeira ordem: qualquer agente
de IA com a URL consome sem tocar no banco diretamente, e sem deixar rastro em `V$SESSION`.

---

## Vetor 4 — Acesso e extração

Quem e o quê se conecta ao banco, e quanto dado sai. Cobre o caso em que a IA não está no banco
— está do outro lado da conexão.

Índice:
- [4.1 Sessões agora](#41-sessões-agora)
- [4.2 Ferramentas de IA conectadas](#42-ferramentas-de-ia-conectadas)
- [4.3 Auditoria unificada](#43-auditoria-unificada)
- [4.4 Auditoria tradicional](#44-auditoria-tradicional)
- [4.5 Extração em massa](#45-extração-em-massa)
- [4.6 Contas e criação recente](#46-contas-e-criação-recente)
- [4.7 Com Diagnostic Pack](#47-com-diagnostic-pack)

Lembrete: se você está rodando por MCP do SQLcl, **sua sessão aparece aqui**. Identifique-a
antes de classificar qualquer coisa.

---

### 4.1 Sessões agora

Fotografia do instante. Barata e útil como panorama, mas sozinha é uma janela estreita — o
achado de verdade quase sempre está na auditoria (4.3).

```sql
SELECT username, osuser, machine, program, module,
       COUNT(*) AS sessoes,
       TO_CHAR(MIN(logon_time),'YYYY-MM-DD HH24:MI') AS mais_antiga
  FROM v$session
 WHERE type = 'USER'
   AND username IS NOT NULL
 GROUP BY username, osuser, machine, program, module
 ORDER BY 6 DESC
```

Em RAC, troque por `gv$session` e inclua `inst_id`.

---

### 4.2 Ferramentas de IA conectadas

Lista completa de padrões no Anexo A.

```sql
SELECT username, osuser, machine, program, module, client_info,
       TO_CHAR(logon_time,'YYYY-MM-DD HH24:MI') AS conectou_em
  FROM v$session
 WHERE type = 'USER'
   AND REGEXP_LIKE(LOWER(program||' '||NVL(module,' ')||' '||NVL(client_info,' ')),
       'python|jupyter|ipykernel|langchain|llama|haystack|mcp|sqlcl|sql\.exe|dbeaver|datagrip|cursor|windsurf|code\.exe|copilot|continue|cline|n8n|node|deno|bun')
 ORDER BY logon_time DESC
```

Leitura, em ordem de força:

1. **Programa de IA + máquina de usuário final + tabela sensível** — achado forte.
2. **Programa de IA + servidor de aplicação conhecido** — provavelmente a aplicação; verifique.
3. **IDE com copiloto** — Habilitador. O assistente vê o esquema e o resultado das consultas,
   mesmo sem uma linha de código de IA no banco.
4. **`python` em servidor de ETL** — quase sempre ruído. Descarte com o motivo.

`MODULE` e `CLIENT_INFO` são preenchidos pela aplicação e podem ser falsificados ou vir vazios.
Não são prova; são pista.

---

### 4.3 Auditoria unificada

12c+, e exige `AUDIT_VIEWER` ou `AUDIT_ADMIN` além de `SELECT_CATALOG_ROLE`. **Sempre com
filtro de data e sempre agregado** — a tabela chega a dezenas de milhões de linhas.

Antes, confirme que há o que consultar:

```sql
SELECT parameter, value FROM v$option WHERE parameter = 'Unified Auditing'
```

```sql
SELECT policy_name, enabled_option, entity_name, success, failure
  FROM audit_unified_enabled_policies
 ORDER BY policy_name
```

Se nada estiver habilitado além do padrão, diga no relatório: **a janela de visão histórica é
praticamente nula**, e isso por si é um achado de governança.

Panorama de programas cliente nos últimos 90 dias:

```sql
SELECT client_program_name, os_username, userhost, dbusername,
       COUNT(*) AS eventos,
       TO_CHAR(MIN(event_timestamp),'YYYY-MM-DD') AS primeiro,
       TO_CHAR(MAX(event_timestamp),'YYYY-MM-DD') AS ultimo
  FROM unified_audit_trail
 WHERE event_timestamp > SYSDATE - 90
 GROUP BY client_program_name, os_username, userhost, dbusername
 ORDER BY 5 DESC
```

Filtrado por ferramenta de IA:

```sql
SELECT client_program_name, os_username, userhost, dbusername,
       COUNT(*) AS eventos,
       TO_CHAR(MIN(event_timestamp),'YYYY-MM-DD') AS primeiro,
       TO_CHAR(MAX(event_timestamp),'YYYY-MM-DD') AS ultimo
  FROM unified_audit_trail
 WHERE event_timestamp > SYSDATE - 180
   AND REGEXP_LIKE(LOWER(client_program_name),
       'python|jupyter|langchain|llama|mcp|sqlcl|sql\.exe|dbeaver|datagrip|cursor|windsurf|code\.exe|node')
 GROUP BY client_program_name, os_username, userhost, dbusername
 ORDER BY 5 DESC
```

`MIN(event_timestamp)` responde "desde quando" — a informação que muda a conversa de "alguém
usa" para "isso começou em março".

Se a coleta ficar lenta, reduza para 30 dias e aumente aos poucos.

---

### 4.4 Auditoria tradicional

11g, ou 12c+ em modo misto. Só devolve algo se a auditoria estiver ligada.

```sql
SELECT value FROM v$parameter WHERE name = 'audit_trail'
```

```sql
SELECT username, os_username, userhost, client_id,
       COUNT(*) AS eventos,
       TO_CHAR(MIN(timestamp),'YYYY-MM-DD') AS primeiro,
       TO_CHAR(MAX(timestamp),'YYYY-MM-DD') AS ultimo
  FROM dba_audit_trail
 WHERE timestamp > SYSDATE - 90
 GROUP BY username, os_username, userhost, client_id
 ORDER BY 5 DESC
```

`DBA_AUDIT_TRAIL` não tem `CLIENT_PROGRAM_NAME`; o que há é `USERHOST` e `OS_USERNAME` —
identifica a máquina e o usuário do SO, não a ferramenta. Registre essa limitação.

---

### 4.5 Extração em massa

O achado mais próximo de vazamento real, com ou sem IA no circuito.

```sql
SELECT * FROM (
  SELECT sql_id, parsing_schema_name, module,
         executions, rows_processed,
         ROUND(rows_processed / NULLIF(executions,0)) AS linhas_por_exec,
         TO_CHAR(last_active_time,'YYYY-MM-DD HH24:MI') AS ultima_exec,
         SUBSTR(sql_text,1,200) AS sql
    FROM v$sql
   WHERE command_type = 3
     AND executions > 0
     AND rows_processed / NULLIF(executions,0) > 100000
   ORDER BY 6 DESC)
 WHERE ROWNUM <= 30
```

`command_type = 3` é `SELECT`. Ajuste o corte de 100 mil conforme o porte do banco.

Cruze com o módulo cliente:

```sql
SELECT * FROM (
  SELECT module, parsing_schema_name,
         COUNT(*) AS sqls,
         SUM(rows_processed) AS linhas_total,
         TO_CHAR(MAX(last_active_time),'YYYY-MM-DD HH24:MI') AS ultima_exec
    FROM v$sql
   WHERE command_type = 3
     AND module IS NOT NULL
   GROUP BY module, parsing_schema_name
   ORDER BY 4 DESC)
 WHERE ROWNUM <= 30
```

Um módulo não homologado no topo desta lista é o parágrafo mais importante do relatório.

Interpretação honesta: `SELECT *` de tabela inteira é o que todo ETL noturno faz. O que
distingue é o **conjunto**: módulo não homologado **e** máquina de usuário final **e** tabela
sensível **e** horário fora do padrão. Um fator isolado é ruído.

Truncar e mascarar o `sql_text` antes de levar ao relatório — ver a regra de
mascaramento no Vetor 2.

---

### 4.6 Contas e criação recente

```sql
SELECT username, account_status, authentication_type, profile,
       TO_CHAR(created,'YYYY-MM-DD')         AS criado_em,
       TO_CHAR(last_login,'YYYY-MM-DD HH24:MI') AS ultimo_login
  FROM dba_users
 WHERE oracle_maintained = 'N'
 ORDER BY created DESC
```

`LAST_LOGIN` existe a partir do 12c. Conta de serviço criada nos últimos meses, com nome de
ferramenta ou de integração, é um bom fio a puxar — cruze com 4.3 e com as credenciais de 1.4.

Perfis e privilégios amplos:

```sql
SELECT grantee, granted_role, admin_option, default_role
  FROM dba_role_privs
 WHERE granted_role IN ('DBA','SELECT_CATALOG_ROLE','EXP_FULL_DATABASE','DATAPUMP_EXP_FULL_DATABASE')
   AND grantee NOT IN ('SYS','SYSTEM')
 ORDER BY granted_role, grantee
```

`EXP_FULL_DATABASE` numa conta de aplicação é capacidade de exportar o banco inteiro.

---

### 4.7 Com Diagnostic Pack

**Só execute se a Pergunta 3 do Passo 1 foi respondida com "Sim".** `DBA_HIST_*` e
`V$ACTIVE_SESSION_HISTORY` exigem licença, e o uso fica registrado em
`DBA_FEATURE_USAGE_STATISTICS` — ou seja, deixa rastro auditável.

Histórico de SQL com marcadores de IA, cobrindo meses:

```sql
SELECT s.sql_id, t.sql_text,
       TO_CHAR(MIN(s.begin_interval_time),'YYYY-MM-DD') AS primeira,
       TO_CHAR(MAX(s.begin_interval_time),'YYYY-MM-DD') AS ultima,
       SUM(st.executions_delta) AS execucoes
  FROM dba_hist_sqltext t
  JOIN dba_hist_sqlstat st ON st.sql_id = t.sql_id AND st.dbid = t.dbid
  JOIN dba_hist_snapshot s ON s.snap_id = st.snap_id AND s.dbid = st.dbid
 WHERE REGEXP_LIKE(t.sql_text,
       'dbms_vector|dbms_cloud_ai|vector_distance|vector_embedding|select ai |utl_http|apex_web_service|openai|anthropic', 'i')
 GROUP BY s.sql_id, t.sql_text
 ORDER BY 4 DESC
```

Programas cliente ao longo do tempo:

```sql
SELECT program, module, machine,
       COUNT(*) AS amostras,
       TO_CHAR(MIN(sample_time),'YYYY-MM-DD') AS primeira,
       TO_CHAR(MAX(sample_time),'YYYY-MM-DD') AS ultima
  FROM dba_hist_active_sess_history
 WHERE sample_time > SYSDATE - 90
   AND REGEXP_LIKE(LOWER(program||' '||NVL(module,' ')),
       'python|jupyter|langchain|llama|mcp|sqlcl|dbeaver|datagrip|cursor|code\.exe|node')
 GROUP BY program, module, machine
 ORDER BY 4 DESC
```

Sem Diagnostic Pack, as alternativas são `DBA_FEATURE_USAGE_STATISTICS` (seção 3.1),
`UNIFIED_AUDIT_TRAIL` (4.3), `DBA_OBJECTS.CREATED` (2.7) e a coleta periódica desta própria
skill, comparando execuções. Nenhuma custa licença.

---

## Anexo A — Catálogo de indicadores

Fonte única dos padrões usados nas seções 1 e 2. Leia antes de montar qualquer `REGEXP_LIKE`.

Índice:
- [Hosts de provedores de IA](#hosts-de-provedores-de-ia)
- [Endpoints e caminhos de API](#endpoints-e-caminhos-de-api)
- [Prefixos de chave de API](#prefixos-de-chave-de-api)
- [Pacotes Oracle que permitem saída ou IA](#pacotes-oracle-que-permitem-saída-ou-ia)
- [Palavras-chave de aplicação](#palavras-chave-de-aplicação)
- [Programas cliente de IA](#programas-cliente-de-ia)
- [Regex prontos](#regex-prontos)

---

### Hosts de provedores de IA

| Provedor | Host típico | Ambiguidade |
|---|---|---|
| OpenAI | `api.openai.com` | baixa — achado direto |
| Azure OpenAI | `<nome>.openai.azure.com`, `<nome>.cognitiveservices.azure.com` | baixa |
| Anthropic | `api.anthropic.com` | baixa |
| Google Gemini | `generativelanguage.googleapis.com`, `aiplatform.googleapis.com` | baixa |
| AWS Bedrock | `bedrock-runtime.<região>.amazonaws.com`, `bedrock.<região>.amazonaws.com` | baixa |
| OCI GenAI | `inference.generativeai.<região>.oci.oraclecloud.com` | baixa |
| Cohere | `api.cohere.ai`, `api.cohere.com` | baixa |
| Mistral | `api.mistral.ai` | baixa |
| DeepSeek | `api.deepseek.com` | baixa |
| Groq | `api.groq.com` | baixa |
| xAI | `api.x.ai` | baixa |
| Perplexity | `api.perplexity.ai` | baixa |
| OpenRouter | `openrouter.ai` | baixa |
| Together | `api.together.xyz`, `api.together.ai` | baixa |
| Replicate | `api.replicate.com` | baixa |
| Hugging Face | `api-inference.huggingface.co`, `huggingface.co` | média — também é repositório de modelo |
| LLM local | `localhost`, `127.0.0.1`, `ollama`, `:11434`, `:1234`, `:8000` | **alta** — ver nota |
| Automação | `hooks.zapier.com`, `hook.<us\|eu>.make.com`, `n8n` | média — orquestrador, IA no outro lado |

**Genéricos que geram falso positivo:** `amazonaws.com`, `azure.com`, `googleapis.com`,
`oraclecloud.com` isolados quase sempre são backup, storage ou serviço já contratado. Só marque
quando o subdomínio bate com a coluna do meio. O host `*` é achado à parte, independente de IA.

**LLM local (`localhost`/`127.0.0.1`):** ambíguo por natureza — a maioria é app server ou ORDS
no mesmo host. Vira indicador só se a porta bater com runtime conhecido (11434 Ollama, 1234
LM Studio, 8080/8000 genérico) **e** houver código com vocabulário de IA apontando para ela.
Anote como Provável, nunca como Confirmado sem o segundo sinal.

---

### Endpoints e caminhos de API

Aparecem no PL/SQL mesmo quando o host está numa variável ou numa tabela de parâmetros — por
isso são melhores que o host para varrer código.

```
/v1/chat/completions      /v1/completions        /v1/embeddings
/v1/messages              /v1/responses          /v1/models
/openai/deployments/      :generateContent       /converse
/embed                    /rerank                /api/generate
```

`/v1/messages` e `:generateContent` são muito específicos. `/v1/models` e `/embed` são
genéricos e precisam de contexto.

---

### Prefixos de chave de API

Usados para achar segredo hardcoded. **Nunca imprima o valor encontrado** — ver a seção de
mascaramento no Vetor 2.

```
sk-           sk-proj-      sk-ant-       sk-or-v1-
AIza          gsk_          xai-          hf_
r8_           csk-          Bearer
```

`AIza` é Google, `gsk_` é Groq, `hf_` é Hugging Face, `r8_` é Replicate. Chaves de Azure OpenAI
e Bedrock **não têm prefixo** — Azure é hexadecimal de 32 caracteres e Bedrock usa credencial
AWS (`AKIA...`), então busque também por `api-key`, `AKIA`, `aws_secret`.

---

### Pacotes Oracle que permitem saída ou IA

**Saída de rede** — pré-requisito para qualquer chamada a LLM a partir do banco:

```
UTL_HTTP            APEX_WEB_SERVICE      DBMS_CLOUD (SEND_REQUEST)
UTL_TCP             UTL_SMTP              UTL_INADDR      UTL_URL
DBMS_LDAP           HTTPURITYPE           DBMS_XMLSTORE
```

`UTL_HTTP` e `APEX_WEB_SERVICE` respondem pela quase totalidade dos casos reais.
`HTTPURITYPE` é o caminho esquecido: funciona em SQL puro e é frequentemente ignorado em
revisão de segurança.

**IA nativa Oracle** (existência varia por versão — confirme no passo de descoberta):

```
DBMS_VECTOR         DBMS_VECTOR_CHAIN     DBMS_CLOUD_AI
DBMS_DATA_MINING    DBMS_PREDICTIVE_ANALYTICS
VECTOR_EMBEDDING    VECTOR_DISTANCE       TO_VECTOR       AI_CHAT
```

`VECTOR_DISTANCE`, `VECTOR_EMBEDDING` e `TO_VECTOR` são funções SQL — aparecem no cursor cache,
não só no PL/SQL. `SELECT AI` é sintaxe, não pacote: busque a string `select ai ` no SQL.

**Execução externa** — permite `curl` para uma API sem passar por `UTL_HTTP`:

```
DBMS_SCHEDULER (job_type EXECUTABLE / EXTERNAL_SCRIPT)
DBMS_JAVA / SocketPermission
DBMS_PIPE + listener externo
```

---

### Palavras-chave de aplicação

Ruidosas. Use só na passada ampla e sempre com triagem manual depois.

```
prompt        completion     embedding      token_count    temperature
chatbot       copilot        assistant      llm            gpt
inferencia    inferência     sumarizacao    sumarização    ia_
openai_       anthropic_     vetor          semantic       rag_
```

`prompt`, `token`, `assistant` e `temperature` existem em sistemas anteriores à IA generativa.
`rag_`, `embedding`, `llm` e `gpt` são bem mais discriminantes.

---

### Programas cliente de IA

Para `V$SESSION.PROGRAM` / `MODULE` e `UNIFIED_AUDIT_TRAIL.CLIENT_PROGRAM_NAME`:

```
python          python3         jupyter        ipykernel
langchain       llamaindex      llama_index    haystack
mcp             sqlcl           sql.exe        oracle-mcp
dbeaver         datagrip        cursor         windsurf
code.exe        copilot         continue       cline
n8n             node            deno           bun
```

Notas de leitura:

- `python` sozinho não é IA. É indicador quando aparece em máquina de usuário final (e não em
  servidor de aplicação), em horário irregular, ou com volume de extração alto.
- `sql.exe` / `sqlcl` é o binário do SQLcl — **e é também o que o MCP do SQLcl usa**. Se a
  própria coleta está rodando por MCP, a sessão que você vê pode ser a sua.
- IDE com copiloto (`dbeaver`, `datagrip`, `cursor`, `code.exe`) não prova envio de dado a LLM,
  mas coloca o esquema e os resultados ao alcance de um assistente. Classifique como
  Habilitador salvo evidência melhor.

---

### Regex prontos

Cole direto. Ajuste conforme o resultado da triagem.

**Alta confiança — host de provedor** (para `DBA_NETWORK_ACLS.HOST` e `DBA_SOURCE.TEXT`):

```
api\.openai\.com|openai\.azure\.com|cognitiveservices\.azure\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|aiplatform\.googleapis\.com|bedrock-runtime|inference\.generativeai|api\.cohere|api\.mistral\.ai|api\.deepseek\.com|api\.groq\.com|api\.x\.ai|api\.perplexity\.ai|openrouter\.ai|api\.together|api\.replicate\.com|huggingface\.co
```

**Alta confiança — endpoint, chave e função de IA** (para código e SQL):

```
/v1/chat/completions|/v1/embeddings|/v1/messages|/v1/responses|/openai/deployments|:generatecontent|sk-ant-|sk-proj-|sk-or-v1-|gsk_|xai-|dbms_vector|dbms_cloud_ai|vector_embedding|vector_distance|ai_chat
```

**Contexto — habilitadores** (só faz sentido junto com um sinal acima):

```
utl_http|apex_web_service|dbms_cloud\.send_request|httpuritype|utl_tcp|dbms_java
```

**Ampla — vocabulário de aplicação** (ruidosa; só fora do pico e com triagem):

```
openai|anthropic|claude|gemini|bedrock|cohere|mistral|deepseek|ollama|llm|gpt-|embedding|chatbot|copilot|rag_|prompt
```

Em `REGEXP_LIKE` sempre aplique `LOWER()` na coluna e mantenha o padrão em minúsculas, ou passe
`'i'` como terceiro argumento.
