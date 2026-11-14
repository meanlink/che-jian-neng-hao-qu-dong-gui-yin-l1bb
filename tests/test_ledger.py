"""考核账本核心规则测试（SQLite 内存库，不依赖 Redis/MySQL）。

覆盖：开户冻结口径、同日冲突整批拒绝、重复入账幂等、待入账/已入账状态、
封账与入账并发边界、冲正幂等与只进未封月份、月合计追溯原始日账与归因版本。
"""
import os
import sys
import time
from datetime import date

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from src.config.database import Base
import src.models  # noqa: F401
from src.models import User, Job, LedgerAccount, LedgerBatch, LedgerEntry, LedgerMonth
from src.services import ledger
from src.utils.serialization import checksum
from fastapi import HTTPException

engine = create_engine('sqlite:///:memory:')
Session = sessionmaker(engine)

DAYS = [f'2026-09-{d:02d}' for d in range(1, 11)]  # 10 个留出日期
PRED = {d: 1000.0 + i for i, d in enumerate(DAYS)}


class U:
    def __init__(self, id):
        self.id = id
        self.email = f'u{id}@test.local'


class Row:
    def __init__(self, day, actual_kwh):
        self.day = day
        self.actual_kwh = actual_kwh


class BatchInput:
    def __init__(self, idem_key, rows):
        self.idem_key = idem_key
        self.rows = rows


class ReversalInput:
    def __init__(self, idem_key, ref_day, actual_kwh, note=''):
        self.idem_key = idem_key
        self.ref_day = ref_day
        self.actual_kwh = actual_kwh
        self.note = note


class AccountInput:
    def __init__(self, name, job_id, line_code, line_name=''):
        self.name = name
        self.job_id = job_id
        self.line_code = line_code
        self.line_name = line_name


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    with Session() as session:
        admin = User(email='admin@test.local', name='admin', role='admin', password='x')
        session.add(admin)
        session.flush()
        result = {
            'summary': 's', 'method': 'm', 'metrics': [{'label': '验证RMSE', 'value': 12.3}],
            'chart': {'labels': DAYS, 'series': [
                {'name': '留出期实测', 'values': [1000 + i for i in range(10)]},
                {'name': '留出期预测', 'values': [1000 + i for i in range(10)]},
            ]},
        }
        job = Job(name='基线', dataset_id=1, params={'max_features': 4}, status='completed',
                  result=result, approved=True, created_by=admin.id,
                  created_at=time.time() - 100, finished_at=time.time() - 90)
        session.add(job)
        session.commit()
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture()
def account(db):
    user = db.get(User, 1)
    job = db.scalar(select(Job))
    a = ledger.create_account(db, AccountInput('P01 考核账本', job.id, '冲压P01', '冲压一线'), user)
    db.commit()
    return a


def test_open_account_freezes_approved_basis(db, account):
    assert account.basis_version.startswith('job-1@')
    assert account.basis['predictions'] == PRED
    assert account.basis_checksum == checksum(account.basis)
    # 原任务结果事后变化不影响已冻结的账本
    job = db.get(Job, account.job_id)
    job.result = {'chart': {'labels': [], 'series': []}}
    db.commit()
    fresh = db.get(LedgerAccount, account.id)
    assert fresh.basis['predictions'] == PRED


def test_open_account_requires_approved_completed_job(db):
    user = db.get(User, 1)
    job = db.scalar(select(Job))
    job.approved = False
    db.commit()
    with pytest.raises(HTTPException) as exc:
        ledger.create_account(db, AccountInput('x', job.id, 'L1'), user)
    assert exc.value.status_code == 409
    # 同一线体+任务重复开户拒绝
    job.approved = True
    db.commit()
    ledger.create_account(db, AccountInput('x2', job.id, 'L1'), user)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        ledger.create_account(db, AccountInput('x3', job.id, 'L1'), user)
    assert exc.value.status_code == 409


def test_conflicting_same_day_rejects_whole_batch(db, account):
    user = db.get(User, 1)
    data = BatchInput('k1', [Row(DAYS[0], 1100), Row(DAYS[0], 1200), Row(DAYS[1], 1100)])
    with pytest.raises(HTTPException) as exc:
        ledger.submit_batch(db, account.id, data, user)
    assert exc.value.status_code == 409 and '全部拒绝' in exc.value.detail
    db.rollback()
    assert db.scalar(select(func.count()).select_from(LedgerBatch)) == 0
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 0


