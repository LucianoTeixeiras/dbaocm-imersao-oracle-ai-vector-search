--------------------------------------------------------------------------------
-- SELECT AI - DEMONSTRACAO COM O SCHEMA HR
--------------------------------------------------------------------------------
-- Ambiente : Autonomous Database 23ai (imersao-set-26ai) - sa-saopaulo-1
-- Schema   : HR (Oracle sample schema 19.2 - 7 tabelas, 107 funcionarios)
-- Provedor : OpenAI
--
-- ESTE SCRIPT FOI EXECUTADO E VALIDADO NESTE BANCO. Os comentarios marcados
-- com [TESTADO] registram o comportamento real observado, nao a teoria.
--
-- ORDEM DE EXECUCAO: PARTE 1 como ADMIN, PARTE 2 nos dois, resto como HR.
--------------------------------------------------------------------------------
--
-- DUAS REGRAS QUE DEFINEM TODO O DESENHO (ambas verificadas na pratica):
--
-- REGRA 1 - A ACL de rede e checada contra o USUARIO DA SESSAO, nao contra o
--           dono do pacote DBMS_CLOUD_AI. Consequencia: TODO usuario que for
--           chamar o Select AI precisa da sua propria ACE, inclusive o ADMIN.
--           [TESTADO] Sem a ACE, o ADMIN recebeu:
--              ORA-20000: ORA-24247: Network access denied by access control list
--
-- REGRA 2 - O credential_name declarado no profile e resolvido no schema de
--           QUEM EXECUTA, nao no schema dono do profile. Consequencia: mesmo
--           com DBMS_CLOUD_AI.GRANT_PROFILE_ACCESS, cada usuario precisa ter
--           uma credencial LOCAL com o MESMO NOME que o profile referencia.
--           [TESTADO] Com o profile ADMIN.HR_OPENAI apontando para OPENAI_CRED
--           e o HR sem credencial propria, o HR recebeu:
--              ORA-20004: Credential "HR"."OPENAI_CRED" does not exist
--           Nao existe GRANT_CREDENTIAL neste ADB - credencial nao se
--           compartilha entre schemas. A chave precisa existir nos dois lados.
--------------------------------------------------------------------------------

SET SERVEROUTPUT ON
SET LINESIZE 200
SET PAGESIZE 100


--------------------------------------------------------------------------------
-- PARTE 1 - HABILITACAO (EXECUTAR CONECTADO COMO **ADMIN**)
--------------------------------------------------------------------------------

-- 1.1 Permitir que o HR crie credencial e use o Select AI
GRANT EXECUTE ON DBMS_CLOUD    TO hr;   -- necessario para CREATE_CREDENTIAL
GRANT EXECUTE ON DBMS_CLOUD_AI TO hr;   -- necessario para SET_PROFILE / SELECT AI

-- OBS: DBMS_CLOUD_PIPELINE NAO e usado pelo Select AI (serve para pipelines de
-- carga/exportacao continua). Nao conceda sem necessidade.

-- 1.2 Conferir os grants concedidos ao HR
--     NAO filtre por table_name = 'DBMS_CLOUD'. Neste ADB o sinonimo publico
--     DBMS_CLOUD aponta para C##CLOUD$SERVICE.DBMS_CLOUD$PDBCS_260904_0 - o nome
--     real carrega sufixo de versao e muda a cada release. O filtro literal
--     esconde um grant que de fato existe. A consulta abaixo resolve o sinonimo.
SELECT s.synonym_name AS pacote,
       p.table_name   AS objeto_real,
       p.privilege,
       p.grantor
  FROM dba_synonyms s
  JOIN dba_tab_privs p
    ON p.owner      = s.table_owner
   AND p.table_name = s.table_name
 WHERE s.owner = 'PUBLIC'
   AND s.synonym_name IN ('DBMS_CLOUD', 'DBMS_CLOUD_AI')
   AND p.grantee = 'HR'
 ORDER BY s.synonym_name;
-- Esperado: 2 linhas, ambas com privilegio EXECUTE.

