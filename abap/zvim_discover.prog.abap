*&---------------------------------------------------------------------*
*& ZVIM_DISCOVER - read-only report to confirm the names ZCL_VIM_ADAPTER assumes.
*& 1) lists VIM tables; 2) lists fields of a chosen table; 3) shows sample rows.
*&---------------------------------------------------------------------*
REPORT zvim_discover.

PARAMETERS: p_ns    TYPE c LENGTH 20 DEFAULT '/OPT/VIM%',
            p_table TYPE tabname,
            p_rows  TYPE i DEFAULT 5.

START-OF-SELECTION.
  DATA: lt_tabs  TYPE STANDARD TABLE OF dd02t,
        ls_tab   TYPE dd02t,
        lt_flds  TYPE STANDARD TABLE OF dd03l,
        ls_fld   TYPE dd03l,
        lr_tab   TYPE REF TO data.
  FIELD-SYMBOLS: <lt> TYPE STANDARD TABLE, <row> TYPE any, <f> TYPE any.

  IF p_table IS INITIAL.
    SELECT * FROM dd02t INTO TABLE lt_tabs
      WHERE tabname LIKE p_ns AND ddlanguage = sy-langu ORDER BY tabname.
    LOOP AT lt_tabs INTO ls_tab.
      WRITE: / ls_tab-tabname, ls_tab-ddtext.
    ENDLOOP.
    SKIP.
    WRITE: / 'Re-run with P_TABLE to see fields and sample rows.'.
    RETURN.
  ENDIF.

  SELECT * FROM dd03l INTO TABLE lt_flds
    WHERE tabname = p_table AND as4local = 'A' ORDER BY position.
  LOOP AT lt_flds INTO ls_fld.
    WRITE: / ls_fld-fieldname, ls_fld-keyflag, ls_fld-rollname, ls_fld-datatype, ls_fld-leng.
  ENDLOOP.
  SKIP.
  WRITE: / 'Sample rows (check which field marks an open exception, and where the reason code is):'.
  CREATE DATA lr_tab TYPE STANDARD TABLE OF (p_table).
  ASSIGN lr_tab->* TO <lt>.
  SELECT * FROM (p_table) INTO TABLE <lt> UP TO p_rows ROWS.
  LOOP AT <lt> ASSIGNING <row>.
    SKIP.
    LOOP AT lt_flds INTO ls_fld.
      ASSIGN COMPONENT ls_fld-fieldname OF STRUCTURE <row> TO <f>.
      IF sy-subrc = 0.
        WRITE: / ls_fld-fieldname, <f>.
      ENDIF.
    ENDLOOP.
  ENDLOOP.