def test_staged_then_posted_and_duplicate_not_accumulated(db, account):
    user = db.get(User, 1)
    b1 = ledger.submit_batch(db, account.id, BatchInput('k1', [Row(DAYS[0], 1100), Row(DAYS[1], 1200)]), user)
    db.commit()
    assert b1.status == 'pending'
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.batch_id == b1.id)).all()
    assert {e.status for e in entries} == {'pending'}
    assert entries[0].predicted_kwh == PRED[entries[0].day]
    assert entries[0].variance_kwh == entries[0].actual_kwh - entries[0].predicted_kwh
    # 确认入账
    ledger.confirm_batch(db, account.id, b1.id, user)
    db.commit()
    assert db.get(LedgerBatch, b1.id).status == 'posted'
    # 重复提交（同值）不新增日账
    b2 = ledger.submit_batch(db, account.id, BatchInput('k2', [Row(DAYS[0], 1100), Row(DAYS[2], 1300)]), user)
    db.commit()
    new_entries = db.scalars(select(LedgerEntry).where(LedgerEntry.batch_id == b2.id)).all()
    assert len(new_entries) == 1 and new_entries[0].day == DAYS[2]
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(
        LedgerEntry.day == DAYS[0], LedgerEntry.entry_type == 'original')) == 1
    # 同 key 重试返回同一批次
    again = ledger.submit_batch(db, account.id, BatchInput('k2', [Row(DAYS[2], 1300)]), user)
    assert again.id == b2.id


def test_different_actual_for_existing_day_rejected(db, account):
    user = db.get(User, 1)
    ledger.submit_batch(db, account.id, BatchInput('k1', [Row(DAYS[0], 1100)]), user)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        ledger.submit_batch(db, account.id, BatchInput('k2', [Row(DAYS[0], 9999)]), user)
    assert exc.value.status_code == 409 and '冲正' in exc.value.detail
    db.rollback()


def test_confirm_idempotent_after_interruption(db, account):
    user = db.get(User, 1)
    b = ledger.submit_batch(db, account.id, BatchInput('k1', [Row(DAYS[0], 1100)]), user)
    db.commit()
    ledger.confirm_batch(db, account.id, b.id, user)
    db.commit()
    # 模拟中断后重试确认
    again = ledger.confirm_batch(db, account.id, b.id, user)
    assert again.status == 'posted'
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(
        LedgerEntry.batch_id == b.id, LedgerEntry.status == 'posted')) == 1


def test_seal_blocks_late_posting_and_pending_rejected_at_confirm(db, account):
    user = db.get(User, 1)
    month = DAYS[0][:7]
    b = ledger.submit_batch(db, account.id, BatchInput('k1', [Row(DAYS[0], 1100)]), user)
    db.commit()
    # 有待入账时不能封账
    with pytest.raises(HTTPException) as exc:
        ledger.seal_month(db, account.id, month, user)
    assert exc.value.status_code == 409
    db.rollback()
    ledger.confirm_batch(db, account.id, b.id, user)
    db.commit()
    sealed = ledger.seal_month(db, account.id, month, user)
    db.commit()
    assert sealed.sealed and sealed.actual_total == 1100
    # 封账后：同月份迟到批次提交即拒绝
    with pytest.raises(HTTPException) as exc:
        ledger.submit_batch(db, account.id, BatchInput('k2', [Row(DAYS[1], 1200)]), user)
    assert exc.value.status_code == 409 and '封账' in exc.value.detail
    db.rollback()
    # 封账幂等
    again = ledger.seal_month(db, account.id, month, user)
    assert again.id == sealed.id


def test_seal_race_pending_batch_rejected_on_confirm(db, account):
    """封账与入账并发：待入账批次在封账生效后确认，整批拒绝且不入账。"""
    user = db.get(User, 1)
    month = DAYS[0][:7]
    b = ledger.submit_batch(db, account.id, BatchInput('k1', [Row(DAYS[0], 1100)]), user)
    db.commit()
    # 并发的封账事务先生效（绕过本事务的待入账检查，直接落一条已封记录模拟生效点）
    db.add(LedgerMonth(account_id=account.id, month=month, sealed=True, entry_count=0,
                       reversal_count=0, predicted_total=0, actual_total=0, variance_total=0,
                       basis_version=account.basis_version, sealed_by=user.id, sealed_at=time.time()))
    db.commit()
    result = ledger.confirm_batch(db, account.id, b.id, user)
    db.commit()
    assert result.status == 'rejected' and '封账' in result.reject_reason
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(
        LedgerEntry.status == 'posted')) == 0
    # 批次拒绝后其待入账行被清除，批次记录保留为拒绝痕迹
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(
        LedgerEntry.batch_id == b.id)) == 0
    assert db.get(LedgerBatch, b.id).reject_reason


