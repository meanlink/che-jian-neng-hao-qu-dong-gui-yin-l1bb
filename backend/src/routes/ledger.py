from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.models import LedgerAccount, LedgerBatch, LedgerEntry, LedgerMonth
from src.schemas.ledger import (LedgerAccountInput, LedgerBatchInput, LedgerReversalInput, LedgerMonthInput)
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import ledger
from src.services.audit import record
from src.utils.serialization import (ledger_account_dict, ledger_batch_dict,
                                     ledger_entry_dict, ledger_month_dict)

router = APIRouter()


@router.get('/ledger/accounts')
def accounts(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(LedgerAccount).order_by(LedgerAccount.id.desc()))
    return [ledger_account_dict(a) for a in rows]


@router.post('/ledger/accounts', status_code=201)
def open_account(data: LedgerAccountInput, db=Depends(get_db), u=Depends(admin_only)):
    account = ledger.create_account(db, data, u)
    record(db, u.email, 'ledger.open', {'id': account.id, 'job_id': account.job_id, 'line': account.line_code})
    db.commit()
    return ledger_account_dict(account)


@router.get('/ledger/accounts/{id}')
def account_detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    account = db.get(LedgerAccount, id)
    if not account:
        raise HTTPException(404, '考核账本不存在')
    batches = [ledger_batch_dict(b) for b in db.scalars(
        select(LedgerBatch).where(LedgerBatch.account_id == id).order_by(LedgerBatch.id.desc()))]
    entries = [ledger_entry_dict(e) for e in db.scalars(
        select(LedgerEntry).where(LedgerEntry.account_id == id).order_by(LedgerEntry.day, LedgerEntry.id))]
    months = [ledger_month_dict(m) for m in db.scalars(
        select(LedgerMonth).where(LedgerMonth.account_id == id).order_by(LedgerMonth.month))]
    totals = ledger.month_totals(db, account)
    return {**ledger_account_dict(account), 'batches': batches, 'entries': entries,
            'months': months, 'totals': totals}


@router.post('/ledger/accounts/{id}/batches', status_code=201)
def submit_batch(id: int, data: LedgerBatchInput, db=Depends(get_db), u=Depends(can_write)):
    batch = ledger.submit_batch(db, id, data, u)
    record(db, u.email, 'ledger.batch.submit', {'id': batch.id, 'idem_key': batch.idem_key,
                                                'rows': len(data.rows)})
    db.commit()
    return ledger_batch_dict(batch)


@router.post('/ledger/accounts/{id}/batches/{batch_id}/confirm')
def confirm_batch(id: int, batch_id: int, db=Depends(get_db), u=Depends(can_write)):
    batch = ledger.confirm_batch(db, id, batch_id, u)
    record(db, u.email, 'ledger.batch.confirm', {'id': batch.id, 'status': batch.status})
    db.commit()
    return ledger_batch_dict(batch)


@router.post('/ledger/accounts/{id}/batches/{batch_id}/discard')
def discard_batch(id: int, batch_id: int, db=Depends(get_db), u=Depends(can_write)):
    batch = ledger.discard_batch(db, id, batch_id, u)
    record(db, u.email, 'ledger.batch.discard', {'id': batch.id})
    db.commit()
    return ledger_batch_dict(batch)


@router.post('/ledger/accounts/{id}/reversals', status_code=201)
def create_reversal(id: int, data: LedgerReversalInput, db=Depends(get_db), u=Depends(can_write)):
    batch = ledger.create_reversal(db, id, data, u)
    record(db, u.email, 'ledger.reversal', {'id': batch.id, 'ref_day': data.ref_day})
    db.commit()
    return ledger_batch_dict(batch)


@router.post('/ledger/accounts/{id}/months/seal')
def seal_month(id: int, data: LedgerMonthInput, db=Depends(get_db), u=Depends(admin_only)):
    month = ledger.seal_month(db, id, data.month, u)
    record(db, u.email, 'ledger.month.seal', {'account_id': id, 'month': data.month})
    db.commit()
    return ledger_month_dict(month)
