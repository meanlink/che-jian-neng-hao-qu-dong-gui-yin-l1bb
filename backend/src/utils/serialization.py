import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
def ledger_account_dict(a):
    return {'id':a.id,'name':a.name,'line_code':a.line_code,'line_name':a.line_name,'job_id':a.job_id,
            'basis_version':a.basis_version,'basis_checksum':a.basis_checksum,'created_at':a.created_at,
            'basis':a.basis}
def ledger_batch_dict(b):
    return {k:getattr(b,k) for k in ['id','account_id','idem_key','kind','status','note','reject_reason',
                                     'created_by','created_at','posted_at']}
def ledger_entry_dict(e):
    return {k:getattr(e,k) for k in ['id','account_id','batch_id','entry_type','status','day','ref_day',
                                     'reversal_of_id','predicted_kwh','actual_kwh','variance_kwh',
                                     'book_month','created_at','posted_at']}
def ledger_month_dict(m):
    return {k:getattr(m,k) for k in ['id','account_id','month','sealed','entry_count','reversal_count',
                                     'predicted_total','actual_total','variance_total','basis_version',
                                     'sealed_by','sealed_at']}
