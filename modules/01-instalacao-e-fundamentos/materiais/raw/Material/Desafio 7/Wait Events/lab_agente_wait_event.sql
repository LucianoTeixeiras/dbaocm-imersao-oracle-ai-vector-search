--------------------------------------------------------------------------------
--  AGENTE INVESTIGADOR DE WAIT EVENTS  -  Select AI Agent
--  Porte da skill "Wait_Event_Investigator" para DBMS_CLOUD_AI_AGENT
--------------------------------------------------------------------------------
--  Autor..: Guilherme Brito - DBAOCM
--  Data...: 12/09/2026  (v1)   |   18/10/2026  (v2)
--  Banco..: Oracle Autonomous Database 26ai / ADMIN
--           validado em 23.26.3.3.0 (v1) e 23.26.4.1.0 (v2)
--  Status.: VALIDADO. RUN_TEAM -> SUCCEEDED (16s na v1, 33s na v2), 5 tools
--           chamadas na ordem correta. Na v2, com o banco ocioso, o laudo
--           conclui corretamente "nao ha causa raiz a apontar" e aponta o
--           hard parse (18,4% do DB time) em vez de diagnosticar a si mesmo.
--
--  HISTORICO DA v2 (18/10/2026)
--    1. CREATE_TOOL: a partir da 23.26.4 o atributo "instruction" passou a ser
--       OBRIGATORIO tambem para tool de funcao. Sem ele:
--         ORA-20052: Missing value for tool attribute - instruction
--    2. Profile: o nome do profile de IA agora e um ponto de ajuste explicito
--       (PARTE 2). Neste ADB e o HR_OPENAI.
--    3. Consulta de duracao do RUN_TEAM corrigida: EXTRACT(SECOND) devolvia so o
--       componente de segundos (1min10s aparecia como 10).
--    4. Evento "TCP Socket (KGAS)": e a espera das chamadas HTTP de saida -
--       inclusive as do PROPRIO agente ao LLM. Com o banco ocioso o agente
--       escolhia esse evento e diagnosticava a si mesmo; descartado o KGAS, caia
--       no "enq: JG - queue lock" (o job do scheduler do proprio RUN_TEAM).
--       Tools 3 e 4 ganharam regras para os dois eventos, e a Tool 5 marca
--       qualquer SQL que seja o proprio agente/cliente (RUN_TEAM, DBMS_CLOUD_AI,
--       DBMS_SCHEDULER, SqlDevBind, dbmsoutput) e manda NAO inventar causa raiz.
--    5. DEMO_SAIDA criada so se nao existir (e compartilhada com o lab do HR).
--
--  ESCOPO: TRIAGEM, nao laudo completo.
--  O agente responde "para onde olhar" em ~15 segundos, com rastro auditavel.
--  O aprofundamento (plano de execucao, P1/P2 ate o bloco, RAC) continua manual.
--
--  MAPEAMENTO DA SKILL
--    Papel + Principios inegociaveis -> role do agent
--    Etapa 0 (enquadrar)             -> user_prompt
--    Etapas 1 a 5 (16 blocos SQL)    -> 5 tools
--    Etapa 6 (arvore de decisao)     -> a decisao do proprio agente (ReAct)
--    Etapas 7 a 10 (laudo)           -> instruction da task
--------------------------------------------------------------------------------


-- token
<OPENAI_API_KEY_REDACTED>


--##############################################################################
--  PARTE 0 - PRE-REQUISITO DE PRIVILEGIO   >>> LEIA ANTES <<<
--##############################################################################
--
--  ROLES NAO VALEM DENTRO DE PL/SQL DEFINER'S RIGHTS.
--  Ter SELECT_CATALOG_ROLE nao basta: a funcao nao compila ou nao enxerga a view.
--
--  Neste ADB funcionou porque o ADMIN tem GRANT DIRETO. Teste antes com:

CREATE OR REPLACE FUNCTION wait_fn_teste_privilegio RETURN VARCHAR2 AS
    v_a NUMBER; v_b NUMBER; v_c NUMBER; v_d NUMBER; v_e NUMBER; v_f NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_a FROM v$sys_time_model;
    SELECT COUNT(*) INTO v_b FROM v$system_wait_class;
    SELECT COUNT(*) INTO v_c FROM v$system_event   WHERE ROWNUM <= 10;
    SELECT COUNT(*) INTO v_d FROM v$event_name     WHERE ROWNUM <= 10;
    SELECT COUNT(*) INTO v_e FROM v$sql            WHERE ROWNUM <= 10;
    SELECT COUNT(*) INTO v_f FROM v$active_session_history WHERE ROWNUM <= 10;
    RETURN 'OK - todas as views acessiveis de dentro de PL/SQL';
EXCEPTION
    WHEN OTHERS THEN RETURN 'FALHOU: ' || SQLERRM;
END;
/

SELECT wait_fn_teste_privilegio FROM dual;

