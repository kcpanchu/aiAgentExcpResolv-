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
      tt_invoice TYPE STANDARD TABLE OF ty_invoice WITH DEFAULT KEY.

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

ENDCLASS.
