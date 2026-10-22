*&---------------------------------------------------------------------*
*& ZCL_VIM_ADAPTER - VIM-specific implementation of ZIF_VIM_ADAPTER.
*&
*& DRAFT, UNTESTED. Every VIM-specific name lives in LOAD_CONFIG below and is
*& marked ASSUMED. Run ZVIM_DISCOVER in your system, correct the config, then
*& activate in a sandbox client. NW 7.31-compatible syntax.
*&
*& Writes never touch VIM tables directly: each agent action is mapped (table
*& ZVIM_ACT_MAP) to a wrapper function module YOU write around VIM's standard
*& action API, with the fixed signature documented in docs/VIM_ADAPTER.md.
*&---------------------------------------------------------------------*
CLASS zcl_vim_adapter DEFINITION PUBLIC FINAL CREATE PUBLIC.
  PUBLIC SECTION.
    INTERFACES zif_vim_adapter.
    METHODS constructor.

  PRIVATE SECTION.
    TYPES:
      BEGIN OF ty_cfg,
        header_table   TYPE tabname,     " DP document header
        docid_field    TYPE fieldname,
        bukrs_field    TYPE fieldname,
        lifnr_field    TYPE fieldname,
        amount_field   TYPE fieldname,
        waers_field    TYPE fieldname,
        ebeln_field    TYPE fieldname,   " PO number on header; may be initial
        xblnr_field    TYPE fieldname,
        status_field   TYPE fieldname,   " field that marks an open exception
        open_status    TYPE string,      " value meaning open
        reason_field   TYPE fieldname,   " field holding the exception reason code
        notes_field    TYPE fieldname,   " OCR/email text; may be initial
      END OF ty_cfg.
    DATA mv_cfg TYPE ty_cfg.

    METHODS load_config.
    METHODS read_header
      IMPORTING iv_doc_id TYPE c
      EXPORTING ev_found  TYPE abap_bool.
    METHODS call_mapped_action
      IMPORTING iv_map_key      TYPE zvim_act_map-action
                iv_doc_id       TYPE c
                iv_params_json  TYPE string
                iv_dry_run      TYPE abap_bool
      EXPORTING ev_message      TYPE string
      EXCEPTIONS rejected failed not_implemented.
ENDCLASS.