--  Se falhar, peça ao DBA (como SYS):
--      GRANT SELECT ON v_$sys_time_model          TO <usuario>;
--      GRANT SELECT ON v_$system_wait_class       TO <usuario>;
--      GRANT SELECT ON v_$system_event            TO <usuario>;
--      GRANT SELECT ON v_$event_name              TO <usuario>;
--      GRANT SELECT ON v_$session                 TO <usuario>;
--      GRANT SELECT ON v_$sql                     TO <usuario>;
--      GRANT SELECT ON v_$instance                TO <usuario>;
--      GRANT SELECT ON v_$parameter               TO <usuario>;
--      GRANT SELECT ON v_$active_session_history  TO <usuario>;   -- Diagnostics Pack
--  (note o "v_$", que e' a tabela real por tras do sinonimo "v$")
--
--  Alternativa: declarar as funcoes com AUTHID CURRENT_USER, ai a role do
--  chamador vale - mas o agente passa a depender de quem executa.

DROP FUNCTION wait_fn_teste_privilegio;


--##############################################################################
--  PARTE 1 - AS 5 FUNCOES
--##############################################################################

--------------------------------------------------------------------------------
-- TOOL 1 - Etapa 1 da skill: dimensionar a dor (Time Model)
--          Devolve um VEREDITO. E' ele que faz o agente desviar.
--------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wait_fn_triagem RETURN VARCHAR2 AS
    v_db_time  NUMBER;
    v_db_cpu   NUMBER;
    v_sqlexec  NUMBER;
    v_hard     NUMBER;
    v_uptime   NUMBER;
    v_cpus     NUMBER;
    v_aas      NUMBER;
    v_pct_cpu  NUMBER;
    v_pct_hard NUMBER;
    v_out      VARCHAR2(4000);
BEGIN
    SELECT MAX(CASE WHEN stat_name='DB time'                  THEN value END),
           MAX(CASE WHEN stat_name='DB CPU'                   THEN value END),
           MAX(CASE WHEN stat_name='sql execute elapsed time' THEN value END),
           MAX(CASE WHEN stat_name='hard parse elapsed time'  THEN value END)
      INTO v_db_time, v_db_cpu, v_sqlexec, v_hard
      FROM v$sys_time_model;

    SELECT (SYSDATE - startup_time) * 86400 INTO v_uptime FROM v$instance;
    SELECT TO_NUMBER(value) INTO v_cpus FROM v$parameter WHERE name = 'cpu_count';

    v_pct_cpu  := ROUND(100 * v_db_cpu / NULLIF(v_db_time,0), 1);
    v_pct_hard := ROUND(100 * v_hard   / NULLIF(v_db_time,0), 1);
    v_aas      := ROUND((v_db_time/1e6) / NULLIF(v_uptime,0), 2);

    v_out := 'TRIAGEM DE DB TIME (acumulado desde o startup, ha ' ||
             TO_CHAR(ROUND(v_uptime/3600,1),'FM999G990D0') || ' horas)' || CHR(10) ||
             'DB time..................: ' || TO_CHAR(ROUND(v_db_time/1e6),'FM999G999G990') || ' s' || CHR(10) ||
             'DB CPU...................: ' || TO_CHAR(ROUND(v_db_cpu/1e6),'FM999G999G990')  || ' s (' || TO_CHAR(v_pct_cpu,'FM990D0') || '% do DB time)' || CHR(10) ||
             'sql execute elapsed time.: ' || TO_CHAR(ROUND(v_sqlexec/1e6),'FM999G999G990') || ' s' || CHR(10) ||
             'hard parse elapsed time..: ' || TO_CHAR(ROUND(v_hard/1e6),'FM999G999G990')    || ' s (' || TO_CHAR(v_pct_hard,'FM990D0') || '% do DB time)' || CHR(10) ||
             'Tempo em ESPERA (DB time - DB CPU): ' || TO_CHAR(ROUND((v_db_time - v_db_cpu)/1e6),'FM999G999G990') ||
             ' s (' || TO_CHAR(ROUND(100 - v_pct_cpu,1),'FM990D0') || '%)' || CHR(10) ||
             'AAS medio................: ' || TO_CHAR(v_aas,'FM990D00') || ' em ' || v_cpus || ' CPU(s)' || CHR(10) ||
             CHR(10) || 'VEREDITO: ';

    IF v_pct_cpu >= 70 THEN
        v_out := v_out || 'O GARGALO NAO E WAIT EVENT. ' || TO_CHAR(v_pct_cpu,'FM990D0') ||
                 '% do DB time e CPU. Pare de procurar evento de espera e va direto para o SQL ' ||
                 'mais caro e seu plano de execucao. NAO chame as tools WAIT_CLASSES nem TOP_EVENTOS.';
    ELSE
        v_out := v_out || 'ESPERA E RELEVANTE (' || TO_CHAR(ROUND(100 - v_pct_cpu,1),'FM990D0') ||
                 '% do DB time). Prossiga: chame a tool WAIT_CLASSES para descobrir a classe dominante.';
    END IF;

    IF v_pct_hard >= 10 THEN
        v_out := v_out || CHR(10) || 'ALERTA: hard parse consome ' || TO_CHAR(v_pct_hard,'FM990D0') ||
                 '% do DB time. Suspeite de falta de bind variables.';
    END IF;

    IF v_aas > v_cpus THEN
        v_out := v_out || CHR(10) || 'ALERTA: AAS (' || TO_CHAR(v_aas,'FM990D00') ||
                 ') maior que o numero de CPUs (' || v_cpus || '). Banco saturado no acumulado.';
    END IF;

    RETURN v_out;
END;
/

--------------------------------------------------------------------------------
-- TOOL 2 - Etapa 2 da skill: wait class antes do wait event
--------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wait_fn_classes RETURN VARCHAR2 AS
    v_out   VARCHAR2(4000);
    v_total NUMBER;
BEGIN
    SELECT SUM(time_waited) INTO v_total
      FROM v$system_wait_class WHERE wait_class <> 'Idle';

    IF NVL(v_total,0) = 0 THEN
        RETURN 'Nenhuma espera nao-idle registrada.';
    END IF;

    v_out := 'WAIT CLASSES NAO-IDLE (acumulado desde o startup)' || CHR(10) ||
             RPAD('CLASSE',22) || RPAD('SEGUNDOS',12) || RPAD('% ESPERA',10) || 'WAITS' || CHR(10) ||
             RPAD('-',60,'-') || CHR(10);

    FOR r IN (SELECT wait_class,
                     ROUND(time_waited/100) AS segundos,
                     ROUND(100 * time_waited / v_total, 1) AS pct,
                     total_waits
                FROM v$system_wait_class
               WHERE wait_class <> 'Idle' AND time_waited > 0
               ORDER BY time_waited DESC
               FETCH FIRST 8 ROWS ONLY)
    LOOP
        v_out := v_out || RPAD(r.wait_class,22) ||
                 RPAD(TO_CHAR(r.segundos,'FM999G999G990'),12) ||
                 RPAD(TO_CHAR(r.pct,'FM990D0')||'%',10) ||
                 TO_CHAR(r.total_waits,'FM999G999G990') || CHR(10);
    END LOOP;

    RETURN v_out || CHR(10) ||
        'A classe no topo direciona a investigacao: User I/O = SQL lendo demais ou storage; ' ||
        'Concurrency = contencao interna (latch, mutex, buffer busy); ' ||
        'Application = lock causado pela aplicacao (enq TM/TX); ' ||
        'Commit = log file sync; Configuration = redo/undo/shared pool mal dimensionado; ' ||
        'Network = round-trips.' || CHR(10) ||
        'PROXIMO PASSO: chame a tool TOP_EVENTOS.';
END;
/

--------------------------------------------------------------------------------
-- TOOL 3 - Etapa 3 da skill: top eventos com latencia e % do DB time
--          Trata a pegadinha do %DBTIME acima de 100% (background x foreground)
--------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wait_fn_top_eventos RETURN VARCHAR2 AS
    v_out     VARCHAR2(4000);
    v_db_time NUMBER;
    v_bg      BOOLEAN := FALSE;
    v_kgas    BOOLEAN := FALSE;
BEGIN
    SELECT value INTO v_db_time FROM v$sys_time_model WHERE stat_name = 'DB time';

    v_out := 'TOP WAIT EVENTS NAO-IDLE (acumulado desde o startup)' || CHR(10) ||
             RPAD('EVENTO',34) || RPAD('CLASSE',15) || RPAD('SEG',11) ||
             RPAD('%DBTIME',10) || 'AVG_MS' || CHR(10) ||
             RPAD('-',80,'-') || CHR(10);

    FOR r IN (SELECT event, wait_class,
                     ROUND(time_waited_micro/1e6) AS segundos,
                     ROUND(100 * time_waited_micro / NULLIF(v_db_time,0), 1) AS pct_db,
                     ROUND(time_waited_micro/1000/NULLIF(total_waits,0), 2) AS avg_ms
                FROM v$system_event
               WHERE wait_class <> 'Idle' AND time_waited_micro > 0
               ORDER BY time_waited_micro DESC
               FETCH FIRST 8 ROWS ONLY)
    LOOP
        IF r.pct_db > 100 THEN v_bg := TRUE; END IF;
        IF r.event = 'TCP Socket (KGAS)' THEN v_kgas := TRUE; END IF;
        v_out := v_out || RPAD(SUBSTR(r.event,1,33),34) ||
                 RPAD(SUBSTR(r.wait_class,1,14),15) ||
                 RPAD(TO_CHAR(r.segundos,'FM999G999G990'),11) ||
                 RPAD(TO_CHAR(r.pct_db,'FM999G990D0')||'%',10) ||
                 TO_CHAR(r.avg_ms,'FM999G999G990D00') || CHR(10);
    END LOOP;

    v_out := v_out || CHR(10) ||
        'COMO LER: valores CUMULATIVOS desde o startup. Um evento com muitos segundos ' ||
        'pode ser o pico de ontem, nao o problema de agora. Abaixo de 5% do DB time o ' ||
        'evento e irrelevante.' || CHR(10);

    IF v_bg THEN
        v_out := v_out ||
        'IMPORTANTE: ha evento com %DBTIME acima de 100%. Isso NAO e erro. DB time conta apenas ' ||
        'sessoes de FOREGROUND (usuario), enquanto V$SYSTEM_EVENT soma tambem as esperas dos ' ||
        'processos de BACKGROUND (LGWR, DBWR, CKPT, ARCH). Evento de System I/O estourando 100% ' ||
        'e tipicamente background e NAO afeta o usuario final. Priorize os eventos com %DBTIME ' ||
        'abaixo de 100%.' || CHR(10);
    END IF;

    -- v2: sem este aviso, com o banco ocioso o agente escolhia o KGAS e
    -- diagnosticava as proprias chamadas ao LLM como causa raiz.
    IF v_kgas THEN
        v_out := v_out ||
        'ATENCAO: "TCP Socket (KGAS)" e espera de chamada HTTP de saida (UTL_HTTP, DBMS_CLOUD, ' ||
        'Select AI) - INCLUSIVE as chamadas que este proprio agente faz ao LLM agora. So trate ' ||
        'como causa se houver outro evento de foreground relevante descartado ou se a aplicacao ' ||
        'chamar APIs externas. Prefira o proximo evento de foreground da lista.' || CHR(10);
    END IF;

    RETURN v_out || 'PROXIMO PASSO: chame DECODIFICAR_EVENTO com o evento de foreground mais relevante.';
END;
/

--------------------------------------------------------------------------------
-- TOOL 4 - Etapas 3 e 4 da skill: P1/P2/P3 + arvore de decisao por evento
--------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wait_fn_decodificar(
    p_evento IN VARCHAR2
) RETURN VARCHAR2 AS
    v_nome  VARCHAR2(64);
    v_class VARCHAR2(64);
    v_p1    VARCHAR2(64);
    v_p2    VARCHAR2(64);
    v_p3    VARCHAR2(64);
    v_out   VARCHAR2(4000);
    v_sug   VARCHAR2(1000);
BEGIN
    BEGIN
        SELECT name, wait_class, parameter1, parameter2, parameter3
          INTO v_nome, v_class, v_p1, v_p2, v_p3
          FROM v$event_name WHERE LOWER(name) = LOWER(TRIM(p_evento));
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            FOR r IN (SELECT name FROM v$event_name
                       WHERE LOWER(name) LIKE '%'||LOWER(TRIM(p_evento))||'%'
                       FETCH FIRST 5 ROWS ONLY)
            LOOP
                v_sug := v_sug || '  - ' || r.name || CHR(10);
            END LOOP;
            IF v_sug IS NULL THEN
                RETURN 'Evento "' || p_evento || '" nao existe em V$EVENT_NAME.';
            END IF;
            RETURN 'Evento "' || p_evento || '" nao encontrado. Voce quis dizer:' || CHR(10) || v_sug;
    END;

    v_out := 'EVENTO....: ' || v_nome  || CHR(10) ||
             'WAIT CLASS: ' || v_class || CHR(10) ||
             'P1........: ' || NVL(v_p1,'(nao usado)') || CHR(10) ||
             'P2........: ' || NVL(v_p2,'(nao usado)') || CHR(10) ||
             'P3........: ' || NVL(v_p3,'(nao usado)') || CHR(10) || CHR(10);

    v_out := v_out || 'INTERPRETACAO: ' ||
        CASE
          WHEN v_nome = 'db file sequential read' THEN
            'Leitura de UM bloco por vez, tipicamente via indice. P1=file#, P2=block#. ' ||
            'Quase NUNCA e problema de storage: e SQL lendo blocos demais por plano ruim. ' ||
            'Se avg_ms < 10 o storage esta saudavel e a causa e o plano. Acima de 20ms, ai sim suspeite de I/O. ' ||
            'ACAO: ache o SQL e o objeto, nao o disco.'
          WHEN v_nome = 'db file scattered read' THEN
            'Leitura multibloco, tipicamente full table scan. P1=file#, P2=block#, P3=blocos. ' ||
            'ACAO: verifique se o full scan e proposital; se nao, falta indice ou a estatistica esta velha.'
          WHEN v_nome = 'log file sync' THEN
            'A sessao deu COMMIT e espera o LGWR gravar o redo. P1=buffer#. ' ||
            'Causa 1: commits demais (commit dentro de loop na aplicacao). ' ||
            'Causa 2: redo lento - compare com log file parallel write. ' ||
            'ACAO: se log file parallel write tambem esta alto, e storage do redo; senao e a aplicacao commitando linha a linha.'
          WHEN v_nome IN ('buffer busy waits','read by other session') THEN
            'Duas ou mais sessoes disputam o MESMO bloco. P1=file#, P2=block#, P3=classe do bloco. ' ||
            'ACAO: identifique o objeto via ROW_WAIT_OBJ#. Segment header pede ASSM/freelists; ' ||
            'bloco de dados costuma ser hot block da aplicacao.'
          WHEN v_nome LIKE 'enq: TX%' THEN
            'Lock de LINHA: uma sessao espera outra liberar a linha. P2=usn+slot, P3=sequence. ' ||
            'Isso NAO e problema de banco, e transacao aberta na aplicacao. ' ||
            'ACAO: ache o blocking_session e veja ha quanto tempo esta INACTIVE. Causa classica: falta de commit.'
          WHEN v_nome LIKE 'enq: TM%' THEN
            'Lock de TABELA. Causa classica: FOREIGN KEY SEM INDICE - delete no pai trava a filha inteira. ' ||
            'ACAO: liste as FKs sem indice do objeto envolvido antes de qualquer outra coisa.'
          WHEN v_nome LIKE 'latch%' OR v_nome LIKE '%mutex%' OR v_nome LIKE 'library cache%' THEN
            'Contencao de estrutura interna da SGA (Concurrency). Geralmente sintoma de hard parse ' ||
            'excessivo por falta de bind variables, ou SQL com version_count alto. ' ||
            'ACAO: olhe hard parse no time model; nao aumente shared pool no escuro.'
          WHEN v_nome LIKE 'gc %' THEN
            'RAC: bloco vindo de outra instancia pelo interconnect. ' ||
            'ACAO: troque V$ por GV$, agrupe por INST_ID, verifique latencia do interconnect.'
          WHEN v_nome = 'control file sequential read' THEN
            'Leitura do controlfile, quase sempre por BACKGROUND (CKPT, ARCH, MMON). ' ||
            'Raramente afeta o usuario final. ACAO: ignore para tuning de aplicacao, a menos ' ||
            'que haja sessao de usuario esperando nele.'
          WHEN v_nome = 'TCP Socket (KGAS)' THEN
            'Espera de socket TCP aberto pelo proprio banco: chamada HTTP de saida via UTL_HTTP, ' ||
            'DBMS_CLOUD ou Select AI. Num banco ocioso, quase sempre e o PROPRIO AGENTE chamando o LLM. ' ||
            'ACAO: confira se os SQL do evento sao DBMS_CLOUD_AI/RUN_TEAM; se forem, descarte e ' ||
            'investigue o proximo evento. Se for a aplicacao, meca a latencia da API externa.'
          WHEN v_nome = 'enq: JG - queue lock' THEN
            'Enqueue de grupo de jobs do DBMS_SCHEDULER. O RUN_TEAM do Select AI Agent roda cada ' ||
            'task como job do scheduler, entao num banco ocioso este evento e o PROPRIO AGENTE. ' ||
            'ACAO: se os SQL forem DBMS_SCHEDULER/RUN_TEAM, descarte; senao, veja jobs concorrentes ' ||
            'em DBA_SCHEDULER_RUNNING_JOBS.'
          WHEN v_class = 'Idle' THEN
            'Classe IDLE: sessao ociosa esperando trabalho. NAO e gargalo. Descarte da analise.'
          WHEN v_nome LIKE 'enq:%' THEN
            'Enqueue (lock serializado). Os 2 caracteres apos "enq:" dizem o tipo do recurso. ' ||
            'ACAO: identifique quem segura o enqueue em V$LOCK.'
          ELSE
            'Sem regra especifica cadastrada nesta tool. Use a wait class (' || v_class ||
            ') para direcionar e os parametros P1/P2/P3 acima para chegar ao recurso esperado.'
        END;

    RETURN v_out || CHR(10) || CHR(10) ||
           'PROXIMO PASSO: chame SQL_DO_EVENTO com este mesmo nome de evento.';
END;
/

--------------------------------------------------------------------------------
-- TOOL 5 - Etapa 5.4 da skill: do evento ate o SQL e o objeto (ASH)
--          Com guarda de licenca de Diagnostics Pack
--------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wait_fn_sql_do_evento(
    p_evento  IN VARCHAR2,
    p_minutos IN NUMBER DEFAULT 60
) RETURN VARCHAR2 AS
    v_out      VARCHAR2(4000);
    v_amostras NUMBER;
    v_pack     VARCHAR2(40);
    v_proprio  NUMBER := 0;
    -- v2: assinaturas de SQL que sao o proprio agente ou o cliente que o disparou,
    -- e nao carga da aplicacao. Com o banco ocioso, sao as UNICAS amostras no ASH.
    c_proprio  CONSTANT VARCHAR2(200) :=
        'DBMS_CLOUD_AI|RUN_TEAM|SqlDevBind|dbmsoutput_linesarray|DBMS_SCHEDULER\.SET_JOB_ARG';
BEGIN
    -- Em ADB o parametro nao e exposto e o Diagnostics Pack ja esta incluso.
    -- Sem este tratamento, o NO_DATA_FOUND mata a funcao silenciosamente.
    BEGIN
        SELECT value INTO v_pack FROM v$parameter
         WHERE name = 'control_management_pack_access';
    EXCEPTION
        WHEN NO_DATA_FOUND THEN v_pack := NULL;
    END;

    IF v_pack IS NOT NULL AND UPPER(v_pack) NOT LIKE '%DIAGNOSTIC%' THEN
        RETURN 'ASH indisponivel: control_management_pack_access = ' || v_pack ||
               '. V$ACTIVE_SESSION_HISTORY exige Diagnostics Pack licenciado. ' ||
               'Sem licenca use V$SESSION em tempo real ou Statspack.';
    END IF;

    BEGIN
        SELECT COUNT(*) INTO v_amostras
          FROM v$active_session_history
         WHERE sample_time > SYSTIMESTAMP - NUMTODSINTERVAL(p_minutos,'MINUTE')
           AND event = p_evento;
    EXCEPTION
        WHEN OTHERS THEN
            RETURN 'Nao foi possivel ler V$ACTIVE_SESSION_HISTORY: ' || SQLERRM ||
                   '. Verifique privilegio e licenca de Diagnostics Pack.';
    END;

    IF v_amostras = 0 THEN
        RETURN 'Nenhuma amostra de ASH para o evento "' || p_evento || '" nos ultimos ' ||
               p_minutos || ' minutos. O evento NAO esta ativo agora - so aparece no ' ||
               'acumulado desde o startup. Nao trate como problema atual.';
    END IF;

    v_out := 'SQL POR TRAS DE "' || p_evento || '" (ASH, ultimos ' || p_minutos || ' min, ' ||
             v_amostras || ' amostras)' || CHR(10) || RPAD('-',78,'-') || CHR(10);

    FOR r IN (SELECT ash.sql_id,
                     COUNT(*) AS amostras,
                     ROUND(100*COUNT(*)/v_amostras,1) AS pct,
                     MAX(ash.current_obj#) AS obj#
                FROM v$active_session_history ash
               WHERE ash.sample_time > SYSTIMESTAMP - NUMTODSINTERVAL(p_minutos,'MINUTE')
                 AND ash.event = p_evento
                 AND ash.sql_id IS NOT NULL
               GROUP BY ash.sql_id
               ORDER BY COUNT(*) DESC
               FETCH FIRST 5 ROWS ONLY)
    LOOP
        DECLARE
            v_txt VARCHAR2(300);
            v_obj VARCHAR2(200);
        BEGIN
            BEGIN
                SELECT SUBSTR(REPLACE(REPLACE(sql_text,CHR(10),' '),CHR(13),' '),1,150)
                  INTO v_txt FROM v$sql WHERE sql_id = r.sql_id AND ROWNUM = 1;
            EXCEPTION WHEN NO_DATA_FOUND THEN v_txt := '(SQL ja saiu da shared pool)';
            END;
            BEGIN
                SELECT owner||'.'||object_name||' ('||object_type||')'
                  INTO v_obj FROM dba_objects WHERE object_id = r.obj# AND ROWNUM = 1;
            EXCEPTION WHEN NO_DATA_FOUND THEN v_obj := '(nao identificado)';
            END;
            IF REGEXP_LIKE(v_txt, c_proprio, 'i') THEN
                v_proprio := v_proprio + r.amostras;
                v_txt := '[PROPRIO AGENTE/CLIENTE - NAO E CARGA DA APLICACAO] ' || v_txt;
            END IF;
            v_out := v_out || 'SQL_ID ' || r.sql_id || '  |  ' || r.amostras ||
                     ' amostras (' || TO_CHAR(r.pct,'FM990D0') || '% do evento)' || CHR(10) ||
                     '  OBJETO: ' || v_obj || CHR(10) ||
                     '  TEXTO : ' || v_txt || CHR(10) || CHR(10);
        END;
    END LOOP;

    IF v_proprio > 0 THEN
        v_out := v_out ||
            'ATENCAO: ' || v_proprio || ' de ' || v_amostras || ' amostras sao do PROPRIO agente ' ||
            '(RUN_TEAM, job do scheduler, chamada ao LLM) ou do cliente que o disparou. Isso NAO e ' ||
            'carga da aplicacao. Se for a maioria, conclua no laudo que o banco nao tem carga ' ||
            'relevante na janela e que nao ha causa raiz a apontar - NAO invente uma.' || CHR(10) || CHR(10);
    END IF;

    RETURN v_out ||
           'ACAO: o SQL_ID do topo responde pela maior parte da espera. Pegue o plano real com ' ||
           'DBMS_XPLAN.DISPLAY_CURSOR(''<sql_id>'',NULL,''ALLSTATS LAST'') e compare E-Rows com ' ||
           'A-Rows antes de propor indice.';
END;
/

-- Teste isolado das 5 funcoes
SELECT wait_fn_triagem      FROM dual;
SELECT wait_fn_classes      FROM dual;
SELECT wait_fn_top_eventos  FROM dual;
SELECT wait_fn_decodificar('db file sequential read') FROM dual;
SELECT wait_fn_sql_do_evento('db file sequential read', 60) FROM dual;


--##############################################################################
--  PARTE 2 - TOOLS, AGENT, TASK, TEAM
--##############################################################################
-- O texto da tool e' o UNICO que o modelo le para decidir. Nele vao as
-- regras de desvio da skill ("se o veredito disser CPU, NAO chame as outras").
--
-- v2: a partir da 23.26.4 o atributo "instruction" e obrigatorio tambem para
-- tool de funcao (senao: ORA-20052 Missing value for tool attribute - instruction).
-- O procedimento local MK grava o mesmo texto em "instruction" e em description,
-- o que funciona tanto na 23.26.3 quanto na 23.26.4.

BEGIN
    DECLARE
        PROCEDURE mk(p_nome VARCHAR2, p_func VARCHAR2, p_txt VARCHAR2) IS
        BEGIN
            DBMS_CLOUD_AI_AGENT.CREATE_TOOL(
                tool_name   => p_nome,
                attributes  => JSON_OBJECT('function' VALUE p_func, 'instruction' VALUE p_txt),
                description => p_txt);
        END;
    BEGIN
        mk('TRIAGEM_DBTIME', 'ADMIN.WAIT_FN_TRIAGEM',
           'Etapa 1 da investigacao: dimensiona a dor via Time Model. Nao recebe parametro. Retorna DB time, DB CPU, tempo em espera, hard parse, AAS e um VEREDITO dizendo se o gargalo e CPU ou espera. SEMPRE chame esta tool PRIMEIRO. Se o veredito disser que o gargalo e CPU, NAO chame WAIT_CLASSES nem TOP_EVENTOS.');

        mk('WAIT_CLASSES', 'ADMIN.WAIT_FN_CLASSES',
           'Etapa 2: lista as wait classes nao-idle com segundos, percentual e numero de waits. Nao recebe parametro. Chame somente se a TRIAGEM_DBTIME indicar que espera e relevante. A classe do topo direciona a investigacao.');

        mk('TOP_EVENTOS', 'ADMIN.WAIT_FN_TOP_EVENTOS',
           'Etapa 3: lista os top wait events nao-idle com segundos, percentual do DB time e latencia media em ms. Nao recebe parametro. Use para escolher o evento a investigar.');

        mk('DECODIFICAR_EVENTO', 'ADMIN.WAIT_FN_DECODIFICAR',
           'Etapa 4: recebe p_evento, o nome exato de um wait event, e retorna a wait class, o significado de P1, P2 e P3 e a interpretacao tecnica com a acao recomendada para aquele evento especifico.');

        mk('SQL_DO_EVENTO', 'ADMIN.WAIT_FN_SQL_DO_EVENTO',
           'Etapa 5: recebe p_evento (nome exato do wait event) e p_minutos (janela em minutos, use 60) e retorna, via ASH, os SQL_ID e objetos responsaveis por aquela espera. Se voltar "nenhuma amostra", o evento nao esta ativo agora e nao deve ser tratado como problema atual.');
    END;
END;
/

-- >>> PONTO DE AJUSTE: PROFILE DE IA <<<
-- O agente usa um profile do DBMS_CLOUD_AI ja existente (credencial + modelo).
-- Veja quais existem e troque "HR_OPENAI" no CREATE_AGENT abaixo pelo seu:
SELECT profile_name, status FROM user_cloud_ai_profiles;

-- E teste a credencial ANTES de seguir. ORA-20401 "Authorization failed" aqui
-- significa chave do provedor revogada/expirada - o RUN_TEAM vai falhar igual.
SELECT DBMS_CLOUD_AI.GENERATE(prompt => 'responda so: ok', profile_name => 'HR_OPENAI', action => 'chat') FROM dual;

-- Os "Principios inegociaveis" da skill viram o role do agent
BEGIN
    DBMS_CLOUD_AI_AGENT.CREATE_AGENT(
        agent_name  => 'INVESTIGADOR_WAIT',
        attributes  => '{
            "profile_name": "HR_OPENAI",
            "role": "Oracle Database Performance Engineer senior. Voce conduz investigacao de wait events BASEADA EM EVIDENCIA, do sintoma ate a causa raiz. Principios inegociaveis: (1) nunca concluir sem evidencia vinda de uma tool; (2) sintoma nao e causa raiz - db file sequential read quase nunca e storage, e SQL com plano ruim; (3) sempre descartar a classe Idle; (4) espera so faz sentido como percentual do DB Time; (5) V$SYSTEM_EVENT e cumulativo desde o startup, entao valor absoluto engana; (6) top-down sempre: DB Time, Wait Class, Wait Event, SQL, Objeto. Nunca invente numero que nao veio de uma tool.",
            "tools": ["TRIAGEM_DBTIME", "WAIT_CLASSES", "TOP_EVENTOS", "DECODIFICAR_EVENTO", "SQL_DO_EVENTO"]
        }',
        description => 'Investigador de wait events baseado na skill Wait_Event_Investigator.');
END;
/

-- As Etapas 6 a 10 da skill (arvore, hipoteses, laudo) viram a instruction
BEGIN
    DBMS_CLOUD_AI_AGENT.CREATE_TASK(
        task_name   => 'INVESTIGAR_ESPERAS',
        attributes  => '{
            "instruction": "Conduza uma triagem de wait events neste banco, top-down e baseada em evidencia. Passos obrigatorios: 1) chame TRIAGEM_DBTIME e LEIA O VEREDITO; 2) se o veredito disser que o gargalo e CPU, PARE de investigar espera, diga isso claramente e recomende ir para o SQL mais caro; 3) se espera for relevante, chame WAIT_CLASSES e identifique a classe dominante; 4) chame TOP_EVENTOS; 5) escolha o evento de FOREGROUND mais relevante, ignorando eventos cujo percentual do DB time passe de 100 por cento porque esses sao de background e nao afetam o usuario; 6) chame DECODIFICAR_EVENTO para esse evento; 7) chame SQL_DO_EVENTO para esse mesmo evento com p_minutos 60. Entregue um laudo em portugues do Brasil com: RESUMO EXECUTIVO em duas linhas, EVIDENCIAS coletadas citando os numeros que as tools retornaram, HIPOTESE DE CAUSA RAIZ, e PLANO DE ACAO com no maximo 3 itens priorizados. Se alguma evidencia faltar, diga o que falta em vez de supor."
        }',
        description => 'Triagem top-down de wait events.');