-- 1.3 Liberar saida HTTP para a API da OpenAI - PARA CADA USUARIO QUE VAI USAR
--     Ver REGRA 1 no cabecalho. O ADMIN tambem precisa.
BEGIN
  DBMS_NETWORK_ACL_ADMIN.APPEND_HOST_ACE(
    host => 'api.openai.com',
    ace  => xs$ace_type(privilege_list => xs$name_list('http'),
                        principal_name  => 'HR',
                        principal_type  => xs_acl.ptype_db)
  );
END;
/

BEGIN
  DBMS_NETWORK_ACL_ADMIN.APPEND_HOST_ACE(
    host => 'api.openai.com',
    ace  => xs$ace_type(privilege_list => xs$name_list('http'),
                        principal_name  => 'ADMIN',
                        principal_type  => xs_acl.ptype_db)
  );
END;
/

-- 1.4 Conferir a ACL
SELECT a.host, p.principal, p.privilege, p.is_grant
  FROM dba_network_acls a
  JOIN dba_network_acl_privileges p ON p.acl = a.acl
 WHERE a.host = 'api.openai.com'
 ORDER BY p.principal;
-- Esperado: ADMIN e HR, ambos com http.

-- 1.5 Conferir que o schema HR esta populado e com estatisticas atualizadas.
--     O Select AI monta o prompt a partir do dicionario (colunas, tipos, FKs,
--     comentarios). Schema sem estatistica e sem comentario gera SQL pior.
SELECT table_name, num_rows, last_analyzed
  FROM dba_tables
 WHERE owner = 'HR'
   AND table_name IN ('REGIONS','COUNTRIES','LOCATIONS','DEPARTMENTS',
                      'JOBS','EMPLOYEES','JOB_HISTORY')
 ORDER BY num_rows;
-- Esperado: REGIONS 4, JOB_HISTORY 10, JOBS 19, LOCATIONS 23, COUNTRIES 25,
--           DEPARTMENTS 27, EMPLOYEES 107


--------------------------------------------------------------------------------
-- PARTE 2 - CREDENCIAL E AI PROFILE
--------------------------------------------------------------------------------
-- Desenho adotado: profile UNICO no ADMIN, compartilhado com o HR.
-- Vantagem: o HR nao pode alterar o object_list, ou seja, nao amplia por conta
-- propria o conjunto de tabelas que a IA enxerga. O escopo fica com o ADMIN.
-- Limitacao (REGRA 2): a credencial precisa existir nos DOIS schemas, com o
-- mesmo nome. Nao ha ganho de sigilo da chave - ha ganho de controle de escopo.
--------------------------------------------------------------------------------

-- ---------- 2.1 Como ADMIN ----------

BEGIN
  DBMS_CLOUD.CREATE_CREDENTIAL(
    credential_name => 'OPENAI_CRED',
    username        => 'OPENAI',
    password        => '<SUA_API_KEY_DA_OPENAI>'
  );
END;
/

BEGIN
DBMS_CLOUD.CREATE_CREDENTIAL(
credential_name   => 'OPENAI_CRED', 
username          =>  'OPENAI', 
password          =>  '<OPENAI_API_KEY_REDACTED>'
);
END;
/


BEGIN
  DBMS_CLOUD_AI.CREATE_PROFILE(
    profile_name => 'HR_OPENAI',
    attributes   => '{"provider"        : "openai",
                      "credential_name" : "OPENAI_CRED",
                      "model"           : "gpt-5.5",
                      "comments"        : "true",
                      "conversation"    : "true",
                      "object_list"     : [{"owner": "HR", "name": "REGIONS"},
                                           {"owner": "HR", "name": "COUNTRIES"},
                                           {"owner": "HR", "name": "LOCATIONS"},
                                           {"owner": "HR", "name": "DEPARTMENTS"},
                                           {"owner": "HR", "name": "JOBS"},
                                           {"owner": "HR", "name": "EMPLOYEES"},
                                           {"owner": "HR", "name": "JOB_HISTORY"}]
                     }',
    description  => 'Select AI sobre o schema HR - compartilhado com o usuario HR'
  );
END;
/

-- Teste de fumaca ANTES de compartilhar. Valida credencial + rede + modelo.
-- Se falhar aqui, o problema e de configuracao, nao de prompt.
SELECT DBMS_CLOUD_AI.GENERATE(prompt       => 'Responda apenas: OK',
                              profile_name => 'HR_OPENAI',
                              action       => 'chat') AS teste
  FROM dual;
