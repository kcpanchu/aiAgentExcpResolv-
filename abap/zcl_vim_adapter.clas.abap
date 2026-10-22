*& TODO(VIM): implement against YOUR VIM version. Do not guess table/field names here -
*& inspect DP header/exception data and the action function modules in the system
*& (SE11/SE37, VIM IMG: "Define Exception Reason"/"Process Types") first.
CLASS zcl_vim_adapter DEFINITION PUBLIC FINAL CREATE PUBLIC.
  PUBLIC SECTION.
    INTERFACES zif_vim_adapter.
ENDCLASS.

CLASS zcl_vim_adapter IMPLEMENTATION.
  METHOD zif_vim_adapter~get_open_exceptions.
    " 1. read DP documents with an open exception work item (status filter)
    " 2. map vendor/company/amount/PO/invoice ref from the DP header
    " 3. fill reason_code from the exception reason; notes from OCR text
    RAISE not_implemented.
  ENDMETHOD.
  METHOD zif_vim_adapter~post_note.
    " use VIM's note/comment API on the DP document
    RAISE not_implemented.
  ENDMETHOD.
  METHOD zif_vim_adapter~execute_action.
    " allowlist iv_action; IF iv_dry_run = abap_true -> validate only, change nothing
    " else call VIM's standard process-option/action for the DP document
    RAISE not_implemented.
  ENDMETHOD.
ENDCLASS.