END;
/

BEGIN
    DBMS_CLOUD_AI_AGENT.CREATE_TEAM(
        team_name   => 'TIME_PERFORMANCE',
        attributes  => '{
            "agents": [{"name": "INVESTIGADOR_WAIT", "task": "INVESTIGAR_ESPERAS"}],
            "process": "sequential"
        }',
        description => 'Time de investigacao de performance.');
END;
/

-- Tabela auxiliar (compartilhada com o lab do HR) - 23ai: IF NOT EXISTS
CREATE TABLE IF NOT EXISTS demo_saida (
    id     NUMBER GENERATED BY DEFAULT AS IDENTITY,
    saida  CLOB,
    quando TIMESTAMP DEFAULT SYSTIMESTAMP);


--##############################################################################
--  PARTE 3 - EXECUTAR   (~15 a 30 segundos)
--##############################################################################
--  ANTES DA DEMO: com o banco ocioso, a unica atividade recente no ASH e o
--  proprio agente chamando o LLM ("TCP Socket (KGAS)"). Gere carga antes
--  (ex.: um loop de consultas sem bind no HR) para o laudo ter o que achar.

DECLARE
    v_conv   VARCHAR2(100);
    v_result CLOB;
BEGIN
    v_conv := DBMS_CLOUD_AI.CREATE_CONVERSATION();
    v_result := DBMS_CLOUD_AI_AGENT.RUN_TEAM(
        team_name   => 'TIME_PERFORMANCE',
        user_prompt => 'O banco parece lento. Investigue as esperas e me diga para onde olhar.',
        params      => '{"conversation_id": "' || v_conv || '"}');
    INSERT INTO demo_saida (saida) VALUES ('[WAIT] ' || v_result);
    COMMIT;
