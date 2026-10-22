*&---------------------------------------------------------------------*
*& Z_VIM_ACT_POST_NOTE - wrapper for action POST_NOTE (see docs/VIM_ADAPTER.md)
*& Create in SE37 in a new function group (e.g. ZVIM_ACT). DRAFT, UNTESTED. NW 7.40+.
*&
*& The only unknown is the VIM note call in section 5. Until it is filled in,
*& the module raises FAILED, so nothing is silently dropped.
*& Maintain ZVIM_ACT_MAP: ACTION=POST_NOTE, FUNCNAME=Z_VIM_ACT_POST_NOTE, ACTIVE=X.
*&---------------------------------------------------------------------*
FUNCTION z_vim_act_post_note.
*"----------------------------------------------------------------------
*"  IMPORTING
*"     VALUE(IV_DOCID)       TYPE  C
*"     VALUE(IV_PARAMS_JSON) TYPE  STRING      " for POST_NOTE: the plain note text
*"  EXPORTING
*"     VALUE(EV_MESSAGE)     TYPE  STRING
*"  EXCEPTIONS
*"     REJECTED
*"     FAILED
*"----------------------------------------------------------------------
  CONSTANTS: lc_prefix  TYPE string VALUE `[AI agent] `,   " backticks keep the trailing blank
             lc_max_len TYPE i VALUE 2000.
  DATA: lv_text  TYPE string,
        lv_docid TYPE c LENGTH 20,
        lv_len   TYPE i.

  " 1. authorization: dedicated object, so this action can be granted separately.
  "    Create object ZVIM_ACT with field ZACTION in SU21; give the technical user
  "    only the actions it needs.
  AUTHORITY-CHECK OBJECT 'ZVIM_ACT' ID 'ZACTION' FIELD 'POST_NOTE'.
  IF sy-subrc <> 0.
    ev_message = 'Not authorized for POST_NOTE'.
    RAISE rejected.
  ENDIF.

  " 2. input validation (this module does not trust its caller)
  lv_docid = iv_docid.
  IF lv_docid IS INITIAL.
    ev_message = 'Document id is empty'.
    RAISE rejected.
  ENDIF.

  lv_text = iv_params_json.
  " control chars (incl. line breaks) -> blank; collapse; the text may carry model output
  REPLACE ALL OCCURRENCES OF REGEX '[[:cntrl:]]' IN lv_text WITH ` `.
  CONDENSE lv_text.
  IF lv_text IS INITIAL.
    ev_message = 'Note text is empty'.
    RAISE rejected.
  ENDIF.

  " 3. provenance: every machine-written note is visibly marked
  IF lv_text NP '[AI agent]*'.
    lv_text = lc_prefix && lv_text.
  ENDIF.
  lv_len = strlen( lv_text ).
  IF lv_len > lc_max_len.
    lv_text = substring( val = lv_text off = 0 len = lc_max_len - 3 ) && '...'.
  ENDIF.

  " 4. the document must exist (adapter checks too; this module stands alone)
  "    TODO(VIM): confirm the header table/field, same values as LOAD_CONFIG in ZCL_VIM_ADAPTER
  "    SELECT SINGLE docid FROM /opt/vim_1head INTO lv_docid WHERE docid = lv_docid.
  "    IF sy-subrc <> 0. ev_message = |Document { iv_docid } not found|. RAISE rejected. ENDIF.

  " 5. ---- THE ONE VIM-SPECIFIC CALL ------------------------------------
  "    TODO(VIM): add the note to the DP document using VIM's own note/comment API so it
  "    appears in the VIM cockpit/document history. Find it by:
  "      - in VIM, add a note manually, then trace it (ST05 / SE37 where-used / debugger on
  "        the "Add note" button) to see the function module or class VIM calls; or
  "      - check the OpenText VIM developer/API guide for your version.
  "    Call it inside the TRY/CATCH or EXCEPTIONS handling below and do NOT write VIM tables directly.
  "
  "    CALL FUNCTION '<VIM_NOTE_FM>'
  "      EXPORTING  docid = lv_docid  note_text = lv_text
  "      EXCEPTIONS OTHERS = 1.
  "    IF sy-subrc <> 0.
  "      ROLLBACK WORK.
  "      ev_message = 'VIM refused the note'.
  "      RAISE failed.
  "    ENDIF.
  "
  "    Fail closed until the call above is implemented:
  ev_message = 'POST_NOTE wrapper: VIM note call not implemented yet'.
  RAISE failed.

  " 6. commit only after the VIM call succeeded (unreachable until section 5 is implemented)
  COMMIT WORK.
  ev_message = |Note posted on { lv_docid }|.
ENDFUNCTION.
