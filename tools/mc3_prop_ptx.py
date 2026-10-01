#!/usr/bin/env python3
"""Extract the prop particle rules compiled into a city's *_props.pck as text .ptx.

A retail prop that throws particles (hydrant spray, newspapers, gas pump
sparks, bench splinters...) carries its rules inside the props resource
($/resources/prop/<city>_<tod>_<weather>_props.pck), not in tune/effects: the
text .ptx files there are only a few left-overs. Each rule is an
mcPropParticleBirthRule (0x190 bytes) followed by its name, "<type>_<part>"
(d_prop_hydrant_01x_particle_hydrant_firehydrant); +4 points at that name.

The same object loads from text: mcPropParticleBirthRule::LoadWithHash(name)
0x390070 reads tune/effects/<name>.ptx (vtable 0x62CD38: directory
"tune/effects", extension/block "ptx"). This tool writes that text from the
binary, field by field, so any mod can LoadWithHash the retail effects:

    swPtxBirth::FileIO 0x1EFE7C              mcPropParticleBirthRule 0x3900F8
    +80 Life  +84 LifeVar  +88 PositionVar   +0x160 m_inheritMatrix (u8)
    +100 Velocity +112 VelocityVar           +0x161 m_sprayAfterBroken
    +124 VelocityDamping  +136/+138 tiles    +0x162 m_inheritCollisionVelocity
    +152 RadiusBirth +160 RadiusDeathPercent +0x163 m_bRecieveAmbientLighting
    +164 radiusChangeRate +168 useDeathPct   +0x164 m_bImpactStrengthAffects...
    +172 Gravity +176/+192 ColorBirth/Death  +0x168 m_minForceToSpawn
    +208 RotateSpeed +212 ColorRamp +216 Type +0x16C m_emissive
    +220 FrameRate +224/+236 Freq/FreqVar    +0x170 m_alwaysOn
    +248 RadiusVar +252 RotateVar            +0x178 m_position
    +256 cycleFrames +260 m_fadeInTime       +0x184 m_spewTimeLimit
    +268 Rate +272 NumInitialParticles       +0x188 m_emitRate
    +276 TextureName[40] +316 BlendSet +320 ColorMod +324 MinShadowClamp  +76 RotatePtx

The texture is not part of the rule: the prop loader calls
swPtxBirth::SetTexture(rule, <x>_shared_particle, 8, 8) and the tiles index
that 8x8 atlas, so rules from detroit's pack go with d_shared_particle.

    python mc3_prop_ptx.py detroit_dusk_clear_props.pck --list
    python mc3_prop_ptx.py detroit_dusk_clear_props.pck --out $MC3_HOSTFS/ASSETS/tune/effects
"""
import argparse
import os
import re
import struct
import sys

RULE_SIZE = 0x190
ROOT = 0x80

FIELDS = [   # (text name, offset, kind)
    ('TextureName', 276, 'str'),
    ('PositionVar', 88, 'v3'),
    ('RadiusVar', 248, 'f'),
    ('RadiusBirth', 152, 'v2'),
    ('useDeathPercent', 168, 'b'),
    ('radiusChangeRate', 164, 'f'),
    ('RadiusDeathPercent', 160, 'f'),
    ('Life', 80, 'f'),
    ('LifeVar', 84, 'f'),
    ('Velocity', 100, 'v3'),
    ('VelocityVar', 112, 'v3'),
    ('VelocityDamping', 124, 'v3'),
    ('Gravity', 172, 'f'),
    ('StartTextureTile', 136, 'h'),
    ('EndTextureTile', 138, 'h'),
    ('cycleFrames', 256, 'b'),
    ('ColorBirth', 176, 'v4'),
    ('ColorDeath', 192, 'v4'),
    ('m_fadeInTime', 260, 'f'),
    ('NumInitialParticles', 272, 'i'),
    ('Rate', 268, 'f'),
    ('Type', 216, 'i'),
    ('FrameRate', 220, 'f'),
    ('Freq', 224, 'v3'),
    ('FreqVar', 236, 'v3'),
    ('BlendSet', 316, 'i'),
    ('ColorRamp', 212, 'i'),
    ('RotatePtx', 76, 'b'),
    ('RotateSpeed', 208, 'f'),
    ('RotateVar', 252, 'f'),
    ('ColorMod', 320, 'b'),
    ('MinShadowClamp', 324, 'f'),
    ('m_inheritMatrix', 0x160, 'b'),
    ('m_sprayAfterBroken', 0x161, 'b'),
    ('m_inheritCollisionVelocity', 0x162, 'b'),
    ('m_bRecieveAmbientLighting', 0x163, 'b'),
    ('m_bImpactStrengthAffectsSpawnRate', 0x164, 'b'),
    ('m_minForceToSpawn', 0x168, 'f'),
    ('m_alwaysOn', 0x170, 'b'),
    ('m_emissive', 0x16C, 'f'),
    ('m_position', 0x178, 'v3'),
    ('m_spewTimeLimit', 0x184, 'f'),
    ('m_emitRate', 0x188, 'f'),
]


