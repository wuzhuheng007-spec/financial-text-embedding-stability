import argparse
import json
from .catalog import build_catalog,validate_catalog,export_catalog

def main():
    parser=argparse.ArgumentParser(description='Offline WRDS metadata catalog and local licensed row store')
    commands=parser.add_subparsers(dest='command',required=True)
    for action in ('build','validate','export','query','import','verify-import'):
        q=commands.add_parser(action)
        q.add_argument('--db',required=True)
        if action=='build':
            q.add_argument('--inventory',required=True);q.add_argument('--reviewed',required=True)
        if action=='export':q.add_argument('--out',required=True)
        if action=='query':q.add_argument('--sql',required=True);q.add_argument('--limit',type=int,default=1000)
        if action=='import':
            q.add_argument('--source',required=True);q.add_argument('--dataset-kind',required=True)
            q.add_argument('--member');q.add_argument('--output-root')
        if action=='verify-import':q.add_argument('--content-id',type=int)
    a=parser.parse_args()
    if a.command=='build':r=build_catalog(a.inventory,a.reviewed,a.db)
    elif a.command=='validate':r=validate_catalog(a.db)
    elif a.command=='export':r=export_catalog(a.db,a.out)
    else:
        from .local import import_csv,verify_import,query_readonly
        if a.command=='query':r=query_readonly(a.db,a.sql,limit=a.limit)
        elif a.command=='import':r=import_csv(a.db,a.source,a.dataset_kind,member=a.member,output_root=a.output_root)
        else:r=verify_import(a.db,content_id=a.content_id)
    print(json.dumps(r,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