END;
/

SELECT saida FROM demo_saida ORDER BY id DESC FETCH FIRST 1 ROW ONLY;


-- O rastro: a cadeia top-down que o agente montou sozinho
SELECT TO_CHAR(h.start_date,'HH24:MI:SS') AS hora,
       h.tool_name,
       SUBSTR(h.input,1,70) AS entrada
  FROM user_ai_agent_tool_history h
 WHERE h.team_exec_id = (SELECT MAX(team_exec_id) KEEP (DENSE_RANK LAST ORDER BY start_date)
                           FROM user_ai_agent_team_history WHERE team_name = 'TIME_PERFORMANCE')
 ORDER BY h.start_date;
--
-- Saida real capturada na validacao:
--   14:08:08  TRIAGEM_DBTIME      {}
--   14:08:09  WAIT_CLASSES        {}
--   14:08:13  TOP_EVENTOS         {}
--   14:08:14  DECODIFICAR_EVENTO  {"P_EVENTO":"db file sequential read"}
--   14:08:16  SQL_DO_EVENTO       {"P_EVENTO":"db file sequential read","P_MINUTOS":60}
--
-- O detalhe que prova julgamento: no TOP_EVENTOS o primeiro colocado era
-- "control file sequential read" com 476.260 s e 5.127% do DB time. O agente
-- IGNOROU esse e escolheu "db file sequential read" com apenas 2,2%, porque a
-- tool avisou que %DBTIME acima de 100% e background e nao afeta o usuario.
-- E' o principio 4 da skill sendo aplicado sem ninguem mandar.