def rules(data):
    """[(name, file offset of the rule)] of every particle rule in a props pack."""
    base = struct.unpack_from('<I', data, 0)[0]
    out = []
    for m in re.finditer(rb'[a-z]_prop_[A-Za-z0-9_]+?_particle_[A-Za-z0-9_]+\0', data):
        off = m.start() - RULE_SIZE
        if off < ROOT:
            continue
        if struct.unpack_from('<I', data, off + 4)[0] != base + m.start() - ROOT:
            continue
        out.append((m.group()[:-1].decode(), off))
    return out


def value(data, off, kind):
    if kind == 'f':
        return struct.unpack_from('<f', data, off)[0]
    if kind in ('v2', 'v3', 'v4'):
        return struct.unpack_from('<%df' % int(kind[1]), data, off)
    if kind == 'b':
        return data[off]
    if kind == 'h':
        return struct.unpack_from('<h', data, off)[0]
    if kind == 'i':
        return struct.unpack_from('<i', data, off)[0]
    if kind == 'str':
        return data[off:off + 40].split(b'\0')[0].decode('latin-1')
    raise ValueError(kind)


def to_text(data, off):
    lines = ['type: a', 'ptx {']
    for name, rel, kind in FIELDS:
        v = value(data, off + rel, kind)
        if kind == 'str':
            s = '"%s"\t' % v
        elif kind == 'f':
            s = '%f' % v
        elif kind.startswith('v'):
            s = '\t'.join('%f' % x for x in v)
        else:
            s = '%d' % v
        lines.append('  %s %s ' % (name, s))
    lines.append('}')
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('pck')
    ap.add_argument('--out', help='write <name>.ptx files here (existing files are kept unless --force)')
    ap.add_argument('--only', action='append', default=[], help='substring of the rule names to write')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    data = open(a.pck, 'rb').read()
    found = rules(data)
    if a.only:
        found = [(n, o) for n, o in found if any(s in n for s in a.only)]
    for name, off in found:
        if a.list or not a.out:
            g = lambda f: value(data, off + dict((n, r) for n, r, k in FIELDS)[f], dict((n, k) for n, r, k in FIELDS)[f])
            print('%-58s tiles %3d-%-3d emit %6.2f always %d spray %d spew %5.2f force %8.1f life %.2f' % (
                name, g('StartTextureTile'), g('EndTextureTile'), g('m_emitRate'), g('m_alwaysOn'),
                g('m_sprayAfterBroken'), g('m_spewTimeLimit'), g('m_minForceToSpawn'), g('Life')))
        if a.out:
            path = os.path.join(a.out, name + '.ptx')
            if os.path.exists(path) and not a.force:
                print('kept (exists): %s' % path)
                continue
            open(path, 'w', newline='\n').write(to_text(data, off))
    if a.out:
        print('%d rule(s) -> %s' % (len(found), a.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