-- Esperado: OK

-- Compartilhar o profile com o HR
BEGIN
  DBMS_CLOUD_AI.GRANT_PROFILE_ACCESS(profile_name      => 'HR_OPENAI',
                                     user_or_role_name => 'HR');
END;
/

-- ---------- 2.2 Como HR ----------
--   connect -name "HR - imersao-out-26ai"

SELECT USER AS usuario_da_sessao FROM dual;
-- Esperado: HR

-- A credencial LOCAL e obrigatoria (REGRA 2). O nome tem de ser identico ao
-- credential_name declarado no profile do ADMIN: OPENAI_CRED.
BEGIN
  DBMS_CLOUD.CREATE_CREDENTIAL(
    credential_name => 'OPENAI_CRED',
    username        => 'OPENAI',
    password        => '<OPENAI_API_KEY_REDACTED>'
  );
END;
/

SELECT credential_name, username, enabled FROM user_credentials;

-- Profiles visiveis para o HR (o proprio + os concedidos)
SELECT profile_name, status FROM all_cloud_ai_profiles;

-- Ativar o profile do ADMIN - qualificar com o schema e obrigatorio.
-- Isto e POR SESSAO: ao reconectar, roda de novo.
EXEC DBMS_CLOUD_AI.SET_PROFILE('ADMIN.HR_OPENAI');

-- Teste de fumaca como HR
SELECT DBMS_CLOUD_AI.GENERATE(prompt       => 'Responda apenas: OK',
                              profile_name => 'ADMIN.HR_OPENAI',
                              action       => 'chat') AS teste
  FROM dual;


--------------------------------------------------------------------------------
-- PARTE 3 - AS ACOES DO SELECT AI (COMO HR)
--------------------------------------------------------------------------------
-- Sao 6 acoes. A diferenca entre elas e o que volta, nao o que e enviado.
--------------------------------------------------------------------------------

-- 3.1 runsql (padrao) - gera o SQL, executa e devolve o resultado.
SELECT AI 'quantos funcionarios existem';
SELECT AI runsql 'quantos funcionarios existem';
-- Esperado: 107

-- 3.2 showsql - mostra o SQL gerado, SEM executar.
--     Acao mais util em sala de aula: expoe o que a IA entendeu.
SELECT AI showsql 'quantos funcionarios existem';

-- 3.3 explainsql - gera o SQL e explica em linguagem natural o que ele faz.
SELECT AI explainsql 'qual a media salarial por departamento';

-- 3.4 narrate - executa e devolve a RESPOSTA EM TEXTO, nao a tabela.
SELECT AI narrate 'qual a media salarial por departamento';

-- 3.5 showprompt - exibe o prompt completo enviado ao LLM, incluindo o DDL das
--     tabelas do object_list. Serve para entender custo em tokens e depurar.
SELECT AI showprompt 'quantos funcionarios existem';

-- 3.6 chat - conversa direta com o modelo, IGNORANDO o schema.
--     ATENCAO: chat NAO consulta o banco. Perguntar "quantos funcionarios
--     existem" em modo chat faz o modelo inventar ou dizer que nao sabe,
--     porque ele nao recebe metadados nem executa SQL.
SELECT AI chat 'explique em um paragrafo o que e o schema HR de exemplo da Oracle';


--------------------------------------------------------------------------------
-- PARTE 4 - CONSULTAS DE NEGOCIO (COMO HR)
--------------------------------------------------------------------------------
-- Progressao: tabela unica -> agregacao -> join -> self-join -> temporal.
-- Valores esperados conferidos contra os dados reais do schema.
--------------------------------------------------------------------------------

-- ---------- 4.1 Estrutura ----------
SELECT AI 'qual a estrutura da tabela employees';
SELECT AI 'quais tabelas existem e o que cada uma armazena';
SELECT AI showsql 'liste as colunas da tabela departments com seus tipos';

