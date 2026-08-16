#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, os, py_compile, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
TARGET=ROOT/'services'/'execution_engine.py'
MODULES=('services/stop_realisability.py','services/paper_live_parity.py','services/canary_governor.py')
PROC_HINTS=('execution_engine','market_intelligence','ws_price_oracle','neural_supervisor','system_guardian')

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def running():
    try:
        if os.name=='nt':
            out=subprocess.run(['wmic','process','where',"name='python.exe'",'get','commandline'],capture_output=True,text=True,timeout=20).stdout
        else: out=subprocess.run(['ps','-eo','args'],capture_output=True,text=True,timeout=20).stdout
        return [x.strip()[:140] for x in out.splitlines() if any(h in x.lower() for h in PROC_HINTS) and 'APPLY_LIVE_SIGNOFF' not in x]
    except Exception: return []

STOP_OLD='''        # receive a synthetic fill and continue to settle from chain truth.
        _hard_stop_exit_price = current_price
        _hard_stop_exit_reason = f"HARD_STOP_LOSS_{pnl_pct:.1f}pct"
'''
STOP_NEW=STOP_OLD+'''
        # STOP_REALISABILITY_PROBE_20260803_FINAL
        # Quote-only evidence. Never signs, builds, submits, or changes the close.
        try:
            if not _is_real_eval:
                from services.stop_realisability import probe_stop
                with get_connection() as _srp:
                    probe_stop(
                        _srp, position_id=position_id, mint=mint,
                        quantity=float(position.get("quantity") or 0.0),
                        entry_price=float(position.get("entry_price") or 0.0),
                        trigger_mark_price=float(current_price or 0.0),
                        intended_stop_pct=-abs(float(_hard_stop_pct)),
                        position_size_usd=float(position.get("position_size_usd") or 0.0),
                        credited_stop_pct=-abs(float(_hard_stop_pct)),
                        token_name=str(position.get("token_name") or ""),
                        mark_source=str(position.get("mark_source") or position.get("price_source") or ""),
                        snapshot_id=position.get("snapshot_id"),
                        trigger_mark_age_sec=((time.time()-float(position.get("last_marked_at")))
                                              if position.get("last_marked_at") else None),
                    )
                    _srp.commit()
        except Exception as _probe_exc:
            log.debug("[STOP_REALISABILITY_PROBE_FAIL] pos=%s %s", position_id, _probe_exc)
'''

PAPER_OLD='''                position_id = cur.lastrowid
'''
PAPER_NEW=PAPER_OLD+'''
                # PARITY_PAPER_ADMISSION_20260803_FINAL
                try:
                    from services.paper_live_parity import record as _plp_record, PAPER_ONLY_ADMITTED
                    _plp_record(conn, mint=mint, state=PAPER_ONLY_ADMITTED,
                                paper_position_id=int(position_id), paper_admitted=1,
                                paper_admitted_at=float(now), paper_entry_price=float(entry_price or 0.0),
                                token_name=str(token_name or ""), confidence=float(entry_conf or 0.0))
                except Exception:
                    pass
'''

DECISION_OLD='''                    authored_by="execution_engine.scan_for_entries",
                )
'''
DECISION_NEW=DECISION_OLD+'''
                # PARITY_LIVE_DECISION_20260803_FINAL
                try:
                    from services.paper_live_parity import record as _plp_record, LIVE_REFUSED, PAPER_ONLY_ADMITTED
                    _plp_state = PAPER_ONLY_ADMITTED if _ldc_verdict == "FIRE_PATH_OPEN" else LIVE_REFUSED
                    _plp_record(
                        get_connection(), mint=mint, state=_plp_state,
                        paper_position_id=int(position_id), live_eligible=int(_ldc_verdict == "FIRE_PATH_OPEN"),
                        live_verdict=str(_ldc_verdict), live_refusal_reason=(None if _ldc_verdict == "FIRE_PATH_OPEN" else str(_ldc_blocker or "blocked")),
                        gate_state_json=_ldc_gates, selected_size_usd=float(_ldc_would_fire or 0.0),
                        pattern_stage=(_pattern_perm.state if _pattern_perm else None),
                        confidence=float(entry_conf or 0.0), executability_state=str(_ldc_coverage_reason),
                    )
                except Exception:
                    pass
'''