def test_reversal_only_in_open_month_and_keeps_original(db, account, monkeypatch):
    user = db.get(User, 1)
    month = DAYS[0][:7]
    b = ledger.submit_batch(db, account.id, BatchInput('k1', [Row(DAYS[0], 1100)]), user)
    db.commit()
    ledger.confirm_batch(db, account.id, b.id, user)
    db.commit()
    original = db.scalar(select(LedgerEntry).where(LedgerEntry.day == DAYS[0]))
    # 原始月份封账，原月份快照固定
    ledger.seal_month(db, account.id, month, user)
    db.commit()
    # 冲正登记日固定在 2026-11（未封月份）
    class FakeDate:
        @staticmethod
        def today():
            return date(2026, 11, 5)
        @staticmethod
        def fromisoformat(v):
            return date.fromisoformat(v)
    monkeypatch.setattr(ledger, 'date', FakeDate)
    rb = ledger.create_reversal(db, account.id, ReversalInput('r1', DAYS[0], 1050, '电表复核'), user)
    db.commit()
    assert rb.kind == 'reversal' and rb.status == 'posted'
    rev = db.scalar(select(LedgerEntry).where(LedgerEntry.entry_type == 'reversal'))
    assert rev.book_month == '2026-11' and rev.ref_day == DAYS[0]
    assert rev.reversal_of_id == original.id
    # 原始日账保持原值；原月份封账快照不变
    db.refresh(original)
    assert original.actual_kwh == 1100
    sealed = db.scalar(select(LedgerMonth).where(LedgerMonth.month == month))
    assert sealed.actual_total == 1100 and sealed.variance_total != 0
    # 冲正重试幂等
    again = ledger.create_reversal(db, account.id, ReversalInput('r1', DAYS[0], 1050), user)
    assert again.id == rb.id
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(
        LedgerEntry.entry_type == 'reversal')) == 1
    # 同一原始日账第二次不同更正被拒
    with pytest.raises(HTTPException) as exc:
        ledger.create_reversal(db, account.id, ReversalInput('r2', DAYS[0], 1080), user)
    assert exc.value.status_code == 409


def test_month_totals_trace_to_entries_and_basis_version(db, account, monkeypatch):
    user = db.get(User, 1)
    b = ledger.submit_batch(db, account.id, BatchInput('k1', [
        Row(DAYS[0], 1100), Row(DAYS[1], 1200), Row(DAYS[2], 1300)]), user)
    db.commit()
    ledger.confirm_batch(db, account.id, b.id, user)
    db.commit()
    totals = ledger.month_totals(db, db.get(LedgerAccount, account.id))
    assert len(totals) == 1 and totals[0]['sealed'] is False
    t = totals[0]
    assert t['entry_count'] == 3 and t['reversal_count'] == 0
    assert t['actual_total'] == 3600
    assert t['variance_total'] == round(sum(
        e.actual_kwh - e.predicted_kwh for e in db.scalars(select(LedgerEntry).where(
            LedgerEntry.status == 'posted'))), 4)
    assert len(t['entry_ids']) == 3  # 可追到每一条原始日账
    assert t['basis_version'] == account.basis_version
    # 封账后读取快照
    ledger.seal_month(db, account.id, t['month'], user)
    db.commit()
    sealed_totals = ledger.month_totals(db, db.get(LedgerAccount, account.id))
    assert sealed_totals[0]['sealed'] is True
    assert sealed_totals[0]['actual_total'] == t['actual_total']


def test_day_outside_prediction_basis_rejected(db, account):
    user = db.get(User, 1)
    with pytest.raises(HTTPException) as exc:
        ledger.submit_batch(db, account.id, BatchInput('k1', [Row('2026-01-05', 1100)]), user)
    assert exc.value.status_code == 422
    db.rollback()


def test_reversal_on_same_calendar_day_does_not_clash(db, account, monkeypatch):
    """冲正登记日恰好等于某原始业务日期时，两类行可共存（唯一约束含 entry_type）。"""
    user = db.get(User, 1)
    b = ledger.submit_batch(db, account.id, BatchInput('k1', [Row(DAYS[0], 1100)]), user)
    db.commit()
    ledger.confirm_batch(db, account.id, b.id, user)
    db.commit()
    class FakeDate:
        @staticmethod
        def today():
            return date.fromisoformat(DAYS[0])
        @staticmethod
        def fromisoformat(v):
            return date.fromisoformat(v)
    monkeypatch.setattr(ledger, 'date', FakeDate)
    rb = ledger.create_reversal(db, account.id, ReversalInput('r1', DAYS[0], 1050), user)
    db.commit()
    rows = db.scalars(select(LedgerEntry).where(LedgerEntry.account_id == account.id)).all()
    assert len(rows) == 2
    assert {r.entry_type for r in rows} == {'original', 'reversal'}
    assert rb.status == 'posted'


def test_discard_pending_batch(db, account):
    user = db.get(User, 1)
    b = ledger.submit_batch(db, account.id, BatchInput('k1', [Row(DAYS[0], 1100)]), user)
    db.commit()
    ledger.discard_batch(db, account.id, b.id, user)
    db.commit()
    assert db.get(LedgerBatch, b.id).status == 'rejected'
    # 待入账行随丢弃清除，日期可重新提交
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(
        LedgerEntry.batch_id == b.id)) == 0
    again = ledger.submit_batch(db, account.id, BatchInput('k2', [Row(DAYS[0], 1150)]), user)
    db.commit()
    assert again.status == 'pending'
    with pytest.raises(HTTPException) as exc:
        ledger.discard_batch(db, account.id, b.id, user)
    assert exc.value.status_code == 409
