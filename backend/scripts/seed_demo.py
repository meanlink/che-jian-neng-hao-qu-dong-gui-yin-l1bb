import time, json
from types import SimpleNamespace
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, LedgerAccount
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.services import ledger
from src.utils.security import hash_password
from src.utils.serialization import checksum
def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        seed_ledger(db, m)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,LedgerAccount]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts


def seed_ledger(db, m):
    """幂等演示账本：冻结批准口径，9 月日账已入账、10 月留一批待入账，月份均不封。"""
    job=db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name']))
    admin=db.scalar(select(User).where(User.role=='admin'))
    if not job or db.scalar(select(LedgerAccount).where(LedgerAccount.job_id==job.id, LedgerAccount.line_code=='冲压P01')):
        return
    account=ledger.create_account(db, SimpleNamespace(
        name='冲压P01 · 节能考核账本', job_id=job.id, line_code='冲压P01', line_name='冲压一线'), admin)
    db.flush()
    actual_by_day={str(r['day']): float(r['kwh']) for r in db.get(Dataset, job.dataset_id).records}
    predictions=account.basis['predictions']
    days=sorted(predictions)
    sept=[day for day in days if day.startswith('2026-09')]
    october=[day for day in days if day.startswith('2026-10')]
    if sept:
        batch=ledger.submit_batch(db, account.id, SimpleNamespace(
            idem_key='seed-sept-2026', rows=[SimpleNamespace(day=day, actual_kwh=actual_by_day[day]) for day in sept]), admin)
        db.flush(); ledger.confirm_batch(db, account.id, batch.id, admin); db.flush()
    if october:
        ledger.submit_batch(db, account.id, SimpleNamespace(
            idem_key='seed-oct-pending', rows=[SimpleNamespace(day=day, actual_kwh=actual_by_day[day]) for day in october]), admin)
        db.flush()
    db.add(Audit(actor='seed', action='demo.ledger', detail={'account': account.id}, at=time.time()))


if __name__=='__main__':seed()
