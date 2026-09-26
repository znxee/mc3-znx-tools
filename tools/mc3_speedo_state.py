"""mc3_speedo_state.py - read digital_speedometer.mod's state out of a savestate

The module keeps one struct behind its `state()` accessor, stamped with the
magic 'SHPK' so it can be found without knowing where the loader relocated the
module to. Everything the overlay decided is in there, which makes a savestate a
complete diagnosis: whether the font resolved, whether the three gauge redirects
were installed, and - the reason this file exists - whether the backing plate's
texture ever loaded.

    python mc3_speedo_state.py "PCSX2/sstates/SLUS-21355 (B3FD5361).01.p2s"
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get('MC3BOOT', os.path.join(os.path.dirname(HERE), 'mc3boot')))
import mc3_inject  # noqa: E402

MAGIC = 0x4B504853          # 'SHPK'

# Field order from digital_speedometer_state. Keep in sync with the .cpp.
FIELDS = ('magic', 'hook_calls', 'font_attempts', 'draw_calls', 'digital_font',
          'old_speedometer_disabled', 'old_tachometer_redirected',
          'old_gear_redirected', 'last_kilometres_per_hour', 'last_digits',
          'tachometer_calls', 'tachometer_draw_calls', 'last_rpm', 'last_gear',
          'badge_tex', 'badge_image', 'badge_attempts', 'badge_draws')
NAME_OFF = len(FIELDS) * 4          # char badge_name_buf[24]
FONT_OFF = NAME_OFF + 24            # char font_name_buf[16]


def find(ee):
    target = struct.pack('<I', MAGIC)
    out, i = [], 0
    while True:
        i = ee.find(target, i)
        if i < 0:
            return out
        if i % 4 == 0:
            out.append(i)
        i += 4


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    ee = mc3_inject.ee_from_savestate(argv[1])
    found = find(ee)
    if not found:
        print('state not found (magic SHPK). Did the mod run in this savestate?')
        return 1

    a = found[0]
    v = {n: struct.unpack_from('<I', ee, a + k * 4)[0]
         for k, n in enumerate(FIELDS)}
    print('digital_speedometer @ %08X' % a)
    print('  frames            %d hook, %d draws' % (v['hook_calls'],
                                                        v['draw_calls']))
    print('  font              %08X  (%d attempt(s))'
          % (v['digital_font'], v['font_attempts']))
    print('  redirects         speedometer %d, tachometer %d, gear %d'
          % (v['old_speedometer_disabled'], v['old_tachometer_redirected'],
             v['old_gear_redirected']))
    print('  readings          %d km/h, %d rpm, gear %d'
          % (v['last_kilometres_per_hour'], v['last_rpm'], v['last_gear']))
    name = ee[a + NAME_OFF:a + NAME_OFF + 24].split(b'\0')[0]
    print('  plate             image %08X -> tex %08X'
          % (v['badge_image'], v['badge_tex']))
    print('                    %d attempt(s), %d draw(s)'
          % (v['badge_attempts'], v['badge_draws']))
    print('                    requested name: %r' % name.decode('latin-1'))

    # The three states worth telling apart, because each has its own cause.
    if v['badge_tex'] and v['badge_draws']:
        print('  -> the plate loaded AND drew. If it does not show on screen, the'
              ' problem is the blit: rectangle, UV or render state.')
    elif v['badge_tex']:
        print('  -> it loaded but did NOT draw: draw_badge left before the blit,'
              ' or the pointer failed valid_game_pointer.')
    elif v['badge_image']:
        print('  -> the image loaded but gfxSimpleTex::Create gave no texture.')
    elif v['badge_attempts']:
        print('  -> gfxLoadImageAll returned 0 in %d attempt(s): it is the NAME or'
              ' the file, not the drawing.' % v['badge_attempts'])
        print('     gfxLoadTexImage already asks for `texture/<name>.tex` by itself -'
              ' the globals off_61C288/off_61C290 hold "texture" and "tex" - so the'
              ' name has to be the bare BASENAME, with no folder and no extension.')
        print('     requested: %r' % name.decode('latin-1'))
    else:
        print('  -> it did not even try to load: draw_badge was not called.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