RISK_OLD='''                        log.critical("[LIVE_RISK_HALT] day_loss=$%.2f limit=$%.2f; no new live buy",
                                     _day_loss, _daily_limit)
'''
RISK_NEW=RISK_OLD+'''
                        try:
                            from services.paper_live_parity import record as _plp_record, LIVE_REFUSED
                            with get_connection() as _pc:
                                _plp_record(_pc, mint=mint, state=LIVE_REFUSED, paper_position_id=int(position_id),
                                            live_verdict="BLOCKED", live_refusal_reason="LIVE_RISK_DAILY_LIMIT")
                                _pc.commit()
                        except Exception:
                            pass
'''
CAPS_OLD='''                        log.warning("[LIVE_MIRROR_BLOCKED] open=%d/%d exposure=$%.2f cap=$%.2f",
                                    _real_open, _live_max, _real_exposure, _exposure_cap)
'''
CAPS_NEW=CAPS_OLD+'''
                        try:
                            from services.paper_live_parity import record as _plp_record, LIVE_REFUSED
                            with get_connection() as _pc:
                                _plp_record(_pc, mint=mint, state=LIVE_REFUSED, paper_position_id=int(position_id),
                                            live_verdict="BLOCKED", live_refusal_reason="LIVE_CAPS")
                                _pc.commit()
                        except Exception:
                            pass
'''
COV_OLD='''                            _lr = {"success": False, "error": _coverage_reason}
'''
COV_NEW=COV_OLD+'''
                            try:
                                from services.paper_live_parity import record as _plp_record, LIVE_REFUSED
                                with get_connection() as _pc:
                                    _plp_record(_pc, mint=mint, state=LIVE_REFUSED, paper_position_id=int(position_id),
                                                live_verdict="BLOCKED", live_refusal_reason=str(_coverage_reason),
                                                executability_state=str(_coverage_reason))
                                    _pc.commit()
                            except Exception:
                                pass
'''

BUY_OLD='''                            _lr = _live_buy(mint, _live_size, entry_price, position_id)
'''
BUY_NEW='''                            # CANARY_GOVERNOR_BEFORE_LIVE_BUY_20260803_FINAL
                            _canary_reservation_token = None
                            try:
                                from services.stop_realisability import readiness as _stop_readiness
                                from services.canary_governor import may_fire_canary, reserve_attempt
                                with get_connection() as _cg:
                                    _rd = _stop_readiness(_cg)
                                    _p90 = ((_rd.get("stats") or {}).get("p90_executable_pct"))
                                    _gov = may_fire_canary(
                                        _cg,
                                        projected_executable_loss_pct=(abs(float(_p90)) if _p90 is not None else None),
                                        lane_armed=True, mode_b_pass=True, pattern_pass=True,
                                        executability_ok=True, paper_enabled=True,
                                        readiness_status=_rd.get("status"),
                                    )
                                    if _gov.get("allowed"):
                                        _canary_reservation_token = reserve_attempt(
                                            _cg, position_id=int(position_id), mint=mint,
                                            size_usd=float(_live_size), note="execution_engine_pre_submit")
                                    _cg.commit()
                                if not _gov.get("allowed"):
                                    _lr = {"success": False, "error": "CANARY_GOVERNOR:" + str(_gov.get("reason"))}
                                elif not _canary_reservation_token:
                                    _lr = {"success": False, "error": "CANARY_RESERVATION_REFUSED"}
                                else:
                                    _lr = _live_buy(mint, _live_size, entry_price, position_id)
                            except Exception as _gov_exc:
                                _lr = {"success": False, "error": "CANARY_GOVERNOR_ERROR:" + type(_gov_exc).__name__}
'''

