"""Retrieve public papers for private reading; publish only source identities."""
from pathlib import Path
import concurrent.futures
import datetime
import hashlib
import json
import sys
import urllib.request
import fitz

SOURCES = {
    'BW2007': 'https://pages.stern.nyu.edu/~jwurgler/papers/wurgler_baker_investor_sentiment.pdf',
    'T2007': 'https://www.uts.edu.au/globalassets/sites/default/files/adg_cons2015_tetlock-journal-of-finance-2007.pdf',
    'HS2016': 'https://www.federalreserve.gov/econresdata/feds/2016/files/2016048pap.pdf',
    'DEG2015': 'https://academicweb.nd.edu/~zda/FEARS.pdf',
    'LL2025': 'https://link.springer.com/content/pdf/10.1007/s11142-025-09897-1.pdf',
}

def acquire(item, private_root):
    key, url = item
    row = {'id': key, 'url': url, 'retrieved_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=25) as response:
            raw = response.read()
            row['resolved_url'] = response.url
        if not raw.startswith(b'%PDF'):
            raise ValueError('Response is not a PDF')
        doc = fitz.open(stream=raw, filetype='pdf')
        pages = [p.get_text() for p in doc]
        path = private_root / (key + '.pdf')
        if path.exists():
            raise FileExistsError('Refuse to overwrite private original ' + str(path))
        path.write_bytes(raw)
        text = '\n'.join('=== PDF PAGE ' + str(n+1) + ' ===\n' + t for n,t in enumerate(pages))
        (private_root / (key + '.txt')).write_text(text)
        row.update({'status': 'DOWNLOADED_AND_TEXT_EXTRACTED',
                    'pdf_sha256': hashlib.sha256(raw).hexdigest(),
                    'bytes': len(raw), 'pdf_pages': len(pages),
                    'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'raw_published': False})
    except Exception as exc:
        row.update({'status': 'DOWNLOAD_FAILED', 'error': type(exc).__name__ + ': ' + str(exc)})
    return row

if __name__ == '__main__':
    private_root = Path(sys.argv[1]).resolve()
    private_root.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        rows = list(pool.map(lambda x: acquire(x, private_root), SOURCES.items()))
    result = {'operation': 'Public-document retrieval and text extraction, not empirical replication',
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'pymupdf_version': fitz.VersionBind, 'papers': rows}
    (Path(__file__).parent / 'acquisition_manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(rows, ensure_ascii=False))
