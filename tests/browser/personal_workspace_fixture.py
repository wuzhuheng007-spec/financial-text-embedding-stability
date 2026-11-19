"""Synthetic browser acceptance; not the user's Windows session."""
import os,sys,json,threading,functools
from pathlib import Path
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(sys.argv[1]).resolve();OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'apps/quant_portal'));sys.path.insert(0,str(ROOT))
os.environ.update(DJANGO_SETTINGS_MODULE='portal.settings',DEBUG='1',DJANGO_TESTING='1',PERSONAL_MODE='1',SECRET_KEY='synthetic-preview-key-only',ALLOWED_HOSTS='testserver,127.0.0.1')
import django
django.setup()
from django.conf import settings
settings.DATABASES['default']['NAME']=OUT/'preview.sqlite3'
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import Client
from core.models import UserPreferences,WatchlistItem
call_command('migrate',verbosity=0)
u,_=get_user_model().objects.get_or_create(username='owner@local.invalid')
prefs,_=UserPreferences.objects.get_or_create(user=u)
for symbol in ['NVDA','MU']:
 WatchlistItem.objects.get_or_create(user=u,symbol=symbol,defaults={'asset_type':'EQUITY','display_name':'Synthetic preview'})
c=Client();c.force_login(u)
for lang in ['zh','en']:
 prefs.display_language=lang;prefs.save()
 html=c.get('/').content.decode().replace('<body>','<body><div style="background:#dcecff;padding:8px;text-align:center">SYNTHETIC PREVIEW · 合成验收页面 · 非实时数据</div>')
 (OUT/f'{lang}.html').write_text(html)
