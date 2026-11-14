"""并发交错测试：封账与确认入账同时发生时，结果必须串行且一致，不重复记账。

SQLite 的库级写锁会把写事务串行化，与 MySQL 行锁下的正确性口径一致：
账户行锁 + 同事务检查，保证要么先入账后封账，要么封账先生效、批次被拒。
"""
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from src.config.database import Base
import src.models  # noqa: F401
from src.models import User, Job, LedgerBatch, LedgerEntry, LedgerMonth
from src.services import ledger

engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                       poolclass=StaticPool)
Session = sessionmaker(engine)
DAYS = [f'2026-09-{d:02d}' for d in range(1, 6)]


class U:
    def __init__(self, uid):
        self.id = uid
        self.email = f'u{uid}@t.local'


class Row:
    def __init__(self, day, kwh):
        self.day = day
        self.actual_kwh = kwh


class BatchInput:
    def __init__(self, key, rows):
        self.idem_key = key
        self.rows = rows


@pytest.fixture()
def account():
    Base.metadata.create_all(engine)
    with Session() as db:
        admin = User(email='admin@t.local', name='a', role='admin', password='x')
        db.add(admin)
        db.flush()
        result = {'chart': {'labels': DAYS, 'series': [
            {'name': '留出期预测', 'values': [1000.0 + i for i in range(len(DAYS))]}]}}
        db.add(Job(name='j', dataset_id=1, params={}, status='completed', result=result,
                   approved=True, created_by=admin.id, created_at=1.0, finished_at=2.0))
        db.commit()
        a = ledger.create_account(db, type('A', (), {'name': '账本', 'job_id': 1,
                                                      'line_code': 'L1', 'line_name': 'L1'})(), admin)
        db.commit()
        account_id = a.id
    with Session() as db:
        b = ledger.submit_batch(db, account_id, BatchInput('race-key', [Row(d, 1100) for d in DAYS]), U(1))
        db.commit()
        batch_id = b.id
    yield account_id, batch_id
    Base.metadata.drop_all(engine)


def test_confirm_against_concurrently_effective_seal_is_consistent(account):
    """封账在另一事务先生效（以已存在封账行为生效点），随后确认入账必须整批拒绝且不重复。"""
    account_id, batch_id = account
    month = DAYS[0][:7]
    # 预置一条已生效封账记录，模拟并发封账事务先提交
    with Session() as db:
        db.add(LedgerMonth(account_id=account_id, month=month, sealed=True, entry_count=0,
                           reversal_count=0, predicted_total=0, actual_total=0, variance_total=0,
                           basis_version='race', sealed_by=1, sealed_at=1.0))
        db.commit()

    with Session() as db:
        result = ledger.confirm_batch(db, account_id, batch_id, U(1))
        db.commit()

    with Session() as db:
        batch = db.get(LedgerBatch, batch_id)
        posted = db.scalar(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.batch_id == batch_id, LedgerEntry.status == 'posted'))
        assert batch.status == 'rejected' and '封账' in batch.reject_reason
        assert posted == 0
        # 待入账行清除、无重复、无残留
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.batch_id == batch_id)) == 0


def test_parallel_idempotent_batch_submit_returns_one_batch(account):
    account_id, batch_id = account
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        with Session() as db:
            try:
                # 并发重放已存在的幂等键，两个线程必须拿到同一批次，不产生新行
                b = ledger.submit_batch(db, account_id, BatchInput('race-key', [Row(DAYS[0], 1100)]), U(1))
                db.commit()
                return b.id
            except Exception:
                db.rollback()
                raise

    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda _: worker(), range(2)))
    assert set(ids) == {batch_id}
    with Session() as db:
        batches = db.scalars(select(LedgerBatch).where(LedgerBatch.idem_key == 'race-key')).all()
        assert len(batches) == 1
        entries = db.scalars(select(LedgerEntry).where(
            LedgerEntry.account_id == account_id, LedgerEntry.day == DAYS[0],
            LedgerEntry.entry_type == 'original')).all()
        assert len(entries) == 1
