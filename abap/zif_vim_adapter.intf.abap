*& Seam for everything VIM-specific. Implemented per system because DP exception
*& storage, reason codes and action APIs differ by VIM version/configuration.
INTERFACE zif_vim_adapter PUBLIC.
  TYPES:
    BEGIN OF ty_exception,
      doc_id       TYPE c LENGTH 20,    " VIM DP document id (DOCID)
      comp_code    TYPE bukrs,
      vendor       TYPE lifnr,
      reason_code  TYPE c LENGTH 20,    " VIM exception / process reason code
      amount       TYPE p LENGTH 13 DECIMALS 2,
      currency     TYPE waers,
      po_number    TYPE ebeln,
      invoice_ref  TYPE xblnr1,
      notes        TYPE string,         " OCR / email text: UNTRUSTED
      status       TYPE c LENGTH 10,    " OPEN
    END OF ty_exception,
    tt_exception TYPE STANDARD TABLE OF ty_exception WITH DEFAULT KEY.

  METHODS get_open_exceptions
    IMPORTING iv_top TYPE i DEFAULT 50
    EXPORTING et_exc TYPE tt_exception
    EXCEPTIONS not_implemented.
  METHODS post_note
    IMPORTING iv_doc_id TYPE c
              iv_text   TYPE string
    EXCEPTIONS failed not_implemented.
  "! The ONLY write path. Must call VIM's standard action so workflow/history stay intact,
  "! must enforce its own allowlist of actions, and must honour iv_dry_run.
  METHODS execute_action
    IMPORTING iv_doc_id     TYPE c
              iv_action     TYPE c
              iv_params_json TYPE string
              iv_dry_run    TYPE abap_bool
    EXPORTING ev_message    TYPE string
    EXCEPTIONS rejected failed not_implemented.
ENDINTERFACE.
