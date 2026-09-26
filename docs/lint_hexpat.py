"""lint_hexpat.py - check the .hexpat files before shipping them

Several checks, and every one of them has already caught a real mistake:

  * every PLACED type (`Type name @ ...`) and every field type has to exist as
    a struct or enum in the same file. That is how it showed up that I had
    placed a TexturePS2 in mc3_city.hexpat without defining the struct there -
    it only existed in the RAM pattern.
  * every `[[format("x")]]` needs an `fn x` in the file.
  * there can be NO attribute on a declaration with `@`. ImHex refuses
    `u32 x [[format("vt")]] @ end;` with "Expected ';' ... got Operator (@)".
    To format a placed value, wrap it in a one-field struct.
  * a field name cannot be a reserved word. `ref` and `in`, `out`, `str`,
    `parent`, `match`, `be`, `le` are ImHex words, and the error that comes out
    ("got Keyword (ref)") points at the column of the TYPE, not of the name.
  * an identifier used in `ptr(X)` has to exist as a declared field. This
    catches renaming a field and forgetting to fix what uses it - a mistake
    ImHex only reports when it runs.
  * a struct that is defined but never shows up in the tree is useless: whoever
    opens the file only sees the pointer.

This is a regex lint and does not see scope, expressions or missing
declarations: always run the real thing too, `imhex --pl run FILE PATTERN`.

    python docs/lint_hexpat.py docs/*.hexpat
"""
import io
import re
import sys

# ImHex words that cannot be used as a field or variable name.
RESERVED = ('ref', 'in', 'out', 'str', 'auto', 'parent', 'this', 'match',
              'const', 'be', 'le', 'null', 'try', 'catch', 'break', 'continue',
              'signed', 'unsigned', 'while', 'for', 'if', 'else', 'struct',
              'union', 'using', 'enum', 'bitfield', 'fn', 'return', 'import',
              'namespace', 'addressof', 'sizeof', 'padding')

BASICS = {'u8', 'u16', 'u32', 'u64', 's8', 's16', 's32', 'float', 'char',
           'padding', 'bool'}


def check(fname):
    s = io.open(fname, encoding='utf-8').read()
    structs = (set(re.findall(r'^struct (\w+)', s, re.M))
               | set(re.findall(r'^enum (\w+)', s, re.M)) | BASICS)
    fns = set(re.findall(r'^fn (\w+)', s, re.M))
    bad = []

    for m in re.finditer(r'^\s*(\w+)\s+\w+(\[.*\])?\s*(\[\[.*?\]\])?\s*@', s, re.M):
        if m.group(1) not in structs:
            bad.append(('placed type', m.group(1)))

    for m in re.finditer(r'format\("(\w+)"\)', s):
        if m.group(1) not in fns:
            bad.append(('formatter', m.group(1)))

    for m in re.finditer(r'^\s*\w+\s+\w+(?:\[.*\])?\s*\[\[.*?\]\]\s*@', s, re.M):
        bad.append(('attribute on a placement with @', m.group(0).strip()))

    for m in re.finditer(r'^\s*\w+\s+(%s)\s*(\[|;|@)' % '|'.join(RESERVED), s, re.M):
        bad.append(('reserved name', m.group(1)))

    declared_fields = set(re.findall(
        r'^\s*\w+\s+(\w+)\s*(?:\[[^\]]*\])?\s*(?:\[\[.*?\]\])?\s*[;@]', s, re.M))
    declared_fields |= set(re.findall(
        r'^\s*(?:bool|u32|u16|u8|float)\s+(\w+)\s*=', s, re.M))
    for m in re.finditer(r'(?<![\w])ptr\(\s*([A-Za-z_]\w*)\s*[\).]', s):
        if m.group(1) not in declared_fields:
            bad.append(('ptr() of a missing field', m.group(1)))

    for m in re.finditer(r'^\s*(\w+)\s+\w+(\[[^\]]*\])?\s*(\[\[.*?\]\])?;', s, re.M):
        if m.group(1) not in structs and m.group(1) not in ('return', 'if', 'else', 'fn', 'import'):
            bad.append(('field type', m.group(1)))

    # ImHex requires DECLARE BEFORE USE: a struct can only be used below its
    # own definition ("Type X has not been declared yet"). Since the file is
    # large and structs get added where the topic calls for them, it is easy to
    # put a new one after whatever uses it.
    lines = s.split('\n')
    defined = {}
    for n, ln in enumerate(lines):
        m = re.match(r'^struct (\w+)', ln)
        if m:
            defined.setdefault(m.group(1), n)
    for n, ln in enumerate(lines):
        m = re.match(r'^\s*(\w+)\s+\w+(?:\[[^\]]*\])?\s*(?:\[\[.*?\]\])?\s*[;@]', ln)
        if m and m.group(1) in defined and n < defined[m.group(1)]:
            bad.append(('type used before it is declared', m.group(1)))

    placed = set(re.findall(r'^\s*(\w+)\s+\w+(?:\[.*\])?\s*(?:\[\[.*?\]\])?\s*@', s, re.M))
    fields = set(re.findall(r'^\s*(\w+)\s+\w+(?:\[\d+\])?\s*(?:\[\[.*?\]\])?;', s, re.M))
    defs = set(re.findall(r'^struct (\w+)', s, re.M))
    for o in sorted(defs - ((placed | fields) & defs)):
        bad.append(('orphan struct, never shows up', o))

    seen_one = set()
    return [x for x in bad if not (x in seen_one or seen_one.add(x))]


def main():
    for fname in sys.argv[1:]:
        bad = check(fname)
        print('%-30s %s' % (fname.replace('\\', '/').split('/')[-1],
                            'ok' if not bad else 'PROBLEMS: %s' % bad))


if __name__ == '__main__':
    main()
