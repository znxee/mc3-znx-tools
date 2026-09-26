"""Audit the actual 3xLQ Matrix34 upload, not the scalar scratchpad mirror."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_city_counts_runtime import Audit

DMA_TAG = struct.pack('<4I', 0x10000004, 0, 0x11000000, 0x6804000C)


def lq_upload(memory, address):
    # R5900 LQ aligns DOWN; see PCSX2 R5900OpcodeImpl.cpp, LQ().
    return b''.join(memory[(address + off) & ~15:
                          ((address + off) & ~15) + 16]
                    for off in (0, 16, 32))


def audit(path, state=None):
    a = Audit(path, state)
    table = a.off(a.u(0x98))
    alignments = Counter()
    expected_by_payload = defaultdict(list)
    broken_by_payload = defaultdict(list)
    rows = []
    for hood in range(a.u(0x94)):
        ent = table + hood * 20
        count = a.u(ent + 12)
        if not count:
            continue
        arr = a.off(a.u(ent + 16), count * 96)
        for index in range(count):
            rec = arr + 96 * index
            matrix = a.base + rec + 32 + (a.delta or 0)
            cd = a.off(a.u(rec + 12))
            name_ptr = a.u(cd + 8)
            name_off = a.off(name_ptr) if name_ptr else cd + 0x70
            name = a.d[name_off:name_off + 160].split(b'\0')[0].decode('ascii', 'replace')
            if a.ram is not None:
                correct = bytes(a.ram[matrix:matrix + 48])
                broken = lq_upload(a.ram, matrix)
                assert correct == a.d[rec + 32:rec + 80], 'RAM/source matrix changed'
            else:
                correct = bytes(a.d[rec + 32:rec + 80])
                assert a.base % 16 == 0
                broken = lq_upload(a.d, rec + 32)
            src = struct.unpack('<12f', correct)
            sent = struct.unpack('<12f', broken)
            row = dict(name=name, matrix=f'{matrix:08X}', mod16=matrix % 16,
                       source_translation=src[9:12], sent_translation=sent[9:12],
                       source_up=src[3:6], sent_up=sent[3:6],
                       upload_matches_source=correct == broken)
            alignments[matrix % 16] += 1
            rows.append(row)
            expected_by_payload[correct].append(row)
            if correct != broken:
                broken_by_payload[broken].append(row)
    packets = []
    if a.ram is not None:
        at = 0
        while True:
            at = a.ram.find(DMA_TAG, at)
            if at < 0:
                break
            if a.ram[at + 64:at + 68] == struct.pack('<I', 0x1400000A):
                payload = bytes(a.ram[at + 16:at + 64])
                bad = broken_by_payload.get(payload, [])
                good = expected_by_payload.get(payload, [])
                if bad or good:
                    packets.append(dict(packet=f'{at:08X}',
                                        broken_matches=len(bad), good_matches=len(good),
                                        example=(bad or good)[0]))
            at += 1
    palm = [r for r in rows if 'palm' in r['name'].lower()]
    result = dict(pck=str(path), pck_sha256=hashlib.sha256(a.d).hexdigest(),
                  state=state, count=len(rows), alignments=dict(alignments),
                  upload_mismatches=sum(not r['upload_matches_source'] for r in rows),
                  matching_dma_packets=len(packets),
                  broken_dma_packets=sum(bool(p['broken_matches']) for p in packets),
                  good_dma_packets=sum(bool(p['good_matches']) for p in packets),
                  examples=(palm[:4] or rows[:4]),
                  dma_examples=sorted(packets, key=lambda p: not p['broken_matches'])[:4])
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pck')
    ap.add_argument('--state')
    ap.add_argument('--out', type=Path)
    args = ap.parse_args()
    result = audit(args.pck, args.state)
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    print(rendered)
    if args.out:
        args.out.write_text(rendered + '\n', encoding='utf-8')
