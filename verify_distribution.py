"""Verify the complete versioned distribution; stdlib only, no network or API."""
import hashlib,json
from pathlib import Path
def main():
    root=Path(__file__).resolve().parent;m=json.loads((root/'release_manifest.json').read_text(encoding='utf-8'))
    assert m['version']=='0.1.2'
    failures=[]
    for r in m['files']:
        p=root/r['path']
        if not p.is_file() or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256']:failures.append(r['path'])
    assert not failures,failures[:10]
    print(json.dumps({'passed':True,'version':m['version'],'files':len(m['files']),'api_calls':0,'network_calls':0}))
if __name__=='__main__':main()