SIG_OLD='''                            _actual_cost_usd = float(_lr.get("actual_cost_usd") or _live_size)
                            _sig = str(_lr.get("tx_sig") or "")
'''
SIG_NEW=SIG_OLD+'''
                            # CANARY_AND_PARITY_SUBMISSION_20260803_FINAL
                            try:
                                from services.canary_governor import mark_submitted
                                from services.paper_live_parity import record as _plp_record, LIVE_SUBMITTED
                                with get_connection() as _txc:
                                    if _canary_reservation_token:
                                        mark_submitted(_txc, reservation_token=_canary_reservation_token)
                                    _plp_record(_txc, mint=mint, state=LIVE_SUBMITTED,
                                                paper_position_id=int(position_id), live_submit_at=time.time(),
                                                buy_signature=_sig, selected_size_usd=float(_live_size))
                                    _txc.commit()
                            except Exception:
                                pass
'''

REALID_OLD='''                                _real_id = int(_real_cur.lastrowid)
'''
REALID_NEW=REALID_OLD+'''
                                # PARITY_LIVE_BUY_SETTLED_20260803_FINAL
                                try:
                                    from services.paper_live_parity import record as _plp_record, DUAL_OPEN
                                    _plp_record(_real_conn, mint=mint, state=DUAL_OPEN,
                                                paper_position_id=int(position_id), live_position_id=int(_real_id),
                                                chain_fill_at=float(_chain_opened_at), live_fill_price=float(_live_entry_price),
                                                raw_token_quantity=int(_live_qty) if float(_live_qty).is_integer() else None,
                                                buy_signature=_sig, reconciliation_status="BUY_RECONCILED")
                                except Exception:
                                    pass
'''

UNRES_OLD='''                            log.critical("[LIVE_BUY_UNRESOLVED] sig=%s mint=%s; live lane must remain blocked",
                                         _sig[:20], mint[:16])
'''
UNRES_NEW=UNRES_OLD+'''
                            try:
                                from services.canary_governor import mark_failed_unresolved
                                from services.paper_live_parity import record as _plp_record, LIVE_SELL_UNRESOLVED
                                with get_connection() as _uc:
                                    mark_failed_unresolved(_uc, reservation_token=_canary_reservation_token,
                                                           position_id=int(position_id), note="buy confirmed unresolved")
                                    _plp_record(_uc, mint=mint, state=LIVE_SELL_UNRESOLVED,
                                                paper_position_id=int(position_id), buy_signature=_sig,
                                                terminal_reason="BUY_CONFIRMED_UNRESOLVED")
                                    _uc.commit()
                            except Exception:
                                pass
'''

FAIL_OLD='''                            log.warning("[LIVE_BUY_FAIL] SIM pos=%d error=%s; paper remains OPEN, no REAL row",
                                        position_id, _lr.get("error"))
'''
FAIL_NEW=FAIL_OLD+'''
                            try:
                                from services.canary_governor import mark_failed_unresolved
                                from services.paper_live_parity import record as _plp_record, LIVE_EXCEPTION
                                with get_connection() as _fc:
                                    mark_failed_unresolved(_fc, reservation_token=_canary_reservation_token,
                                                           position_id=int(position_id), note=str(_lr.get("error")))
                                    _plp_record(_fc, mint=mint, state=LIVE_EXCEPTION,
                                                paper_position_id=int(position_id), terminal_reason=str(_lr.get("error")))
                                    _fc.commit()
                            except Exception:
                                pass
'''

