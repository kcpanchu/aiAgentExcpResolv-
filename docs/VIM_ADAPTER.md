# ZCL_VIM_ADAPTER: what you must confirm and build

The class is a draft (untested, unactivated). It is built so that nothing VIM-specific is hard-coded outside
`LOAD_CONFIG` and the wrapper function modules.

## Step 1: confirm the data model (30-60 min, a VIM-literate ABAPer)
1. Run `ZVIM_DISCOVER` (default lists `/OPT/VIM%` tables); then re-run with `P_TABLE` set to the DP header table.
2. Fill in `LOAD_CONFIG`: header table, field names, how an **open exception** is represented, where the **reason code** is.
3. If an open exception is a *workflow work item* rather than a header status, replace the `WHERE` in
   `get_open_exceptions` (join to the work item tables) - keep the output structure unchanged.
4. Decide where OCR/email text lives (`notes_field`); leave blank if unavailable. It is passed to the model as untrusted data.

## Step 2: customizing table `ZVIM_ACT_MAP` (create in SE11, client-dependent, maintenance via SM30)
| Field | Type | Meaning |
|---|---|---|
| MANDT | MANDT | key |
| ACTION | CHAR30 | key: `APPROVE_VARIANCE`, `REJECT_DUPLICATE`, `ROUTE_TO_BUYER`, `ASSIGN_PO`, `PARK_AND_RECHECK`, `POST_NOTE` |
| FUNCNAME | RS38L_FNAM | wrapper function module |
| ACTIVE | CHAR1 | `X` = allowed. Unmaintained or inactive = rejected. This is the server-side allowlist/kill switch |

Start with every row inactive except `POST_NOTE`. Enable actions one at a time as each reaches the auto phase.

## Step 3: write one wrapper FM per action (fixed signature)
```abap
FUNCTION z_vim_act_<name>.
*"  IMPORTING  VALUE(IV_DOCID)       TYPE C
*"             VALUE(IV_PARAMS_JSON) TYPE STRING
*"  EXPORTING  VALUE(EV_MESSAGE)     TYPE STRING
*"  EXCEPTIONS REJECTED FAILED
```
Each wrapper must:
- re-validate preconditions itself (open exception, amount/tolerance limits, SoD) - do not trust the caller
- call VIM's **standard** process-option/action API for the DP document so workflow, status and history stay correct
- run its own `AUTHORITY-CHECK`, then `COMMIT WORK` (or roll back and raise `FAILED`)
- raise `REJECTED` for business-rule refusals, `FAILED` for technical errors
- parse `IV_PARAMS_JSON` defensively (e.g. `/UI2/CL_JSON`); for `POST_NOTE` it carries the plain note text

## Step 4: wire into the Gateway service
`ExceptionSet`, `NoteSet`, `ActionSet` DPC_EXT methods instantiate `ZCL_VIM_ADAPTER` and call the three interface methods.
Map `ActionSet` results to `Ok`/`Message`; translate `rejected` to HTTP 409 and `failed` to 500 so the Python
side can tell policy refusals from outages.

## Safety properties the draft already enforces
- action must be in `ZVIM_ACT_MAP` and active (independent of the Python policy engine)
- wrapper must exist (checked before use, also in dry run)
- document must exist before acting; dry run validates all of this and changes nothing
- adapter never writes VIM tables directly

## Known limitations
- Open-exception detection is the biggest unknown: the draft assumes a header status field.
- Dynamic `SELECT *` on the header table is fine for polling but not tuned; add an index/field list if volume is high.
- No ABAP Unit yet: after config is confirmed, test with a fake header table via a test seam.
- `cl_abap_dyn_prg=>quote` and string templates (`|...|`) need NW 7.40+; on older stacks replace with `CONCATENATE` and manual quoting.
