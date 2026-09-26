import os
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
SOURCE = Path(os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'mc3_ida/slus_213.55.i64'))
DB = ROOT / 'retail_shader_route.i64'
IDA = Path(os.path.join(os.environ.get('MC3_IDA', 'IDA'), 'idat.exe'))

parser = argparse.ArgumentParser()
parser.add_argument('script', type=Path)
args = parser.parse_args()
if not DB.exists():
    shutil.copy2(SOURCE, DB)
script = args.script.resolve()
result = subprocess.run([str(IDA), '-A', '-L' + str(ROOT / (script.stem + '.log')),
                         '-S' + str(script), str(DB)], cwd=ROOT,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print('IDA exit:', result.returncode)
raise SystemExit(result.returncode)