-- Duracao de cada execucao. v2: soma dia/hora/minuto/segundo - EXTRACT(SECOND)
-- sozinho devolve so o componente de segundos (1min10s aparecia como 10).
-- END_DATE fica nulo enquanto o team esta rodando.
SELECT state,
       ROUND( EXTRACT(DAY    FROM (end_date-start_date))*86400
            + EXTRACT(HOUR   FROM (end_date-start_date))*3600
            + EXTRACT(MINUTE FROM (end_date-start_date))*60
            + EXTRACT(SECOND FROM (end_date-start_date)), 1) AS segundos,
       TO_CHAR(start_date,'DD/MM HH24:MI:SS') AS inicio
  FROM user_ai_agent_team_history
 WHERE team_name = 'TIME_PERFORMANCE' ORDER BY start_date DESC;


--##############################################################################
--  PARTE 4 - TEARDOWN
--##############################################################################

BEGIN
    DBMS_CLOUD_AI_AGENT.DROP_TEAM (team_name  => 'TIME_PERFORMANCE');
    DBMS_CLOUD_AI_AGENT.DROP_TASK (task_name  => 'INVESTIGAR_ESPERAS');
    DBMS_CLOUD_AI_AGENT.DROP_AGENT(agent_name => 'INVESTIGADOR_WAIT');
    DBMS_CLOUD_AI_AGENT.DROP_TOOL (tool_name  => 'TRIAGEM_DBTIME');
    DBMS_CLOUD_AI_AGENT.DROP_TOOL (tool_name  => 'WAIT_CLASSES');
    DBMS_CLOUD_AI_AGENT.DROP_TOOL (tool_name  => 'TOP_EVENTOS');
    DBMS_CLOUD_AI_AGENT.DROP_TOOL (tool_name  => 'DECODIFICAR_EVENTO');
    DBMS_CLOUD_AI_AGENT.DROP_TOOL (tool_name  => 'SQL_DO_EVENTO');