EXC_OLD='''                    log.error("[LIVE_BUY_ERROR] SIM pos=%d: %s; paper remains OPEN, no REAL row",
                              position_id, _le)
'''
EXC_NEW=EXC_OLD+'''
                    try:
                        from services.canary_governor import mark_failed_unresolved
                        from services.paper_live_parity import record as _plp_record, LIVE_EXCEPTION
                        with get_connection() as _ec:
                            mark_failed_unresolved(_ec, reservation_token=locals().get("_canary_reservation_token"),
                                                   position_id=int(position_id), note=str(_le))
                            _plp_record(_ec, mint=mint, state=LIVE_EXCEPTION,
                                        paper_position_id=int(position_id), terminal_reason=str(_le))
                            _ec.commit()
                    except Exception:
                        pass
'''

SELLFAIL_OLD='''                log.critical("[LIVE_SELL_FAIL] REAL pos=%d error=%s; keeping unresolved/open",
                             position_id, _ls.get("error"))
                return False
'''
SELLFAIL_NEW='''                log.critical("[LIVE_SELL_FAIL] REAL pos=%d error=%s; keeping unresolved/open",
                             position_id, _ls.get("error"))
                try:
                    from services.canary_governor import mark_failed_unresolved
                    from services.paper_live_parity import record as _plp_record, LIVE_SELL_UNRESOLVED
                    _sim_parent = int(position.get("sim_parent_position_id") or position_id)
                    with get_connection() as _sfc:
                        mark_failed_unresolved(_sfc, position_id=_sim_parent, note=str(_ls.get("error")))
                        _plp_record(_sfc, mint=mint, state=LIVE_SELL_UNRESOLVED,
                                    paper_position_id=_sim_parent, live_position_id=int(position_id),
                                    sell_signature=str(_ls.get("tx_sig") or ""),
                                    terminal_reason=str(_ls.get("error")))
                        _sfc.commit()
                except Exception:
                    pass
                return False
'''

CLOSE_OLD='''            if is_real_position:
                try:
                    conn.execute(
                        "UPDATE paper_positions SET live_state='SETTLED',sell_tx_sig=?,chain_confirmed_at=?,"
'''
CLOSE_NEW='''            if is_real_position:
                try:
                    conn.execute(
                        "UPDATE paper_positions SET live_state='SETTLED',sell_tx_sig=?,chain_confirmed_at=?,"
'''
# close hook inserted after live settlement block using another anchor
CLOSE2_OLD='''                except Exception as exc:
                    log.critical("[LIVE_SETTLEMENT_WRITE_FAIL] pos=%d error=%s", position_id, exc)
                    raise

            conn.execute(
'''
CLOSE2_NEW='''                except Exception as exc:
                    log.critical("[LIVE_SETTLEMENT_WRITE_FAIL] pos=%d error=%s", position_id, exc)
                    raise

            # PARITY_AND_CANARY_CLOSE_SETTLEMENT_20260803_FINAL
            try:
                from services.paper_live_parity import record as _plp_record, DUAL_SETTLED, TERMINAL_COMPLETE
                _sim_parent = int(position.get("sim_parent_position_id") or position_id)
                _plp_state = DUAL_SETTLED if is_real_position else TERMINAL_COMPLETE
                _plp_record(conn, mint=mint, state=_plp_state,
                            paper_position_id=_sim_parent,
                            live_position_id=(int(position_id) if is_real_position else None),
                            paper_exit_at=(None if is_real_position else float(now)),
                            paper_exit_price=(None if is_real_position else float(exit_price)),
                            paper_credited_pnl_usd=(None if is_real_position else float(pnl_usd)),
                            paper_market_true_pnl_usd=(None if is_real_position else float(pnl_usd)),
                            settled_exit_price=(float(exit_price) if is_real_position else None),
                            settlement_pnl_usd=(float(pnl_usd) if is_real_position else None),
                            sell_signature=(str(_live_sig or "") if is_real_position else None),
                            reconciliation_status=("SETTLED" if is_real_position else "PAPER_CLOSED"))
                if is_real_position:
                    from services.canary_governor import settle_attempt
                    settle_attempt(conn, position_id=_sim_parent, realised_pnl_usd=float(pnl_usd), reconciliation_ok=True)
            except Exception:
                pass

            conn.execute(
'''