-- ---------- 4.2 Tabela unica ----------
SELECT AI 'liste os 5 funcionarios com maior salario, mostre nome, sobrenome e salario';
-- Esperado: King no topo, com 24000
SELECT AI 'qual o menor e o maior salario da empresa';
-- Esperado: 2100 e 24000
SELECT AI 'quantos funcionarios foram admitidos em cada ano, em ordem cronologica';
SELECT AI 'quais funcionarios recebem comissao';
-- Esperado: 35 funcionarios (os outros 72 tem commission_pct nulo)

-- ---------- 4.3 Agregacao ----------
SELECT AI 'qual o total de salarios pagos por departamento, do maior para o menor';
SELECT AI 'quantos funcionarios existem em cada cargo, mostre o titulo do cargo';
SELECT AI 'qual a media salarial de cada cargo comparada com o salario maximo previsto para o cargo';
-- Bom teste: a faixa esta em JOBS e o salario em EMPLOYEES

-- ---------- 4.4 Joins ----------
SELECT AI 'quantos funcionarios trabalham em cada departamento, mostre o nome do departamento';
SELECT AI 'em quais cidades e paises a empresa tem escritorios';
SELECT AI 'liste os funcionarios do departamento de vendas que ganham mais de 10000';
SELECT AI showsql 'qual o total de salarios por regiao do mundo';
-- Join de 5 tabelas: EMPLOYEES -> DEPARTMENTS -> LOCATIONS -> COUNTRIES -> REGIONS
SELECT AI 'qual o total de salarios por regiao do mundo';

-- ---------- 4.5 Self-join (hierarquia) ----------
SELECT AI 'mostre o nome de cada funcionario e o nome do gestor dele';
SELECT AI 'qual funcionario nao tem gestor';
-- Esperado: 1 (King, o presidente)
SELECT AI 'quais gestores tem mais de 5 subordinados diretos';

-- ---------- 4.6 Casos de borda ----------
SELECT AI 'existe algum funcionario sem departamento';
-- Esperado: 1 (Grant). Mostra por que INNER JOIN esconderia esse registro.
SELECT AI 'quais departamentos nao tem nenhum funcionario';
-- Esperado: 16 dos 27 departamentos

-- ---------- 4.7 Temporal ----------
SELECT AI 'quais funcionarios ja mudaram de cargo';
SELECT AI 'mostre o historico de cargos do funcionario com sobrenome Kochhar';
SELECT AI 'quanto tempo em dias durou cada passagem registrada no historico de cargos';

-- ---------- 4.8 Conversa com contexto ----------
-- O profile foi criado com conversation=true: o Select AI mantem o historico
-- da sessao. As perguntas abaixo so fazem sentido em sequencia.
SELECT AI 'quantos funcionarios existem no departamento de TI';
SELECT AI 'e qual a media salarial deles';
SELECT AI 'mostre agora o nome dos tres que ganham mais';

SELECT conversation_id, title, created FROM user_cloud_ai_conversations ORDER BY created DESC;
SELECT prompt, action FROM session_cloud_ai_conversation_prompts ORDER BY 1;


--------------------------------------------------------------------------------
-- PARTE 5 - AJUSTES NO PROFILE (COMO **ADMIN**, dono do profile)
--------------------------------------------------------------------------------

-- 5.1 Trocar o modelo sem recriar o profile
BEGIN
  DBMS_CLOUD_AI.SET_ATTRIBUTE('HR_OPENAI', 'model', 'gpt-4o-mini');
END;
/

-- 5.2 Restringir o escopo. Demonstra que a IA so enxerga o object_list:
--     depois disto, perguntas sobre paises e regioes deixam de funcionar.
BEGIN
  DBMS_CLOUD_AI.SET_ATTRIBUTE(
    'HR_OPENAI', 'object_list',
    '[{"owner": "HR", "name": "EMPLOYEES"},
      {"owner": "HR", "name": "DEPARTMENTS"}]');
END;
/

-- (como HR) a consulta abaixo agora falha ou volta incompleta
SELECT AI showsql 'qual o total de salarios por regiao do mundo';

