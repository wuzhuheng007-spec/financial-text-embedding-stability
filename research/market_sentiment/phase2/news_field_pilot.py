"""Bounded official-event metadata pilot. No direction scoring, returns or fitting."""
from pathlib import Path
import datetime,hashlib,json,urllib.request
import pandas as pd

ROOT=Path(__file__).resolve().parent
EVENTS=[
 {'event_id':'US_FOMC_20190130','market':'US','source_url':'https://www.federalreserve.gov/newsevents/pressreleases/monetary20190130a.htm','official_declared_release_at_utc':'2019-01-30T19:00:00Z','event_started_at_utc':None,'public_document_date':'2019-01-30','time_basis':'Official statement explicitly declares 14:00 EST release; historical first retrieval not observed','event_type':'FOMC_policy_statement','source_fact':'维持利率并表示耐心决定后续调整。','expected_value':None,'expectation_source':None,'ex_ante_good_bad_label':None,'blocking_reason':'No contemporaneous expectation/surprise panel or full event universe'},
 {'event_id':'US_FOMC_20200323','market':'US','source_url':'https://www.federalreserve.gov/newsevents/pressreleases/monetary20200323a.htm','official_declared_release_at_utc':'2020-03-23T12:00:00Z','event_started_at_utc':None,'public_document_date':'2020-03-23','time_basis':'Official statement explicitly declares 08:00 EDT release; historical first retrieval not observed','event_type':'FOMC_market_functioning_announcement','source_fact':'按市场平稳运行和政策传导需要购买国债及机构MBS。','expected_value':None,'expectation_source':None,'ex_ante_good_bad_label':None,'blocking_reason':'Emergency-event selection and contemporaneous expectation intensity not established'},
 {'event_id':'CN_POLICY_20240924','market':'CN','source_url':'https://www.sse.com.cn/home/component/news/c/c_20240924_10762338.shtml','official_declared_release_at_utc':None,'event_started_at_utc':'2024-09-24T01:00:00Z','public_document_date':'2024-09-24','time_basis':'09:00 Beijing is conference start, not proof of exact first release of full transcript or each policy detail','event_type':'joint_policy_press_conference','source_fact':'宣布降准降息、房贷安排和支持股市的新政策工具。','expected_value':None,'expectation_source':None,'ex_ante_good_bad_label':None,'blocking_reason':'Exact transcript availability, individual policy speech times and prior expectations absent'}
]

def main():
    out=ROOT/'results/news_field_pilot';out.mkdir(parents=True,exist_ok=False)
    private=ROOT/'source_audit/news_html';private.mkdir(exist_ok=True)
    now=datetime.datetime.now(datetime.timezone.utc).isoformat();events=[]
    for record in EVENTS:
        x=dict(record);x.update({'actual_first_public_at_utc':None,'captured_at_utc':now,'source_snapshot_sha256':None,'snapshot_status':'NOT_DOWNLOADED','canonical_event_id':x['event_id'],'return_reaction':None,'sentiment_score':None,'financial_fit_eligible':False})
        try:
            request=urllib.request.Request(x['source_url'],headers={'User-Agent':'Mozilla/5.0 (research source verification)'})
            with urllib.request.urlopen(request,timeout=20) as response:data=response.read()
            assert len(data)>1000
            (private/(x['event_id']+'.html')).write_bytes(data)
            x['source_snapshot_sha256']=hashlib.sha256(data).hexdigest();x['snapshot_status']='CAPTURED_CURRENT_PAGE_NOT_HISTORICAL_VINTAGE'
        except Exception as e:x['snapshot_error']=str(e)
        events.append(x)
    (out/'events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
    pd.DataFrame(events).to_csv(out/'events.csv',index=False)
    summary={'status':'METADATA_PILOT_ONLY','events':len(events),'official_declared_release_times':sum(x['official_declared_release_at_utc'] is not None for x in events),'conference_start_only':sum(x['event_started_at_utc'] is not None for x in events),'captured_current_source_pages':sum(x['source_snapshot_sha256'] is not None for x in events),'actual_historical_first_public_times_observed':0,'expectation_pairs':0,'good_bad_labels':0,'news_reaction_scores':0,'financial_estimator_fits':0,'pilot_selection':'Three illustrative sources fixed before field pilot; no representative sample or efficacy claim','code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'timestamp_utc':now}
    (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))

if __name__=='__main__':main()
