"""节能考核账本：开户冻结归因口径，按日登记预计/实测/差额，封账与冲正。

并发约定：所有变更先对账户行加行锁（with_for_update），检查与写入在同一事务内，
路由层提交后锁才释放；幂等键另有唯一约束兜底，保证中断重试不重复记账。
"""
import time
from datetime import date
from fastapi import HTTPException
from sqlalchemy import select, func
from src.models import Job, LedgerAccount, LedgerBatch, LedgerEntry, LedgerMonth
from src.utils.serialization import checksum

PREDICTION_SERIES = '留出期预测'


def _lock_account(db, account_id) -> LedgerAccount:
    account = db.scalar(select(LedgerAccount).where(LedgerAccount.id == account_id).with_for_update())
    if not account:
        raise HTTPException(404, '考核账本不存在')
    return account


def _month_of(day: str) -> str:
    return day[:7]


def _sealed_month(db, account_id: int, month: str):
    return db.scalar(select(LedgerMonth).where(
        LedgerMonth.account_id == account_id, LedgerMonth.month == month, LedgerMonth.sealed == True))


def _prediction_map(job: Job) -> dict[str, float]:
    chart = (job.result or {}).get('chart') or {}
    labels = chart.get('labels') or []
    predicted = next((s.get('values') or [] for s in chart.get('series', [])
                      if s.get('name') == PREDICTION_SERIES), [])
    return {str(day): float(value) for day, value in zip(labels, predicted)}


def create_account(db, data, user):
    job = db.get(Job, data.job_id)
    if not job or job.status != 'completed':
        raise HTTPException(409, '仅已完成的归因结果可以开立考核账本')
    if not job.approved:
        raise HTTPException(409, '归因结论尚未获业务批准，不能作为考核口径')
    predictions = _prediction_map(job)
    if not predictions:
        raise HTTPException(409, '批准结果缺少逐日预测依据，无法开户')
    exists = db.scalar(select(LedgerAccount).where(
        LedgerAccount.job_id == job.id, LedgerAccount.line_code == data.line_code))
    if exists:
        raise HTTPException(409, '该线体与批准结果已开立考核账本，开户口径不可重复建立')
    basis = {
        'job_id': job.id,
        'job_name': job.name,
        'dataset_id': job.dataset_id,
        'params': job.params,
        'metrics': (job.result or {}).get('metrics', []),
        'method': (job.result or {}).get('method', ''),
        'summary': (job.result or {}).get('summary', ''),
        'frozen_at': time.time(),
        'predictions': predictions,
    }
    account = LedgerAccount(
        name=data.name, line_code=data.line_code, line_name=data.line_name or data.line_code,
        job_id=job.id, basis_version=f'job-{job.id}@{(job.finished_at or 0):.0f}',
        basis=basis, basis_checksum=checksum(basis), created_by=user.id, created_at=time.time(),
    )
    db.add(account)
    db.flush()
    return account


def _existing_idem_batch(db, account_id: int, idem_key: str):
    return db.scalar(select(LedgerBatch).where(
        LedgerBatch.account_id == account_id, LedgerBatch.idem_key == idem_key))


def submit_batch(db, account_id, data, user):
    """登记待入账批次。同日实测冲突整批拒绝；重复同值入账跳过、不重复累计。"""
    account = _lock_account(db, account_id)
    idem = _existing_idem_batch(db, account_id, data.idem_key)
    if idem:
        # 客户端重试：原样返回同一批次，绝不产生第二笔
        return idem
    # 1) 批次内同日校验：同值去重，异值即冲突，整批拒绝（不写任何记录）
    picked: dict[str, float] = {}
    for row in data.rows:
        if row.day in picked and picked[row.day] != row.actual_kwh:
            raise HTTPException(409, f'{row.day} 同日实测值相互冲突，本批 {len(data.rows)} 条全部拒绝，未登记任何账目')
        picked[row.day] = row.actual_kwh
    predictions = account.basis.get('predictions', {})
    # 2) 预测依据、封账月份与既有日账核对（更正必须走冲正，不允许借新批次覆盖）
    dup_days: set[str] = set()
    for day, actual in picked.items():
        if day not in predictions:
            raise HTTPException(422, f'{day} 不在批准归因的预测依据内，无法登记预计电耗')
        if _sealed_month(db, account_id, _month_of(day)):
            raise HTTPException(409, f'{_month_of(day)} 月已封账，迟到数据请在未封月份登记冲正')
        existing = db.scalar(select(LedgerEntry).where(
            LedgerEntry.account_id == account_id, LedgerEntry.day == day,
            LedgerEntry.entry_type == 'original'))
        if existing:
            if existing.actual_kwh != actual:
                raise HTTPException(409, f'{day} 已存在不同实测值的日账，整批拒绝；更正请使用冲正流程')
            dup_days.add(day)
    batch = LedgerBatch(account_id=account_id, idem_key=data.idem_key, kind='entry',
                        status='pending', created_by=user.id, created_at=time.time(),
                        note=f'新登记 {len(picked) - len(dup_days)} 条，重复跳过 {len(dup_days)} 条')
    db.add(batch)
    db.flush()
    for day, actual in picked.items():
        if day in dup_days:
            continue  # 重复入账不重复累计
        predicted = predictions[day]
        db.add(LedgerEntry(
            account_id=account_id, batch_id=batch.id, entry_type='original', status='pending',
            day=day, predicted_kwh=predicted, actual_kwh=actual,
            variance_kwh=round(actual - predicted, 4), book_month=_month_of(day),
            created_at=time.time(),
        ))
    db.flush()
    return batch


