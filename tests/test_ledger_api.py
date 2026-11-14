"""路由级集成测试：权限、HTTP 状态码与完整账本流程（SQLite + 依赖覆盖）。"""
import os
import sys
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from src.config.database import Base, get_db
import src.models  # noqa: F401
from src.models import User, Job, LedgerEntry
from src.main import app
from src.middlewares.auth import current_user

engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                       poolclass=StaticPool)
Session = sessionmaker(engine)
DAYS = [f'2026-09-{d:02d}' for d in range(1, 11)]


def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        users = [User(email='admin@t.local', name='a', role='admin', password='x'),
                 User(email='analyst@t.local', name='n', role='analyst', password='x'),
                 User(email='viewer@t.local', name='v', role='viewer', password='x')]
        db.add_all(users)
        db.flush()
        result = {'summary': 's', 'method': 'm', 'metrics': [],
                  'chart': {'labels': DAYS, 'series': [
                      {'name': '留出期实测', 'values': [1000 + i for i in range(10)]},
                      {'name': '留出期预测', 'values': [1000 + i for i in range(10)]}]}}
        db.add(Job(name='基线', dataset_id=1, params={'max_features': 4}, status='completed',
                   result=result, approved=True, created_by=users[0].id,
                   created_at=time.time() - 100, finished_at=time.time() - 90))
        db.commit()


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    seed()

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


def auth(app, role):
    """返回在每次请求前设置该角色身份覆盖的调用器（依赖覆盖是 app 级全局状态）。"""
    http = TestClient(app)

    def _dep():
        with Session() as db:
            return db.scalar(select(User).where(User.email == f'{role}@t.local'))

    def call(method, path, **kwargs):
        app.dependency_overrides[current_user] = _dep
        return getattr(http, method)(path, **kwargs)
    return call


def test_full_flow_and_permissions(client):
    app = client
    viewer = auth(app, 'viewer')
    admin = auth(app, 'admin')
    analyst = auth(app, 'analyst')

    # viewer 不能开户
    r = viewer('post', '/ledger/accounts', json={'name': 'P01账本', 'job_id': 1, 'line_code': '冲压P01'})
    assert r.status_code == 403
    # analyst 也不能开户
    r = analyst('post', '/ledger/accounts', json={'name': 'P01账本', 'job_id': 1, 'line_code': '冲压P01'})
    assert r.status_code == 403
    # admin 开户
    r = admin('post', '/ledger/accounts', json={'name': 'P01账本', 'job_id': 1,
                                                'line_code': '冲压P01', 'line_name': '冲压一线'})
    assert r.status_code == 201
    account_id = r.json()['id']
    assert r.json()['basis']['predictions'][DAYS[0]] == 1000.0

    # 冲突批次整批拒绝 409
    r = analyst('post', f'/ledger/accounts/{account_id}/batches',
                json={'idem_key': 'k-conflict', 'rows': [
                    {'day': DAYS[0], 'actual_kwh': 1100}, {'day': DAYS[0], 'actual_kwh': 1200}]})
    assert r.status_code == 409

    # viewer 不能登记
    r = viewer('post', f'/ledger/accounts/{account_id}/batches',
               json={'idem_key': 'key-1', 'rows': [{'day': DAYS[0], 'actual_kwh': 1100}]})
    assert r.status_code == 403

    # analyst 登记待入账
    r = analyst('post', f'/ledger/accounts/{account_id}/batches',
                json={'idem_key': 'key-1', 'rows': [{'day': DAYS[0], 'actual_kwh': 1100},
                                                 {'day': DAYS[1], 'actual_kwh': 1200}]})
    assert r.status_code == 201 and r.json()['status'] == 'pending'
    batch_id = r.json()['id']

    # 同 key 重试幂等
    r2 = analyst('post', f'/ledger/accounts/{account_id}/batches',
                 json={'idem_key': 'key-1', 'rows': [{'day': DAYS[0], 'actual_kwh': 1100}]})
    assert r2.json()['id'] == batch_id

    # 确认入账
    r = analyst('post', f'/ledger/accounts/{account_id}/batches/{batch_id}/confirm')
    assert r.status_code == 200 and r.json()['status'] == 'posted'
    # 重复确认不重复记账
    analyst('post', f'/ledger/accounts/{account_id}/batches/{batch_id}/confirm')
    with Session() as db:
        assert db.query(LedgerEntry).filter(LedgerEntry.batch_id == batch_id,
                                            LedgerEntry.status == 'posted').count() == 2

    # 封账（仅 admin）
    r = analyst('post', f'/ledger/accounts/{account_id}/months/seal', json={'month': '2026-09'})
    assert r.status_code == 403
    r = admin('post', f'/ledger/accounts/{account_id}/months/seal', json={'month': '2026-09'})
    assert r.status_code == 200 and r.json()['sealed'] is True
    assert r.json()['actual_total'] == 2300

    # 封账后迟到入账被拒
    r = analyst('post', f'/ledger/accounts/{account_id}/batches',
                json={'idem_key': 'k-late', 'rows': [{'day': DAYS[2], 'actual_kwh': 900}]})
    assert r.status_code == 409

    # 冲正（analyst 可登记；ref_day 的原始账保持不变）
    r = analyst('post', f'/ledger/accounts/{account_id}/reversals',
                json={'idem_key': 'rev1', 'ref_day': DAYS[0], 'actual_kwh': 1050, 'note': '电表复核'})
    assert r.status_code == 201 and r.json()['kind'] == 'reversal'
    # 冲正重试幂等
    again = analyst('post', f'/ledger/accounts/{account_id}/reversals',
                    json={'idem_key': 'rev1', 'ref_day': DAYS[0], 'actual_kwh': 1050})
    assert again.json()['id'] == r.json()['id']

    # 详情：月合计可追溯、状态齐全
    r = viewer('get', f'/ledger/accounts/{account_id}')
    detail = r.json()
    assert r.status_code == 200
    statuses = {e['status'] for e in detail['entries']}
    assert 'posted' in statuses
    months = {m['month']: m for m in detail['totals']}
    assert months['2026-09']['sealed'] is True and months['2026-09']['actual_total'] == 2300
    assert months[date.today().strftime('%Y-%m')]['reversal_count'] == 1
    assert all(m['entry_ids'] for m in detail['totals'])
    assert all(m['basis_version'] for m in detail['totals'])