-- 5.3 Restaurar o escopo completo
BEGIN
  DBMS_CLOUD_AI.SET_ATTRIBUTE(
    'HR_OPENAI', 'object_list',
    '[{"owner": "HR", "name": "REGIONS"},
      {"owner": "HR", "name": "COUNTRIES"},
      {"owner": "HR", "name": "LOCATIONS"},
      {"owner": "HR", "name": "DEPARTMENTS"},
      {"owner": "HR", "name": "JOBS"},
      {"owner": "HR", "name": "EMPLOYEES"},
      {"owner": "HR", "name": "JOB_HISTORY"}]');
END;
/

-- 5.4 Desativar o profile na sessao (como HR)
EXEC DBMS_CLOUD_AI.CLEAR_PROFILE;

-- Sem profile ativo o SELECT AI falha. Alternativa: passar o profile explicito.
SELECT DBMS_CLOUD_AI.GENERATE(prompt       => 'quantos funcionarios existem',
                              profile_name => 'ADMIN.HR_OPENAI',
                              action       => 'runsql') AS resultado
  FROM dual;

EXEC DBMS_CLOUD_AI.SET_PROFILE('ADMIN.HR_OPENAI');


--------------------------------------------------------------------------------
-- PARTE 6 - DIAGNOSTICO (erros reais observados neste banco)
--------------------------------------------------------------------------------
-- ORA-24247: Network access denied by access control list (ACL)
--     Falta a ACE da PARTE 1.3 para o principal DA SESSAO. Lembre da REGRA 1:
--     vale tambem para o ADMIN. Conferir:
--        SELECT a.host, p.principal, p.privilege
--          FROM dba_network_acls a
--          JOIN dba_network_acl_privileges p ON p.acl = a.acl
--         WHERE a.host = 'api.openai.com';
--
-- ORA-20004: Credential "<USUARIO>"."<NOME>" does not exist
--     REGRA 2: falta a credencial LOCAL com o mesmo nome declarado no profile.
--     Criar no schema do usuario que executa.
--
-- ORA-20401: Authorization failed for URI - bearer://api.openai.com/...
--     Rede e credencial OK, chave recusada pelo provedor. Recriar:
--        BEGIN DBMS_CLOUD.DROP_CREDENTIAL('OPENAI_CRED'); END;
--        /
--     e repetir o CREATE_CREDENTIAL com a chave correta.
--
-- ORA-00942 em DBMS_CLOUD.CREATE_CREDENTIAL
--     Falta o GRANT EXECUTE ON DBMS_CLOUD da PARTE 1.1.
--
-- "profile not set"
--     Falta SET_PROFILE nesta sessao. O profile nao persiste entre conexoes.
--     Lembre de qualificar com o schema: 'ADMIN.HR_OPENAI'.
--
-- SQL gerado errado ou incompleto
--     Rodar a mesma pergunta com showprompt para ver o que a IA recebeu.
--     Causas frequentes: tabela fora do object_list, coluna sem comentario,
--     estatisticas desatualizadas, pergunta ambigua.
--
SELECT prompt, action, error_message
  FROM user_cloud_ai_conversation_prompts
 ORDER BY 1;


--------------------------------------------------------------------------------
-- PARTE 7 - LIMPEZA (OPCIONAL)
--------------------------------------------------------------------------------

-- Como HR
BEGIN DBMS_CLOUD.DROP_CREDENTIAL('OPENAI_CRED'); END;
/

-- Como ADMIN
BEGIN
  DBMS_CLOUD_AI.DROP_PROFILE(profile_name => 'HR_OPENAI', force => TRUE);
END;
/
BEGIN DBMS_CLOUD.DROP_CREDENTIAL('OPENAI_CRED'); END;
/

-- Revogar acesso do HR (descomente para desfazer por completo)
-- BEGIN
--   DBMS_NETWORK_ACL_ADMIN.REMOVE_HOST_ACE(
--     host => 'api.openai.com',
--     ace  => xs$ace_type(privilege_list => xs$name_list('http'),
--                         principal_name  => 'HR',
--                         principal_type  => xs_acl.ptype_db),
--     remove_empty_acl => TRUE);
-- END;
-- /
-- REVOKE EXECUTE ON DBMS_CLOUD_AI FROM hr;
-- REVOKE EXECUTE ON DBMS_CLOUD    FROM hr;

--------------------------------------------------------------------------------
-- FIM
--------------------------------------------------------------------------------