def confirm_batch(db, account_id, batch_id, user):
    """待入账 → 已入账。封账并发以封账生效为界：已封月份的批次只能拒绝。"""
    account = _lock_account(db, account_id)
    batch = db.get(LedgerBatch, batch_id)
    if not batch or batch.account_id != account.id:
        raise HTTPException(404, '入账批次不存在')
    if batch.status != 'pending':
        return batch  # 已入账/已拒绝：重试直接返回，不重复记账
    entries = list(db.scalars(select(LedgerEntry).where(
        LedgerEntry.batch_id == batch.id, LedgerEntry.status == 'pending')))
    blocked = sorted({e.book_month for e in entries if _sealed_month(db, account_id, e.book_month)})
    if blocked:
        batch.status = 'rejected'
        batch.reject_reason = f"月份 {','.join(blocked)} 已封账，本批未入账；请在未封月份登记冲正"
        # 批次终结：未落账的待入账行删除，释放日期以便重新提交；批次行保留拒绝痕迹
        for entry in entries:
            db.delete(entry)
        db.flush()
        return batch
    now = time.time()
    for entry in entries:
        entry.status = 'posted'
        entry.posted_at = now
    batch.status = 'posted'
    batch.posted_at = now
    db.flush()
    return batch


def discard_batch(db, account_id, batch_id, user):
    account = _lock_account(db, account_id)
    batch = db.get(LedgerBatch, batch_id)
    if not batch or batch.account_id != account.id:
        raise HTTPException(404, '入账批次不存在')
    if batch.status != 'pending':
        raise HTTPException(409, '仅待入账批次可以丢弃')
    batch.status = 'rejected'
    batch.reject_reason = '人工丢弃待入账批次'
    for entry in db.scalars(select(LedgerEntry).where(LedgerEntry.batch_id == batch.id,
                                                      LedgerEntry.status == 'pending')):
        db.delete(entry)
    db.flush()
    return batch


def create_reversal(db, account_id, data, user):
    """迟到/更正：只在当前未封月份登记冲正行，原月份与已发简报保持原值。"""
    account = _lock_account(db, account_id)
    idem = _existing_idem_batch(db, account_id, data.idem_key)
    if idem:
        return idem  # 冲正中断后重试，返回原批次，不重复记账
    original = db.scalar(select(LedgerEntry).where(
        LedgerEntry.account_id == account_id, LedgerEntry.day == data.ref_day,
        LedgerEntry.entry_type == 'original', LedgerEntry.status == 'posted'))
    if not original:
        raise HTTPException(404, f'{data.ref_day} 没有已入账的原始日账，无法冲正')
    if db.scalar(select(LedgerEntry).where(LedgerEntry.reversal_of_id == original.id)):
        raise HTTPException(409, f'{data.ref_day} 已登记过冲正，每条原始日账仅允许一次更正')
    today = date.today().isoformat()
    book_month = _month_of(today)
    if _sealed_month(db, account_id, book_month):
        raise HTTPException(409, f'当前月份 {book_month} 已封账，无法登记冲正')
    batch = LedgerBatch(account_id=account_id, idem_key=data.idem_key, kind='reversal',
                        status='posted', note=data.note or f'更正 {data.ref_day} 实测电耗',
                        created_by=user.id, created_at=time.time(), posted_at=time.time())
    db.add(batch)
    db.flush()
    db.add(LedgerEntry(
        account_id=account_id, batch_id=batch.id, entry_type='reversal', status='posted',
        day=today, ref_day=data.ref_day, reversal_of_id=original.id,
        predicted_kwh=original.predicted_kwh, actual_kwh=data.actual_kwh,
        variance_kwh=round(data.actual_kwh - (original.predicted_kwh or 0), 4),
        book_month=book_month, created_at=time.time(), posted_at=time.time(),
    ))
    db.flush()
    return batch