ANCHORS=[
('stop probe',STOP_OLD,STOP_NEW,'STOP_REALISABILITY_PROBE_20260803_FINAL'),
('paper admission parity',PAPER_OLD,PAPER_NEW,'PARITY_PAPER_ADMISSION_20260803_FINAL'),
('live decision parity',DECISION_OLD,DECISION_NEW,'PARITY_LIVE_DECISION_20260803_FINAL'),
('risk refusal parity',RISK_OLD,RISK_NEW,'LIVE_RISK_DAILY_LIMIT'),
('caps refusal parity',CAPS_OLD,CAPS_NEW,'live_refusal_reason="LIVE_CAPS"'),
('coverage refusal parity',COV_OLD,COV_NEW,'executability_state=str(_coverage_reason)'),
('governor before buy',BUY_OLD,BUY_NEW,'CANARY_GOVERNOR_BEFORE_LIVE_BUY_20260803_FINAL'),
('submission lifecycle',SIG_OLD,SIG_NEW,'CANARY_AND_PARITY_SUBMISSION_20260803_FINAL'),
('buy settlement parity',REALID_OLD,REALID_NEW,'PARITY_LIVE_BUY_SETTLED_20260803_FINAL'),
('unresolved buy lifecycle',UNRES_OLD,UNRES_NEW,'BUY_CONFIRMED_UNRESOLVED")\n                                    _uc.commit()'),
('buy failure lifecycle',FAIL_OLD,FAIL_NEW,'mark_failed_unresolved(_fc'),
('buy exception lifecycle',EXC_OLD,EXC_NEW,'mark_failed_unresolved(_ec'),
('sell unresolved lifecycle',SELLFAIL_OLD,SELLFAIL_NEW,'LIVE_SELL_UNRESOLVED\n                                    paper_position_id=_sim_parent'),
('close settlement lifecycle',CLOSE2_OLD,CLOSE2_NEW,'PARITY_AND_CANARY_CLOSE_SETTLEMENT_20260803_FINAL'),
]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--force',action='store_true'); a=ap.parse_args()
    print('project root :',ROOT)
    if not TARGET.exists(): print('FAIL missing',TARGET); return 2
    up=running()
    if up and not a.force:
        print('FAIL Sentinuity appears running'); [print(' ',x) for x in up[:8]]; return 3
    for rel in MODULES:
        p=ROOT/rel
        if not p.exists(): print('FAIL missing',rel); return 1
        py_compile.compile(str(p),doraise=True); print('[OK] compile',rel)
    src=TARGET.read_text(encoding='utf-8',errors='replace'); todo=[]
    print('\nverifying production anchors')
    for label,old,new,mark in ANCHORS:
        if mark in src: print('[SKIP]',label); continue
        n=src.count(old); print(f"[{'OK' if n==1 else 'FAIL'}] {label}: {n}x")
        if n!=1: return 1
        todo.append((old,new))
    stamp=time.strftime('%Y%m%d_%H%M%S'); bdir=ROOT/'backups'/f'live_signoff_final_{stamp}'; bdir.mkdir(parents=True,exist_ok=True)
    shutil.copy2(TARGET,bdir/TARGET.name)
    out=src
    for old,new in todo: out=out.replace(old,new,1)
    staged=TARGET.with_suffix('.py.staged'); staged.write_text(out,encoding='utf-8',newline='')
    py_compile.compile(str(staged),doraise=True); before=sha(TARGET); os.replace(staged,TARGET)
    (bdir/'MANIFEST.txt').write_text(f'execution_engine.py {before} {sha(TARGET)}\n',encoding='utf-8')
    print('execution_engine.py',before[:16],'->',sha(TARGET)[:16]); print('backup',bdir)
    print('APPLIED: telemetry collecting; governor refusing until runtime thresholds pass. Live flags unchanged.')
    return 0
if __name__=='__main__': sys.exit(main())
