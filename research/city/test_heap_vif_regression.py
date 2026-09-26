"""Independent all-model regression: repaired extractor vs original-heap VIF restoration."""
import os
import json,sys,tempfile
from pathlib import Path
T=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'));W=Path(os.path.join(os.environ.get('MC3_WORK', '.'), 'output'))
sys.path.insert(0,str(T));import mc3_heap_to_pck as H
def main():
    heap=(T/'output/heap_la729.bin').read_bytes();results=[]
    with tempfile.TemporaryDirectory(prefix='mc3_vif_regression_') as temp:
        out=Path(temp)/'mesh.pck'
        for line in (T/'output/la729_lote.tsv').read_text().splitlines():
            root,path=line.split('\t');name=Path(path).name
            r=H.emit(heap,0x017BC680,int(root,16),str(out),quiet=True)
            assert out.read_bytes()==(W/'la729_vif_restored_20260906'/name).read_bytes(),name
            results.append({'name':name,**r})
    report={'passed':True,'exact_models':len(results),'models':results}
    (W/'heap_vif_regression_20260906.json').write_text(json.dumps(report,indent=2))
    print('PASS: corrected extractor matches independent restoration byte-for-byte for',len(results),'models')
if __name__=='__main__':main()