CLASS zcl_vim_adapter IMPLEMENTATION.

  METHOD constructor.
    load_config( ).
  ENDMETHOD.

  METHOD load_config.
    " ---- ASSUMED: verify every value with ZVIM_DISCOVER before activating ----
    mv_cfg-header_table  = '/OPT/VIM_1HEAD'.   " ASSUMED DP header table
    mv_cfg-docid_field   = 'DOCID'.            " ASSUMED
    mv_cfg-bukrs_field   = 'BUKRS'.            " ASSUMED
    mv_cfg-lifnr_field   = 'LIFNR'.            " ASSUMED
    mv_cfg-amount_field  = 'RMWWR'.            " ASSUMED gross invoice amount
    mv_cfg-waers_field   = 'WAERS'.            " ASSUMED
    mv_cfg-ebeln_field   = 'EBELN'.            " ASSUMED (may not exist -> leave blank)
    mv_cfg-xblnr_field   = 'XBLNR'.            " ASSUMED
    mv_cfg-status_field  = 'DOCSTATUS'.        " ASSUMED how "open exception" is represented;
    mv_cfg-open_status   = 'OPEN'.             " ASSUMED - your VIM may use status codes or
    mv_cfg-reason_field  = 'PROCESS_TYPE'.     " work items (SWWWIHEAD) instead; adapt read logic
    mv_cfg-notes_field   = ''.                 " set when OCR/email text location is known
  ENDMETHOD.

  METHOD read_header.
    DATA: lr_tab TYPE REF TO data,
          lv_where TYPE string,
          lv_id    TYPE string.
    FIELD-SYMBOLS <lt> TYPE STANDARD TABLE.
    ev_found = abap_false.
    CREATE DATA lr_tab TYPE STANDARD TABLE OF (mv_cfg-header_table).
    ASSIGN lr_tab->* TO <lt>.
    lv_id = cl_abap_dyn_prg=>quote( iv_doc_id ).
    CONCATENATE mv_cfg-docid_field ' = ' lv_id INTO lv_where.
    SELECT * FROM (mv_cfg-header_table) INTO TABLE <lt> UP TO 1 ROWS WHERE (lv_where).
    IF sy-subrc = 0.
      ev_found = abap_true.
    ENDIF.
  ENDMETHOD.

  METHOD zif_vim_adapter~get_open_exceptions.
    DATA: lr_tab   TYPE REF TO data,
          lv_where TYPE string,
          lv_open  TYPE string,
          ls_exc   TYPE zif_vim_adapter=>ty_exception.
    FIELD-SYMBOLS: <lt>  TYPE STANDARD TABLE,
                   <row> TYPE any,
                   <f>   TYPE any.
    CLEAR et_exc.

    CREATE DATA lr_tab TYPE STANDARD TABLE OF (mv_cfg-header_table).
    ASSIGN lr_tab->* TO <lt>.
    lv_open = cl_abap_dyn_prg=>quote( mv_cfg-open_status ).
    CONCATENATE mv_cfg-status_field ' = ' lv_open INTO lv_where.
    SELECT * FROM (mv_cfg-header_table) INTO TABLE <lt> UP TO iv_top ROWS WHERE (lv_where).

    LOOP AT <lt> ASSIGNING <row>.
      CLEAR ls_exc.
      ASSIGN COMPONENT mv_cfg-docid_field OF STRUCTURE <row> TO <f>.
      IF sy-subrc <> 0. CONTINUE. ENDIF.
      ls_exc-doc_id = <f>.
      ASSIGN COMPONENT mv_cfg-bukrs_field  OF STRUCTURE <row> TO <f>. IF sy-subrc = 0. ls_exc-comp_code   = <f>. ENDIF.
      ASSIGN COMPONENT mv_cfg-lifnr_field  OF STRUCTURE <row> TO <f>. IF sy-subrc = 0. ls_exc-vendor      = <f>. ENDIF.
      ASSIGN COMPONENT mv_cfg-amount_field OF STRUCTURE <row> TO <f>. IF sy-subrc = 0. ls_exc-amount      = <f>. ENDIF.
      ASSIGN COMPONENT mv_cfg-waers_field  OF STRUCTURE <row> TO <f>. IF sy-subrc = 0. ls_exc-currency    = <f>. ENDIF.
      ASSIGN COMPONENT mv_cfg-ebeln_field  OF STRUCTURE <row> TO <f>. IF sy-subrc = 0. ls_exc-po_number   = <f>. ENDIF.
      ASSIGN COMPONENT mv_cfg-xblnr_field  OF STRUCTURE <row> TO <f>. IF sy-subrc = 0. ls_exc-invoice_ref = <f>. ENDIF.
      ASSIGN COMPONENT mv_cfg-reason_field OF STRUCTURE <row> TO <f>. IF sy-subrc = 0. ls_exc-reason_code = <f>. ENDIF.
      IF mv_cfg-notes_field IS NOT INITIAL.
        ASSIGN COMPONENT mv_cfg-notes_field OF STRUCTURE <row> TO <f>. IF sy-subrc = 0. ls_exc-notes = <f>. ENDIF.
      ENDIF.
      ls_exc-status = 'OPEN'.
      APPEND ls_exc TO et_exc.
    ENDLOOP.
  ENDMETHOD.

  METHOD zif_vim_adapter~post_note.
    DATA lv_msg TYPE string.
    call_mapped_action(
      EXPORTING iv_map_key = 'POST_NOTE' iv_doc_id = iv_doc_id iv_params_json = iv_text iv_dry_run = abap_false
      IMPORTING ev_message = lv_msg
      EXCEPTIONS rejected = 1 failed = 2 not_implemented = 3 OTHERS = 4 ).
    CASE sy-subrc.
      WHEN 0.
      WHEN 3. RAISE not_implemented.
      WHEN OTHERS. RAISE failed.
    ENDCASE.
  ENDMETHOD.

  METHOD zif_vim_adapter~execute_action.
    CALL METHOD call_mapped_action
      EXPORTING
        iv_map_key     = iv_action
        iv_doc_id      = iv_doc_id
        iv_params_json = iv_params_json
        iv_dry_run     = iv_dry_run
      IMPORTING
        ev_message     = ev_message
      EXCEPTIONS
        rejected       = 1
        failed         = 2
        not_implemented = 3
        OTHERS         = 4.
    CASE sy-subrc.
      WHEN 0.
      WHEN 1. RAISE rejected.
      WHEN 3. RAISE not_implemented.
      WHEN OTHERS. RAISE failed.
    ENDCASE.
  ENDMETHOD.

  METHOD call_mapped_action.
    DATA: ls_map TYPE zvim_act_map,
          lv_fm  TYPE rs38l_fnam,
          lv_found TYPE abap_bool.

    " 1. allowlist: action must be maintained AND active in customizing
    SELECT SINGLE * FROM zvim_act_map INTO ls_map WHERE action = iv_map_key.
    IF sy-subrc <> 0 OR ls_map-active <> 'X'.
      ev_message = |Action { iv_map_key } is not enabled in ZVIM_ACT_MAP|.
      RAISE rejected.
    ENDIF.
    lv_fm = ls_map-funcname.
    CALL FUNCTION 'FUNCTION_EXISTS'
      EXPORTING funcname = lv_fm
      EXCEPTIONS function_not_exist = 1 OTHERS = 2.
    IF sy-subrc <> 0.
      ev_message = |Wrapper { lv_fm } does not exist|.
      RAISE not_implemented.
    ENDIF.

    " 2. the document must exist and still be an open exception (no double processing)
    read_header( EXPORTING iv_doc_id = iv_doc_id IMPORTING ev_found = lv_found ).
    IF lv_found = abap_false.
      ev_message = |Document { iv_doc_id } not found|.
      RAISE rejected.
    ENDIF.

    " 3. dry run: everything above passed, change nothing
    IF iv_dry_run = abap_true.
      ev_message = |DRY RUN ok: { iv_map_key } -> { lv_fm }|.
      RETURN.
    ENDIF.

    " 4. real execution through YOUR wrapper around VIM's standard action.
    "    The wrapper owns authorization, VIM API call and COMMIT WORK.
    CALL FUNCTION lv_fm
      EXPORTING
        iv_docid       = iv_doc_id
        iv_params_json = iv_params_json
      IMPORTING
        ev_message     = ev_message
      EXCEPTIONS
        rejected       = 1
        failed         = 2
        OTHERS         = 3.
    CASE sy-subrc.
      WHEN 0.
      WHEN 1. RAISE rejected.
      WHEN OTHERS. RAISE failed.
    ENDCASE.
  ENDMETHOD.

ENDCLASS.
