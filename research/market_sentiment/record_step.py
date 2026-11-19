"""Append a real, evidence-bound step; never rewrite an earlier research record."""
from pathlib import Path
import argparse,datetime,hashlib,json

def record(root,entry):
    path=root/'steps.json'
    old=json.loads(path.read_text()) if path.exists() else []
    if any(x['id']==entry['id'] for x in old):raise ValueError('Step ID already exists; append a correction with a new ID')
    for required in ['id','title','plan','actual','status','evidence','claims','limits']:
        if required not in entry:raise ValueError('Missing '+required)
    hashes={}
    for name in entry['evidence']:
        f=(root/name).resolve()
        if not f.is_relative_to(root.resolve()) or not f.is_file():raise ValueError('Missing or unsafe evidence: '+name)
        hashes[name]=hashlib.sha256(f.read_bytes()).hexdigest()
    entry['evidence_sha256']=hashes
    entry.setdefault('recorded_at_utc',datetime.datetime.now(datetime.timezone.utc).isoformat())
    old.append(entry)
    path.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 逐步研究日志','', '记录时间为本条实际写入时间；历史运行时间仅在原回执中存在时引用。不是后台自动研究记录。','']
    for x in old:
        lines += ['## '+x['id']+' · '+x['title'],'', '**状态：** '+x['status']+'；记录UTC：'+x['recorded_at_utc'],'', '**计划：** '+x['plan'],'','**实际：** '+x['actual'],'','**结论：** '+x['claims'],'','**限制：** '+x['limits'],'','证据：'+ '、'.join('['+n+']('+n+')' for n in x['evidence']),'']
    (root/'JOURNAL.md').write_text('\n'.join(lines),encoding='utf-8')
    return entry

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--entry',type=Path,required=True);a=p.parse_args()
    r=record(Path(__file__).resolve().parent,json.loads(a.entry.read_text()))
    print(json.dumps({'recorded':r['id'],'status':r['status'],'evidence_files':len(r['evidence'])}))
