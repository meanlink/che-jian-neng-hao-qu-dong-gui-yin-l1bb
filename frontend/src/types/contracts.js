/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,name:string,line_code:string,line_name:string,job_id:number,basis_version:string,basis_checksum:string,basis:Object}} LedgerAccount */
/** @typedef {{id:number,account_id:number,idem_key:string,kind:string,status:string,note:string,reject_reason:string}} LedgerBatch */
/** @typedef {{id:number,entry_type:string,status:string,day:string,ref_day:?string,reversal_of_id:?number,predicted_kwh:?number,actual_kwh:number,variance_kwh:?number,book_month:string}} LedgerEntry */
/** @typedef {{month:string,sealed:boolean,entry_count:number,reversal_count:number,predicted_total:number,actual_total:number,variance_total:number,basis_version:string,entry_ids:number[]}} LedgerMonthTotal */
export {};
