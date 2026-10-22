*&---------------------------------------------------------------------*
*& ZCL_VIM_AGENT_API - read layer for the VIM exception agent (ECC).
*& Plain SELECTs on standard tables; called from the SEGW DPC_EXT class.
*& Written in NW 7.31-compatible syntax (no inline declarations).
*& UNTESTED - activate in a sandbox, add ABAP Unit, review authorizations.
*&---------------------------------------------------------------------*
CLASS zcl_vim_agent_api DEFINITION PUBLIC FINAL CREATE PUBLIC.
  PUBLIC SECTION.
    TYPES:
      BEGIN OF ty_po_header,
        po_number TYPE ekko-ebeln,
        vendor    TYPE ekko-lifnr,
        comp_code TYPE ekko-bukrs,
        currency  TYPE ekko-waers,
        doc_type  TYPE ekko-bsart,
      END OF ty_po_header,
      BEGIN OF ty_po_item,
        po_number TYPE ekpo-ebeln,
        item      TYPE ekpo-ebelp,
        material  TYPE ekpo-matnr,
        text      TYPE ekpo-txz01,
        quantity  TYPE ekpo-menge,
        uom       TYPE ekpo-meins,
        net_price TYPE ekpo-netpr,
        price_unit TYPE ekpo-peinh,
        plant     TYPE ekpo-werks,
      END OF ty_po_item,
      tt_po_item TYPE STANDARD TABLE OF ty_po_item WITH DEFAULT KEY,
      BEGIN OF ty_gr,
        po_number TYPE ekbe-ebeln,
        item      TYPE ekbe-ebelp,
        doc       TYPE ekbe-belnr,
        year      TYPE ekbe-gjahr,
        post_date TYPE ekbe-budat,
        quantity  TYPE ekbe-menge,
        uom       TYPE ekbe-meins,
      END OF ty_gr,
      tt_gr TYPE STANDARD TABLE OF ty_gr WITH DEFAULT KEY,
      BEGIN OF ty_vendor,
        vendor      TYPE lfa1-lifnr,
        name        TYPE lfa1-name1,
        risk_flag   TYPE abap_bool,
        recent_bank_change TYPE abap_bool,
      END OF ty_vendor,
      BEGIN OF ty_invoice,
        doc_number  TYPE rbkp-belnr,
        fiscal_year TYPE rbkp-gjahr,
        vendor      TYPE rbkp-lifnr,
        invoice_ref TYPE rbkp-xblnr,
        amount      TYPE rbkp-rmwwr,
        currency    TYPE rbkp-waers,
        doc_date    TYPE rbkp-bldat,
      END OF ty_invoice,
      tt_invoice TYPE STANDARD TABLE OF ty_invoice WITH DEFAULT KEY,
      BEGIN OF ty_coding_line,
        vendor       TYPE lfa1-lifnr,
        company_code TYPE bseg-bukrs,
        doc_number   TYPE bseg-belnr,
        fiscal_year  TYPE bseg-gjahr,
        line_no      TYPE bseg-buzei,
        posting_date TYPE bkpf-budat,
        invoice_ref  TYPE bkpf-xblnr,
        currency     TYPE bkpf-waers,
        gl           TYPE bseg-hkont,
        cost_center  TYPE bseg-kostl,
        order        TYPE bseg-aufnr,
        wbs          TYPE c LENGTH 24,
        profit_center TYPE bseg-prctr,
        tax_code     TYPE bseg-mwskz,
        amount       TYPE bseg-wrbtr,       " signed: debit +, credit -
        text         TYPE bseg-sgtxt,
      END OF ty_coding_line,
      tt_coding_line TYPE STANDARD TABLE OF ty_coding_line WITH DEFAULT KEY,
      BEGIN OF ty_check_in,
        gl TYPE hkont, cost_center TYPE kostl, order TYPE aufnr, wbs TYPE c LENGTH 24,
        profit_center TYPE prctr, tax_code TYPE mwskz,
      END OF ty_check_in,
      BEGIN OF ty_check_result,
        field  TYPE c LENGTH 20,
        value  TYPE c LENGTH 24,
        ok     TYPE abap_bool,
        reason TYPE c LENGTH 80,
      END OF ty_check_result,
      tt_check_result TYPE STANDARD TABLE OF ty_check_result WITH DEFAULT KEY.

    "! Expense coding of the vendor's FI invoices, newest first (max iv_max_docs documents, last iv_months months).
    "! PO-based MIRO invoices post to GR/IR (KTOSL WRX) and have no expense lines here, so they drop out by design:
    "! this is the non-PO coding history.
    CLASS-METHODS get_coding_history
      IMPORTING iv_vendor   TYPE lfa1-lifnr
                iv_bukrs    TYPE bukrs
                iv_max_docs TYPE i DEFAULT 100
                iv_months   TYPE i DEFAULT 18
      EXPORTING et_lines    TYPE tt_coding_line.
    "! One row per supplied field; unsupported/unchecked fields are reported ok = false (fail closed).
    CLASS-METHODS validate_coding
      IMPORTING iv_bukrs   TYPE bukrs
                is_line    TYPE ty_check_in
                iv_date    TYPE sy-datum
      EXPORTING et_results TYPE tt_check_result.

    CLASS-METHODS get_po
      IMPORTING iv_po     TYPE ekko-ebeln
      EXPORTING es_header TYPE ty_po_header
                et_items  TYPE tt_po_item
                ev_found  TYPE abap_bool.
    CLASS-METHODS get_goods_receipts
      IMPORTING iv_po TYPE ekko-ebeln
      EXPORTING et_gr TYPE tt_gr.
    CLASS-METHODS get_vendor
      IMPORTING iv_vendor TYPE lfa1-lifnr
      EXPORTING es_vendor TYPE ty_vendor
                ev_found  TYPE abap_bool.
    "! AND semantics: every supplied (non-initial) criterion must match.
    CLASS-METHODS find_invoices
      IMPORTING iv_vendor TYPE rbkp-lifnr
                iv_ref    TYPE rbkp-xblnr OPTIONAL
                iv_amount TYPE rbkp-rmwwr OPTIONAL
      EXPORTING et_inv    TYPE tt_invoice.
    "! Returns P (purchase order), G (goods receipt), I (invoice) or space.
    CLASS-METHODS doc_type
      IMPORTING iv_doc         TYPE char10
      RETURNING VALUE(rv_type) TYPE char1.