def seal_month(db, account_id, month, user):
    account = _lock_account(db, account_id)
    existing = db.scalar(select(LedgerMonth).where(
        LedgerMonth.account_id == account.id, LedgerMonth.month == month))
    if existing and existing.sealed:
        return existing  # 封账幂等，重复点击不改变快照
    pending = db.scalar(select(func.count()).select_from(LedgerEntry).join(
        LedgerBatch, LedgerBatch.id == LedgerEntry.batch_id).where(
        LedgerEntry.account_id == account.id, LedgerEntry.book_month == month,
        LedgerBatch.status == 'pending'))
    if pending:
        raise HTTPException(409, f'{month} 存在待入账批次，请先确认入账或丢弃后再封账')
    posted = list(db.scalars(select(LedgerEntry).where(
        LedgerEntry.account_id == account.id, LedgerEntry.book_month == month,
        LedgerEntry.status == 'posted')))
    now = time.time()
    row = existing or LedgerMonth(account_id=account.id, month=month)
    row.sealed = True
    row.sealed_by = user.id
    row.sealed_at = now
    row.basis_version = account.basis_version
    row.entry_count = sum(1 for e in posted if e.entry_type == 'original')
    row.reversal_count = sum(1 for e in posted if e.entry_type == 'reversal')
    row.predicted_total = round(sum(e.predicted_kwh or 0 for e in posted), 4)
    row.actual_total = round(sum(e.actual_kwh for e in posted), 4)
    row.variance_total = round(sum(e.variance_kwh or 0 for e in posted), 4)
    db.add(row)
    db.flush()
    return row


def month_totals(db, account: LedgerAccount) -> list[dict]:
    """月合计：已封月份回读封账快照，未封月份按 posted 日账实时计算，均可追到日账行。"""
    sealed_rows = {m.month: m for m in db.scalars(
        select(LedgerMonth).where(LedgerMonth.account_id == account.id))}
    entries = list(db.scalars(select(LedgerEntry).where(
        LedgerEntry.account_id == account.id, LedgerEntry.status == 'posted').order_by(LedgerEntry.day)))
    months: dict[str, dict] = {}
    for entry in entries:
        bucket = months.setdefault(entry.book_month, {'month': entry.book_month, 'entry_count': 0,
                                                       'reversal_count': 0, 'predicted_total': 0.0,
                                                       'actual_total': 0.0, 'variance_total': 0.0,
                                                       'entry_ids': []})
        bucket['entry_count'] += 1 if entry.entry_type == 'original' else 0
        bucket['reversal_count'] += 1 if entry.entry_type == 'reversal' else 0
        bucket['predicted_total'] += entry.predicted_kwh or 0
        bucket['actual_total'] += entry.actual_kwh
        bucket['variance_total'] += entry.variance_kwh or 0
        bucket['entry_ids'].append(entry.id)
    result = []
    for month in sorted(set(months) | set(sealed_rows)):
        sealed = sealed_rows.get(month)
        if sealed and sealed.sealed:
            result.append({'month': month, 'sealed': True, 'sealed_at': sealed.sealed_at,
                           'basis_version': sealed.basis_version,
                           'entry_count': sealed.entry_count, 'reversal_count': sealed.reversal_count,
                           'predicted_total': sealed.predicted_total, 'actual_total': sealed.actual_total,
                           'variance_total': sealed.variance_total,
                           'entry_ids': months.get(month, {}).get('entry_ids', [])})
        elif month in months:
            live = months[month]
            result.append({'month': month, 'sealed': False, 'sealed_at': None,
                           'basis_version': account.basis_version,
                           'entry_count': live['entry_count'], 'reversal_count': live['reversal_count'],
                           'predicted_total': round(live['predicted_total'], 4),
                           'actual_total': round(live['actual_total'], 4),
                           'variance_total': round(live['variance_total'], 4),
                           'entry_ids': live['entry_ids']})
    return result
