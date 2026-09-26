#!/usr/bin/env python3
"""Append a retail donor's embedded sky group to a generated PCK.

The historical builder wrote MapRoot+0x10 as an empty array to satisfy Place,
so mcSkyHatClass existed but received zero shaders. This graft is deliberately
small: it copies only rmcShaderBasic slots with an inline rmcTexturePS2 and
inline MipBuffer pixels. Special/animated shaders are left null instead of
copying a graph that is not understood yet.
"""

import argparse
import hashlib
import json
import os
import struct


def u16(data, offset):
    return struct.unpack_from('<H', data, offset)[0]


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


class Pck:
    def __init__(self, path):
        self.path = path
        self.data = bytearray(open(path, 'rb').read())
        self.base = u32(self.data, 0) - 0x80
        if u32(self.data, 0x0C) != len(self.data) - 0x80:
            raise ValueError('%s: header size does not add up' % path)

    def off(self, pointer):
        offset = pointer - self.base
        if not 0x80 <= offset < len(self.data) - 3:
            raise ValueError('%s: pointer outside %#x' % (self.path, pointer))
        return offset

    def virtual(self, offset):
        return self.base + offset


def build(target_path, donor_path, output_path, audit_path=None):
    target = Pck(target_path)
    donor = Pck(donor_path)
    before = bytes(target.data)
    if target.base != donor.base:
        raise ValueError('different bases')
    if u32(target.data, 0x80) != u32(donor.data, 0x80):
        raise ValueError('different MapRoot token space')

    donor_desc = donor.off(u32(donor.data, 0x80 + 0x10))
    donor_slots = donor.off(u32(donor.data, donor_desc + 4))
    donor_count = u16(donor.data, donor_desc + 8)
    if donor_count == 0 or donor_count > 64:
        raise ValueError('suspicious sky count: %d' % donor_count)

    data = target.data

    def append(blob, align=16):
        while len(data) % align:
            data.append(0xCD)
        offset = len(data)
        data.extend(blob)
        return offset

    copied = []
    skipped = []
    slot_values = []
    for index in range(donor_count):
        pointer = u32(donor.data, donor_slots + 4 * index)
        if not pointer:
            slot_values.append(0)
            skipped.append({'slot': index, 'reason': 'null'})
            continue
        try:
            shader = donor.off(pointer)
            texture = donor.off(u32(donor.data, shader + 8))
        except ValueError:
            slot_values.append(0)
            skipped.append({'slot': index, 'reason': 'outside'})
            continue

        # Proven shape of rmcShaderBasic + rmcTexturePS2.
        if u32(donor.data, shader + 4) != 0 or donor.data[texture + 4] != 0:
            slot_values.append(0)
            skipped.append({'slot': index, 'reason': 'non-basic-or-non-texture0'})
            continue
        try:
            mip = donor.off(u32(donor.data, texture + 0x48))
        except ValueError:
            slot_values.append(0)
            skipped.append({'slot': index, 'reason': 'no-inline-mip'})
            continue
        mip_size = u32(donor.data, mip + 0x0C)
        if mip_size < 0x20 or mip + mip_size > len(donor.data):
            slot_values.append(0)
            skipped.append({'slot': index, 'reason': 'bad-mip-size',
                            'size': mip_size})
            continue

        mip_blob = bytearray(donor.data[mip:mip + mip_size])
        for field in (0x00, 0x04, 0x08, 0x10):
            struct.pack_into('<I', mip_blob, field, 0)
        mip_out = append(mip_blob)

        texture_blob = bytearray(donor.data[texture:texture + 0xA0])
        # Only level 0 is inline in this group; runtime/name pointers cannot
        # keep pointing into the donor file.
        for field in (0x48, 0x54, 0x60, 0x6C, 0x78):
            struct.pack_into('<I', texture_blob, field, 0)
        struct.pack_into('<I', texture_blob, 0x48, target.virtual(mip_out))
        texture_out = append(texture_blob)
        struct.pack_into('<I', data, mip_out + 0x08,
                         target.virtual(texture_out))

        shader_blob = bytearray(donor.data[shader:shader + 0x10])
        struct.pack_into('<I', shader_blob, 0x08,
                         target.virtual(texture_out))
        shader_out = append(shader_blob)
        slot_values.append(target.virtual(shader_out))
        copied.append({'slot': index, 'mip_size': mip_size,
                       'shader_offset': shader_out,
                       'texture_offset': texture_out,
                       'mip_offset': mip_out})

    if not copied:
        raise ValueError('no copyable sky slot')
    slots_out = append(b''.join(struct.pack('<I', item)
                               for item in slot_values))
    desc_blob = bytearray(donor.data[donor_desc:donor_desc + 0x10])
    struct.pack_into('<I', desc_blob, 0x04, target.virtual(slots_out))
    desc_out = append(desc_blob)
    struct.pack_into('<I', data, 0x80 + 0x10, target.virtual(desc_out))
    while len(data) % 16:
        data.append(0xCD)
    struct.pack_into('<I', data, 0x0C, len(data) - 0x80)

    changed_before_append = [index for index, (old, new) in
                             enumerate(zip(before, data)) if old != new]
    allowed = set(range(0x0C, 0x10)) | set(range(0x90, 0x94))
    unexpected = [index for index in changed_before_append if index not in allowed]
    if unexpected:
        raise ValueError('unexpected change before the append: %s' % unexpected[:20])

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'wb') as stream:
        stream.write(data)

    check = Pck(output_path)
    desc = check.off(u32(check.data, 0x90))
    slots = check.off(u32(check.data, desc + 4))
    counts = [u16(check.data, desc + 8), u16(check.data, desc + 10),
              u32(check.data, desc + 12)]
    values = [u32(check.data, slots + 4 * index)
              for index in range(counts[0])]
    valid = 0
    for pointer in values:
        if not pointer:
            continue
        shader = check.off(pointer)
        texture = check.off(u32(check.data, shader + 8))
        mip = check.off(u32(check.data, texture + 0x48))
        if u32(check.data, mip + 8) != check.virtual(texture):
            raise ValueError('MipBuffer owner does not point to a texture')
        valid += 1
    if counts != [donor_count, donor_count, 0] or valid != len(copied):
        raise ValueError('sky count validation failed')

    result = {
        'target': os.path.abspath(target_path),
        'donor': os.path.abspath(donor_path),
        'output': os.path.abspath(output_path),
        'target_sha256': hashlib.sha256(before).hexdigest().upper(),
        'output_sha256': hashlib.sha256(bytes(data)).hexdigest().upper(),
        'target_size': len(before),
        'output_size': len(data),
        'sky_counts': counts,
        'copied': copied,
        'skipped': skipped,
        'valid_slots': valid,
        'prefix_changes': changed_before_append,
        'checks': {
            'header_size': u32(data, 0x0C) == len(data) - 0x80,
            'only_header_and_maproot_pointer_changed': not unexpected,
            'descriptor_counts': counts == [donor_count, donor_count, 0],
            'slot_graphs': valid == len(copied),
        },
    }
    if audit_path:
        with open(audit_path, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
    print('sky: %d/%d Basic+TexturePS2 slots copied; %d special ones null' %
          (valid, donor_count, len(skipped)))
    print('written:', output_path, len(data), 'bytes')
    print('SHA256:', result['output_sha256'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True)
    parser.add_argument('--donor', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--audit')
    args = parser.parse_args()
    build(args.target, args.donor, args.out, args.audit)


if __name__ == '__main__':
    main()
