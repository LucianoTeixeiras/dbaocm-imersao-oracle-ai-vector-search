CREATE OR REPLACE PACKAGE HR.PKG_FOLHA_RH AS
  ----------------------------------------------------------------------------
  -- Rotinas de folha e reajuste                              RH / Sistemas
  -- Criado em 2009. Alterado por varias pessoas desde entao.
  -- Documentacao: ver pasta da rede (link quebrado)
  ----------------------------------------------------------------------------

  PROCEDURE processar_folha_mensal(p_mes IN VARCHAR2 DEFAULT NULL);

  PROCEDURE reajuste_anual(p_ano IN NUMBER);

  PROCEDURE promover(p_employee_id IN NUMBER,
                     p_job_id      IN VARCHAR2,
                     p_dept_id     IN NUMBER DEFAULT NULL);

  PROCEDURE fechar_departamento(p_dept_id IN NUMBER);

  FUNCTION relatorio_folha(p_mes IN VARCHAR2) RETURN SYS_REFCURSOR;

  -- precisa ser publica: e chamada de dentro de SQL no corpo (L316 e L340)
  FUNCTION tempo_de_casa(p_employee_id IN NUMBER) RETURN NUMBER;

END PKG_FOLHA_RH;
/
