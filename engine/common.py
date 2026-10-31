"""Standard-library utilities; importing this module performs no external work."""
import hashlib
import json
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]

def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def stamp():return time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())

def inventory(root):
    root=Path(root);result={}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ValueError('Symlinks are not supported in a candidate')
        if path.is_file():result[path.relative_to(root).as_posix()]=sha(path)
    return result

def redact(value,replacements):
    if isinstance(value,str):
        for original,label in sorted(replacements.items(),key=lambda kv:-len(kv[0])):value=value.replace(str(original),label)
        return value
    if isinstance(value,list):return [redact(x,replacements) for x in value]
    if isinstance(value,dict):return {k:redact(v,replacements) for k,v in value.items()}
    return value
