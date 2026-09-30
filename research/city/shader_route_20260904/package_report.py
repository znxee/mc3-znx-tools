"""Generate a reproducibility manifest for the selected, published RE package."""
import os
import ast
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    files={}
    for p in sorted(ROOT.rglob('*')):
        if not p.is_file() or '__pycache__' in p.parts or p.name=='package_manifest.json': continue
        if p.suffix in ('.i64','.id0','.id1','.nam','.til','.log'): continue
        if p.suffix=='.py': ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
        files[p.relative_to(ROOT).as_posix()]={'size':p.stat().st_size,'sha256':digest(p)}
    sources=[Path(os.environ.get('MC3_ELF', 'SLUS_213.55')),
        Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tools', 'mc3_city_build.py')),
        Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tools', 'output', 'la_tex_mapa.tsv')),
        Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tools', 'output', 'mc2_losangeles/compiled_ppf/losangeles_midnight_clear.ppf'))]
    route=json.loads((ROOT/'route_index.json').read_text())
    report={'date':'2026-09-05','fork':'MC3 Textures and Shaders',
        'decompiled_functions':len(route['functions']),
        'decompilation_export_errors':[r['address'] for r in route['functions'] if r['error']],
        'runtime_tested':False,'files':files,
        'inputs':{str(p):{'size':p.stat().st_size,'sha256':digest(p)} for p in sources}}
    (ROOT/'package_manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('Package files:',len(files),'functions:',len(route['functions']),'Python AST: OK')

if __name__=='__main__':main()