END;
/

DROP FUNCTION wait_fn_triagem;
DROP FUNCTION wait_fn_classes;
DROP FUNCTION wait_fn_top_eventos;
DROP FUNCTION wait_fn_decodificar;
DROP FUNCTION wait_fn_sql_do_evento;

-- NAO derrube o HR_OPENAI aqui: ele e compartilhado com o lab do HR.
-- Para remover tambem o profile:  EXEC DBMS_CLOUD_AI.DROP_PROFILE('HR_OPENAI');


--------------------------------------------------------------------------------
--  LIMITES CONHECIDOS DESTA PROVA DE CONCEITO
--------------------------------------------------------------------------------
--  1. DELTA DE SNAPSHOT. O principio 5 da skill exige delta entre dois pontos,
--     mas cada tool devolve um valor cumulativo desde o startup. As tools
--     mitigam avisando no texto, mas a solucao correta e um par de tools
--     (TIRAR_SNAPSHOT + COMPARAR_SNAPSHOTS) com tabela propria. Nao implementado.
--
--  2. PRIVILEGIO FORA DO ADB. Ver PARTE 0. Role nao vale em definer's rights.
--
--  3. VISIBILIDADE NO ADB. V$SESSION mostra apenas o seu PDB, nao a instancia.
--     Por isso esta PoC nao inclui a tool de sessoes em tempo real nem a cadeia
--     de bloqueio (Etapas 5.1 e 5.2 da skill).
--
--  4. TAMANHO DA SAIDA. Toda tool faz top-N e agrega dentro do PL/SQL. Devolver
--     resultado cru enche o contexto do modelo e derruba a qualidade da conclusao.
--
--  5. O PARADOXO OPERACIONAL. O banco esta lento e voce vai rodar 5 a 7 chamadas
--     de LLM com saida de rede de dentro dele. Em incidente agudo isso e carga
--     extra e dependencia externa no pior momento. Esta PoC serve melhor para
--     ANALISE FORENSE (depois do incidente) ou rodando em um banco de gestao
--     separado que le o alvo por DB link.
--
--  6. E TRIAGEM, NAO LAUDO. O agente entrega "para onde olhar" e a primeira
--     hipotese. A skill entrega laudo com arvore de decisao atualizada a cada
--     evidencia. Uma instruction de task nao tem o poder de prescricao de uma
--     skill de 461 linhas. Use o agente para a triagem e a skill para o
--     aprofundamento.
--------------------------------------------------------------------------------
