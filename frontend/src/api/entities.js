import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const ledger={
 accounts:()=>api('/ledger/accounts'),
 open:data=>api('/ledger/accounts',{method:'POST',body:JSON.stringify(data)}),
 get:id=>api('/ledger/accounts/'+id),
 submit:(id,data)=>api('/ledger/accounts/'+id+'/batches',{method:'POST',body:JSON.stringify(data)}),
 confirm:(id,batchId)=>api('/ledger/accounts/'+id+'/batches/'+batchId+'/confirm',{method:'POST'}),
 discard:(id,batchId)=>api('/ledger/accounts/'+id+'/batches/'+batchId+'/discard',{method:'POST'}),
 reverse:(id,data)=>api('/ledger/accounts/'+id+'/reversals',{method:'POST',body:JSON.stringify(data)}),
 seal:(id,month)=>api('/ledger/accounts/'+id+'/months/seal',{method:'POST',body:JSON.stringify({month})}),
};
