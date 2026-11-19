"""Bounded primary-source statement-link census; metadata is not news surprise."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.request import Request,urlopen
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor
import datetime,hashlib,json,re
import pandas as pd

R=Path(__file__).resolve().parent
class Parser(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.link=None;self.heading=None;self.section=''
    def handle_starttag(self,tag,attrs):
        if tag in ['h4','h5']:self.heading={'tag':tag,'text':''}
        if tag=='a':self.link={'href':dict(attrs).get('href',''),'text':'','section':self.section}
    def handle_data(self,data):
        if self.link is not None:self.link['text']+=data
        if self.heading is not None:self.heading['text']+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.link is not None:self.links.append(self.link);self.link=None
        if self.heading is not None and tag==self.heading['tag']:self.section=self.heading['text'].strip();self.heading=None

def one(item):
    year,url=item;dest=R/'raw'/f'fed_index_{year}.html'
    try:
        blob=urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0 (historical academic research)'}),timeout=30).read();dest.write_bytes(blob)
        p=Parser();p.feed(blob.decode('utf-8',errors='replace'))
        records=[]
        for a in p.links:
            href=urljoin(url,a['href']);txt=' '.join(a['text'].split())
            eligible=(txt=='Statement') if year!='calendar' else (txt=='HTML' and re.search(r'/pressreleases/monetary\d{8}a\.htm$',href) is not None)
            if not eligible:continue
            dates=re.findall(r'(?:19|20)\d{6}',href)
            date=None
            if dates:
                try:date=datetime.datetime.strptime(dates[-1],'%Y%m%d').date().isoformat()
                except ValueError:pass
            if date is not None and not ('1996-01-01'<=date<='2026-11-17'):continue
            if year=='calendar' and date is not None and int(date[:4])<2021:continue
            records.append({'statement_url':href,'source_index_url':url,'source_index_sha256':hashlib.sha256(blob).hexdigest(),'source_archive_year':str(year),'section':a['section'],'link_text':txt,'date_from_url':date,'date_status':'URL_DATE_ONLY_NOT_HISTORICAL_RELEASE_TIMESTAMP','statement_fulltext_this_phase':'NOT_FETCHED','surprise_score':None,'causal_attribution':None})
        return {'archive_year':str(year),'url':url,'sha256':hashlib.sha256(blob).hexdigest(),'bytes':len(blob),'status':'FETCHED_AND_LINKS_PARSED','links':len(records),'capture_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()},records
    except Exception as e:return {'archive_year':str(year),'url':url,'status':'FETCH_FAILED','error':str(e)},[]

def main():
    dest=R/'results/official_calendar';dest.mkdir(exist_ok=True)
    items=[(y,f'https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm') for y in range(1996,2021)]+[('calendar','https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm')]
    indexes=[];records=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for a,b in pool.map(one,items):indexes.append(a);records+=b;print(a['archive_year'],a['status'],a.get('links'),flush=True)
    pd.DataFrame(indexes).to_csv(dest/'index_fetch_inventory.csv',index=False)
    links=pd.DataFrame(records)
    links.to_csv(dest/'statement_links.csv',index=False)
    if len(links):
        links['year']=pd.to_datetime(links.date_from_url).dt.year
        links.groupby('year',dropna=False).agg(anchor_rows=('statement_url','size'),unique_urls=('statement_url','nunique')).reset_index().to_csv(dest/'year_coverage.csv',index=False)
    summary={'requested_indexes':len(items),'successful_indexes':sum(x['status']=='FETCHED_AND_LINKS_PARSED' for x in indexes),'failed_indexes':sum(x['status']=='FETCH_FAILED' for x in indexes),'anchor_rows':len(links),'unique_statement_urls':int(links.statement_url.nunique()) if len(links) else 0,'unknown_url_dates':int(links.date_from_url.isna().sum()) if len(links) else 0,'limitations':['No first-release timestamp reconstruction','Historical archive Statements and current-calendar HTML links only; unscheduled releases outside those indexes may be absent','1990s no-change meetings did not all publish statements; counts do not measure event frequency','No China-wide news universe; no causal labels or sentiment/surprise scoring','Current archive snapshot, not historical information-set certification'],'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (dest/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)

if __name__=='__main__':main()
