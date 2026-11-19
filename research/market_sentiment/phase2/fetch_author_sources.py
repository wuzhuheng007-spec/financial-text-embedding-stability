"""Fetch pinned public author inputs privately and verify exact bytes; no credentials."""
from pathlib import Path
import argparse,hashlib,json,urllib.request,urllib.parse

def run(registry,out):
    records=json.loads(registry.read_text());out.mkdir(parents=True,exist_ok=True)
    historical=registry.with_name('revision_history.json')
    history=json.loads(historical.read_text())
    for x in history['snapshots']:
        records.append({'name':x['file'],'path':'SENTIMENT.xlsx','commit':x['commit'],'local_sha256':x['sha256']})
    records.append({'name':'SENTIMENT_nyu.xlsx','url':'https://pages.stern.nyu.edu/~jwurgler/data/SENTIMENT.xlsx','local_sha256':'3580398aaba6b72ad988844416c7b0ac9caca24fd2ab1d9149a983ec63b9f7a2'})
    for item in records:
        url=item.get('url') or 'https://raw.githubusercontent.com/BWInvestorSentimentIndex/BakerandWurglerInvestorSentimentIndex/'+item['commit']+'/'+urllib.parse.quote(item['path'])
        destination=out/item['name']
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest()!=item['local_sha256']:raise ValueError('Existing input differs; no overwrite: '+item['name'])
            continue
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=45) as response:data=response.read()
        actual=hashlib.sha256(data).hexdigest()
        if actual!=item['local_sha256']:raise ValueError('Input hash mismatch; no substitution accepted: '+item['name'])
        destination.write_bytes(data)
        print(json.dumps({'source':item['name'],'sha256':actual,'verified':True}))
    (out/'source_registry_complete.json').write_text(registry.read_text())
    (out/'revision_history.json').write_text(historical.read_text())

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--registry',type=Path,default=Path(__file__).with_name('source_registry_complete.json'));p.add_argument('--out',type=Path,required=True);a=p.parse_args();run(a.registry,a.out)