ENDCLASS.

CLASS zcl_vim_agent_api IMPLEMENTATION.

  METHOD get_po.
    DATA ls_ekko TYPE ekko.
    CLEAR: es_header, et_items.
    ev_found = abap_false.
    SELECT SINGLE * FROM ekko INTO ls_ekko WHERE ebeln = iv_po.
    IF sy-subrc <> 0.
      RETURN.
    ENDIF.
    AUTHORITY-CHECK OBJECT 'M_BEST_BSA'
      ID 'ACTVT' FIELD '03'
      ID 'BSART' FIELD ls_ekko-bsart.
    IF sy-subrc <> 0.
      RETURN.
    ENDIF.
    es_header-po_number = ls_ekko-ebeln.
    es_header-vendor    = ls_ekko-lifnr.
    es_header-comp_code = ls_ekko-bukrs.
    es_header-currency  = ls_ekko-waers.
    es_header-doc_type  = ls_ekko-bsart.
    SELECT ebeln ebelp matnr txz01 menge meins netpr peinh werks
      FROM ekpo
      INTO TABLE et_items
      WHERE ebeln = iv_po
        AND loekz = space.
    ev_found = abap_true.
  ENDMETHOD.

  METHOD get_goods_receipts.
    " VGABE '1' = goods receipt; reversals (VGABE '2') are not netted here - see README
    SELECT ebeln ebelp belnr gjahr budat menge meins
      FROM ekbe
      INTO TABLE et_gr
      WHERE ebeln = iv_po
        AND vgabe = '1'.
  ENDMETHOD.

  METHOD get_vendor.
    DATA: ls_lfa1  TYPE lfa1,
          lv_objid TYPE cdhdr-objectid,
          lv_from  TYPE sy-datum,
          lt_chg   TYPE STANDARD TABLE OF cdhdr-changenr,
          lv_chg   TYPE cdhdr-changenr,
          lv_hit   TYPE cdpos-changenr.
    CLEAR es_vendor.
    ev_found = abap_false.
    SELECT SINGLE * FROM lfa1 INTO ls_lfa1 WHERE lifnr = iv_vendor.
    IF sy-subrc <> 0.
      RETURN.
    ENDIF.
    es_vendor-vendor = ls_lfa1-lifnr.
    es_vendor-name   = ls_lfa1-name1.
    IF ls_lfa1-sperr = 'X' OR ls_lfa1-sperm = 'X' OR ls_lfa1-loevm = 'X' OR ls_lfa1-xcpdk = 'X'.
      es_vendor-risk_flag = abap_true.
    ENDIF.
    " bank detail (LFBK) change in the last 90 days via change documents
    lv_objid = iv_vendor.
    lv_from  = sy-datum - 90.
    SELECT changenr FROM cdhdr INTO TABLE lt_chg
      WHERE objectclas = 'KRED' AND objectid = lv_objid AND udate >= lv_from.
    LOOP AT lt_chg INTO lv_chg.
      SELECT SINGLE changenr FROM cdpos INTO lv_hit
        WHERE objectclas = 'KRED' AND objectid = lv_objid
          AND changenr = lv_chg AND tabname = 'LFBK'.
      IF sy-subrc = 0.
        es_vendor-recent_bank_change = abap_true.
        EXIT.
      ENDIF.
    ENDLOOP.
    ev_found = abap_true.
  ENDMETHOD.

  METHOD find_invoices.
    CLEAR et_inv.
    " RBSTAT 5 = posted; parked/held invoices are excluded on purpose
    SELECT belnr gjahr lifnr xblnr rmwwr waers bldat
      FROM rbkp
      INTO TABLE et_inv UP TO 50 ROWS
      WHERE lifnr = iv_vendor
        AND rbstat = '5'
        AND ( xblnr = iv_ref OR iv_ref = space )
        AND ( rmwwr = iv_amount OR iv_amount = 0 ).
  ENDMETHOD.

  METHOD doc_type.
    DATA lv_dummy TYPE c LENGTH 10.
    rv_type = space.
    SELECT SINGLE ebeln FROM ekko INTO lv_dummy WHERE ebeln = iv_doc.
    IF sy-subrc = 0.
      rv_type = 'P'.
      RETURN.
    ENDIF.
    SELECT SINGLE mblnr FROM mkpf INTO lv_dummy WHERE mblnr = iv_doc.
    IF sy-subrc = 0.
      rv_type = 'G'.
      RETURN.
    ENDIF.
    SELECT SINGLE belnr FROM rbkp INTO lv_dummy WHERE belnr = iv_doc.
    IF sy-subrc = 0.
      rv_type = 'I'.
    ENDIF.
  ENDMETHOD.

  METHOD get_coding_history.
    TYPES: BEGIN OF lty_item,
             belnr TYPE bsak-belnr, gjahr TYPE bsak-gjahr, budat TYPE bsak-budat,
             xblnr TYPE bsak-xblnr, waers TYPE bsak-waers,
           END OF lty_item.
    DATA: lt_item TYPE STANDARD TABLE OF lty_item,
          ls_item TYPE lty_item,
          lt_bseg TYPE STANDARD TABLE OF bseg,
          ls_bseg TYPE bseg,
          ls_line TYPE ty_coding_line,
          lv_from TYPE sy-datum,
          lv_docs TYPE i,
          lv_wbs  TYPE c LENGTH 24.
    CLEAR et_lines.
    lv_from = sy-datum - ( iv_months * 30 ).
    " vendor credit items (SHKZG H) without special GL indicator = invoices; open (BSIK) and cleared (BSAK)
    SELECT belnr gjahr budat xblnr waers FROM bsik INTO TABLE lt_item
      WHERE bukrs = iv_bukrs AND lifnr = iv_vendor AND budat >= lv_from AND shkzg = 'H' AND umskz = space.
    SELECT belnr gjahr budat xblnr waers FROM bsak APPENDING TABLE lt_item
      WHERE bukrs = iv_bukrs AND lifnr = iv_vendor AND budat >= lv_from AND shkzg = 'H' AND umskz = space.
    SORT lt_item BY budat DESCENDING belnr DESCENDING.
    DELETE ADJACENT DUPLICATES FROM lt_item COMPARING belnr gjahr.
    LOOP AT lt_item INTO ls_item.
      lv_docs = lv_docs + 1.
      IF lv_docs > iv_max_docs.
        EXIT.
      ENDIF.
      CLEAR lt_bseg.
      " expense lines only: G/L (KOART S), no tax/clearing key (KTOSL initial)
      SELECT * FROM bseg INTO TABLE lt_bseg
        WHERE bukrs = iv_bukrs AND belnr = ls_item-belnr AND gjahr = ls_item-gjahr
          AND koart = 'S' AND ktosl = space.
      LOOP AT lt_bseg INTO ls_bseg.
        CLEAR: ls_line, lv_wbs.
        ls_line-vendor = iv_vendor.      ls_line-company_code = iv_bukrs.
        ls_line-doc_number = ls_item-belnr. ls_line-fiscal_year = ls_item-gjahr.
        ls_line-line_no = ls_bseg-buzei. ls_line-posting_date = ls_item-budat.
        ls_line-invoice_ref = ls_item-xblnr. ls_line-currency = ls_item-waers.
        ls_line-gl = ls_bseg-hkont.      ls_line-cost_center = ls_bseg-kostl.
        ls_line-order = ls_bseg-aufnr.   ls_line-profit_center = ls_bseg-prctr.
        ls_line-tax_code = ls_bseg-mwskz. ls_line-text = ls_bseg-sgtxt.
        IF ls_bseg-ps_psp_pnr IS NOT INITIAL.
          CALL FUNCTION 'CONVERSION_EXIT_ABPSP_OUTPUT'
            EXPORTING input = ls_bseg-ps_psp_pnr IMPORTING output = lv_wbs.
          ls_line-wbs = lv_wbs.
        ENDIF.
        IF ls_bseg-shkzg = 'H'.
          ls_line-amount = ls_bseg-wrbtr * -1.
        ELSE.
          ls_line-amount = ls_bseg-wrbtr.
        ENDIF.
        APPEND ls_line TO et_lines.
      ENDLOOP.
    ENDLOOP.
  ENDMETHOD.

  METHOD validate_coding.
    DATA: ls_res   TYPE ty_check_result,
          lv_ktopl TYPE t001-ktopl,
          lv_land  TYPE t001-land1,
          lv_kokrs TYPE tka02-kokrs,
          lv_kalsm TYPE t005-kalsm,
          lv_gl    TYPE skb1-saknr,
          lv_cc    TYPE csks-kostl,
          lv_ord   TYPE aufk-aufnr,
          lv_pc    TYPE cepc-prctr,
          ls_skb1  TYPE skb1,
          ls_ska1  TYPE ska1,
          ls_csks  TYPE csks,
          ls_aufk  TYPE aufk,
          lv_dummy TYPE c LENGTH 1.
    CLEAR et_results.
    SELECT SINGLE ktopl land1 FROM t001 INTO (lv_ktopl, lv_land) WHERE bukrs = iv_bukrs.
    IF sy-subrc <> 0.
      ls_res-field = 'company_code'. ls_res-value = iv_bukrs. ls_res-reason = 'unknown company code'.
      APPEND ls_res TO et_results.
      RETURN.
    ENDIF.
    SELECT SINGLE kokrs FROM tka02 INTO lv_kokrs WHERE bukrs = iv_bukrs.

    IF is_line-gl IS NOT INITIAL.
      lv_gl = is_line-gl.
      CALL FUNCTION 'CONVERSION_EXIT_ALPHA_INPUT' EXPORTING input = lv_gl IMPORTING output = lv_gl.
      CLEAR ls_res. ls_res-field = 'gl'. ls_res-value = is_line-gl.
      SELECT SINGLE * FROM skb1 INTO ls_skb1 WHERE bukrs = iv_bukrs AND saknr = lv_gl.
      IF sy-subrc <> 0.
        ls_res-reason = 'account not created in company code'.
      ELSEIF ls_skb1-xspeb = 'X' OR ls_skb1-xloeb = 'X'.
        ls_res-reason = 'account blocked or flagged for deletion in company code'.
      ELSE.
        SELECT SINGLE * FROM ska1 INTO ls_ska1 WHERE ktopl = lv_ktopl AND saknr = lv_gl.
        IF sy-subrc <> 0.
          ls_res-reason = 'account not in chart of accounts'.
        ELSEIF ls_ska1-xspeb = 'X'.
          ls_res-reason = 'account blocked in chart of accounts'.
        ELSE.
          ls_res-ok = abap_true.
        ENDIF.
      ENDIF.
      APPEND ls_res TO et_results.
    ENDIF.

    IF is_line-cost_center IS NOT INITIAL.
      lv_cc = is_line-cost_center.
      CALL FUNCTION 'CONVERSION_EXIT_ALPHA_INPUT' EXPORTING input = lv_cc IMPORTING output = lv_cc.
      CLEAR ls_res. ls_res-field = 'cost_center'. ls_res-value = is_line-cost_center.
      SELECT SINGLE * FROM csks INTO ls_csks
        WHERE kokrs = lv_kokrs AND kostl = lv_cc AND datbi >= iv_date AND datab <= iv_date.
      IF sy-subrc <> 0.
        ls_res-reason = 'cost center not valid on posting date'.
      ELSEIF ls_csks-bukrs <> iv_bukrs.
        ls_res-reason = 'cost center belongs to another company code'.
      ELSEIF ls_csks-bkzkp = 'X'.
        ls_res-reason = 'cost center locked for primary postings'.
      ELSE.
        ls_res-ok = abap_true.
      ENDIF.
      APPEND ls_res TO et_results.
    ENDIF.

    IF is_line-order IS NOT INITIAL.
      lv_ord = is_line-order.
      CALL FUNCTION 'CONVERSION_EXIT_ALPHA_INPUT' EXPORTING input = lv_ord IMPORTING output = lv_ord.
      CLEAR ls_res. ls_res-field = 'order'. ls_res-value = is_line-order.
      SELECT SINGLE * FROM aufk INTO ls_aufk WHERE aufnr = lv_ord.
      IF sy-subrc <> 0.
        ls_res-reason = 'order not found'.
      ELSEIF ls_aufk-bukrs <> iv_bukrs.
        ls_res-reason = 'order belongs to another company code'.
      ELSEIF ls_aufk-loekz = 'X'.
        ls_res-reason = 'order flagged for deletion'.
      ELSE.
        " TODO: also check system status (released / not closed / no posting lock) via JEST
        ls_res-ok = abap_true.
      ENDIF.
      APPEND ls_res TO et_results.
    ENDIF.

    IF is_line-profit_center IS NOT INITIAL.
      lv_pc = is_line-profit_center.
      CALL FUNCTION 'CONVERSION_EXIT_ALPHA_INPUT' EXPORTING input = lv_pc IMPORTING output = lv_pc.
      CLEAR ls_res. ls_res-field = 'profit_center'. ls_res-value = is_line-profit_center.
      SELECT SINGLE prctr FROM cepc INTO lv_pc
        WHERE prctr = lv_pc AND kokrs = lv_kokrs AND datbi >= iv_date AND datab <= iv_date.
      IF sy-subrc = 0.
        ls_res-ok = abap_true.
      ELSE.
        ls_res-reason = 'profit center not valid on posting date'.
      ENDIF.
      APPEND ls_res TO et_results.
    ENDIF.

    IF is_line-tax_code IS NOT INITIAL.
      CLEAR ls_res. ls_res-field = 'tax_code'. ls_res-value = is_line-tax_code.
      SELECT SINGLE kalsm FROM t005 INTO lv_kalsm WHERE land1 = lv_land.
      SELECT SINGLE mwskz FROM t007a INTO lv_dummy WHERE kalsm = lv_kalsm AND mwskz = is_line-tax_code.
      IF sy-subrc = 0.
        ls_res-ok = abap_true.
      ELSE.
        ls_res-reason = 'tax code not defined for the company code country'.
      ENDIF.
      APPEND ls_res TO et_results.
    ENDIF.

    IF is_line-wbs IS NOT INITIAL.     " not implemented: fail closed so a human looks at WBS coding
      CLEAR ls_res. ls_res-field = 'wbs'. ls_res-value = is_line-wbs.
      ls_res-reason = 'WBS element validation not implemented'.
      APPEND ls_res TO et_results.
    ENDIF.
  ENDMETHOD.

ENDCLASS.
