# prop_virtual_guard_test

Diagnostic guard for the virtual dispatch at `0x0038CC98`, in the first render
of the city props. Calls whose target is aligned and inside the game's code
image go on to the original method without changing arguments or RA. A null,
misaligned or out-of-`0x00100000..0x006FFFFF` target is logged. Version 5
keeps the v4 rule: it does not restart a list from inside it. The first save 07
showed that the valid FE0 chain has eight nodes and ends at the old pointer
`0x009F39CC`; re-reading `array[FE0]` at that point creates the cycle
head -> tail -> head. Any invalid virtual target is now logged in `skipped`
and ends only that routine at `0x0038CE80`. The next original invocation
reloads `array[index]` naturally. The `slot now` field still shows the
published head for diagnosis; `refreshed` should stay zero in v5.

Save 06 also revealed a second transient structure on the first frame:
`0x0025B250` received `a0=0x01BED340` and interpreted the pointer `0x019DF2DC`
as a count, walking up to `0x1Fxxxxxx`. v3 also guards the call at
`0x0025C02C`: an invalid pointer/view or any of the four counts above the
capacity of the matching bank is logged and ignored on that frame. The pairs
are `+0x64 <= 0x190`, `+0x6C <= 0x64`, `+0x74 <= 0xBB8` and `+0x7C <= 0x64`;
the v4 report also shows `list off / limit`. This is still diagnostic, not a
permanent patch.

Save 07 overwritten with v4 got past those two points (`skipped=5`, one sky
list blocked, viewport active), but ended in the BIOS undefined handler. The
recovered frame points to the `jalr` of `rmcModel::Draw` at `0x002A99E4`:
model `0x00BC3EE0` (`a_prop_stlt_02x`), group `0x009D6B90`, geometry 0. The
model asks for shader `0x50` and the lookup returns `0x4E3AFFFF`, with no
vtable/method. v5 guards both branches at `0x002A99E4/0x002A9A48`: a valid
method goes on as a tail call; an invalid one is logged in `bad shader calls`
and only that geometry stops drawing.

Save 08 with v5 hung even before loading, without reaching any of the three
earlier guards. VIF1 was active at `0x005268A0`, waiting for room in the ring,
with PATH2 open. The preserved chain proves the origin: the tag at
`0x00106F40` was `REF6 + DIRECT6` to `0x007100BF`, which holds the data and
strings `ga_plate_2/ga_font_2`, not a GIF packet. The first false qword has
`NLOOP=0/EOP=0`, so PATH2 never ends.

In the earlier saves the state cloned at `0x006F4880` was always the aligned
object `0x0071AC80`; only in save 08 did it become `0x007100AF`, together with
`0x006F487C=0xEBE00101`. The stock routine at `0x00528A08` added 16 and
published exactly the bad tag address. v6 replaces the two loads at
`0x00528A08/0x00528A0C` with a validation of the object: alignment, EE RAM
range, DMA header `0x60000006`, VIF `DIRECT6` and a GIFtag with EOP. A valid
object reproduces the two original loads. An invalid object is logged in
`bad render states` and only that first cloned state is skipped; execution
continues at `0x00528A98` and then applies the independent base state of
`0x006F4864`. The guard does not reset VIF/GIF and does not change the global
pointer.

v7 answers the city fork's new evidence: the `s5` group can keep a valid
vtable but have at `+4` a slot table belonging to a prop (`slot[4]=0x0062CB88`,
the `mcPropBase` vtable). Inside `rmcModel::Draw`, `s5` is just the `a1` it
received. When one of the two shader dispatches is refused, v7 reads the frame
`rmcModel::Draw` already created and logs the real caller, the caller's
`s0-s6`, the live group header and `slot[4]`. For the props path
(`RA=0x003B1F20`), the saved `s2` is exactly the object that produced `a1`: the
reader shows `owner`, selector `owner+4`, base `owner+8` and the rebuilt group.
That collection only happens after the method is judged invalid; the valid path
stays the same tail call as in the earlier versions.

v8 intercepts the two `jal 0x002B3600` that materialise the props packages'
`rmcShaderGroup` (`0x00394510` and `0x00394694`). The handler calls the stock
routine without changing arguments and snapshots the Atlanta group with the
serialised count pair `0x005C/0x005C` right before and after the call. The
report shows vtable, slots, counts, list and `slot[0]/slot[4]` at both ends. If
`after` already points to `mcPropBase` objects, the corruption is born inside
the PageIn/callback; if `after` is right and the first-error snapshot is bad,
the group was overwritten later. v8 does not use `mc3_log.h`: that logger's
global header at `0x0061CFF0` coincides with the `MC3W` report of the active
`proper_widescreen`.

The first v8 test went black after loading with `game frames=1`. The state
showed `version=8`, two hooks installed, one PageIn called, but
`installed/patched=0/0`. That was not a result about the group: modules that
declare `MC3_HOOK` are driven by the hooks and do not get the `defer_once`
entry from the dispatcher. v9 calls the guards' initialisation once, on the
first PageIn, before calling the original. That way the load snapshot stays
early and the four earlier guards are already active when the first frame
starts.

The following saves showed that v8/v9's two call sites did not cover the active
group: both logged a call, but `target 0x5C=0`. The C++ fork decompiled
`0x002B3600` and corrected its role: it is the constructor
`rmcShaderGroup::rmcShaderGroup(datResource&)`, it adds `loader+4` to
`group+4`, relocates the list at `group+0x0C` and calls the slots' Place.
There are eight static `jal` to it, not two.

v10 intercepts all eight (`0x0025928C`, `0x002592C4`, `0x002AFAD8`,
`0x00394364`, `0x00394510`, `0x00394694`, `0x003B5140`, `0x00597FD8`) and the
context's generic fixup at `0x0024AF00`. The PVGD summary keeps the last
before/after of the serialised group with `counts=0x005C005C`; the MC3K v2
logger, owner `PVGD`, keeps the whole sequence with site, group, delta, slots,
counts, list and slot 0/4. The original PCK expects `group+4=0x00937DB0`, but
saves 01/02 use `0x00A288D0`: the extra is `0x000F0B20`, not a second addition
of the full delta `0xFA130600`. So the simple double-relocation hypothesis is
not closed yet; the v10 sequence will say which call site produces the
divergence.

In the v10 saves, the eight hooks saw 96 constructors, but none of them got the
group while its +8 word was still `0x005C005C`. The target context did not go
through `0x0024AF00` either. The live image proved that the identification
fields were still right and that the resource uses one of the two specialised
fixups at `0x003944F0/0x00394654`. Those paths call `0x00397330` and only then
do a `jal 0x002B3600`; so the fixup can build the group indirectly before the
second static constructor.

v11 covers the three fixups. Before the original, it computes the predicted
group address as `context+0xB8 + loader+4`, so the static constructor can be
recognised even after +8 changes format. Afterwards it logs the already
processed group and then the before/after of the `jal 0x002B3600`. PVGD is now
432 bytes and the ring stays owner `PVGD`.

The v11 state showed 188 direct fixups and zero hits of the Atlanta context. The
context exists at the end of the load, so its call to `0x00397330` is through
`jalr`. v12 detours the function's entry instead of trying to enumerate
callers. The patch requires the two stock words `0x27BDFFC0/0xFFB20010`,
preserves a0/a1/RA, logs the original RA, reproduces the two removed
instructions and resumes at `0x00397338`. The snapshot of the predicted group is
now really pre-fixup. The report is 452 bytes and also shows whether the detour
was installed.

The v12 state corrected that inference: the entry detour worked and counted 188
calls, exactly the same 188 seen by the direct call sites, but the target stayed
at zero. The final RAM confirmed the context and its fields are right. The
reason is in the function's own order: `0x00397390` calls the `datResource`
virtual reader with destination `context+0xB4`; only after it returns, at
`0x00397398`, do the manager, the raw group and the counts exist. So filtering
`+BC/+C0/+C4` at the entry was always too early, not evidence of an indirect
caller or of a module installed late.

v13 keeps the entry counter and adds a detour at `0x00397398`, before
`0x003973AC` adds `loader+4` to `context+0xB8`. It requires the stock words
`0x8E4300B8/0x50600005`, calls the capture with `s2=context` and `s3=loader`,
preserves `v0/ra` and reproduces both paths of the `beqzl`: on the zero path it
also executes the delay slot `lw v1,0xB0(s2)` before resuming at `0x003973B4`;
on the non-zero path it resumes at `0x003973A4`. The report is 472 bytes.

The v13 state showed both detours installed and 188 calls in each phase, but
again zero targets. That corrected the object's identity: `0x00397330` is the
constructor of each `mcPropType`; the corrupted group belongs to the outer
`mcPropManagerData`. The live object with vtable `0x0062DC90` starts at
`0x00931100`; in it `+0x08=manager`, `+0x0C=group` and
`+0x10/+0x14/+0x18=52B/7F/464`. The address `0x00931054` used before was just
that root minus `0xAC`, a coincidence caused by the optional `mcPropType` field
used by the getter.

The right route is `mcPropManagerData::Place` at `0x00394290`: it relocates
`object+0x0C` and calls the group constructor directly at `0x00394364`. v14
recognises that call site regardless of `group+8`, keeping the real
before/after in PVGD and in the ring. The three direct `mcPropType` hooks and
the two manual detours were retired; only the eight constructor hooks remain,
reducing the instrumentation's interference. The report stays at 472 bytes.

The ownership rule logged by the MC3 Textures and Shaders fork matters for
reading the result: a partially compiled graph or a texture outside the
owner/PageIn can jump into CD padding, and an object cannot get Place twice.
That does not authorise copying slots or local indices from the vehicle
experiment into the city; it only defines the kind of invariant that has to be
proven.

```ini
[mods]
core.mod = 1
undefined_syscall_trace.mod = 0
prop_virtual_guard_test.mod = defer_once
```

After the test, save a state and run (`mc3_inject.py` is in the mc3boot repo):

```text
python mc3_inject.py state "file.p2s"
```

The `prop virtual guard` section reports how many invalid targets there were
and how many objects were updated or skipped, `bad packet lists` shows the sky
work-list, `bad shader calls` shows object, vtable, method, model, group,
geometry and call site, and `bad render states` shows the refused state object
and the reason. From v7 on, a shader hit adds `Draw caller/parent`,
`s5 writer`, the preserved registers, `prop owner/select`,
`source base/result` and `group vtbl/slots/slot[4]`. In v14, the decisive
result is `all ctor calls` with `manager group site>0`, followed by
`ctor site/group`, `loader/delta`, `slots/count/list before/after`,
`slot0/4 before/after` and `log 'PVGD'`. This is still diagnostic: it must not
stay on in normal play.

## v15 and v16 - from enumerated call sites to an entry detour

v15 added one ring line per direct call (`ctor all site/group/slots/count`),
but was still limited to the eight `jal` that could be enumerated. Since the
v14 state proved that `0x00938710` reaches the manager site with `group+4` and
`group+8` already relocated, the earlier writer may be in a `jalr` or a
callback, out of reach of those hooks.

v16 removes the eight `PVGD_CTOR_HOOK` and installs a detour at the entry of
`rmcShaderGroup::rmcShaderGroup(datResource&)`, at `0x002B3600`. The install
requires the stock words `0x27BDFFF0`/`0x3C020062`; the stub keeps
`a0/a1/ra`, passes the original `ra` to the capture, reproduces the two words
and resumes at `0x002B3608`. The capture derives the call site as `ra - 8`. A
single call-site hook remains, at `0x0025928C`, whose only role is to run
`mod_main()` before forwarding the call, making sure the detour exists before
any other path.

The bootstrap call is indirect (`MC3_CALL2`), so the first ring entry shows up
with a `site` inside the module itself, not `0x0025928C`. The required words
were checked in an EE dump: `0x002B3600/04 = 27BDFFF0/3C020062`,
`0x002B3608 = FFB00000` and `0x0025928C = 0C0ACD80` (`jal 0x002B3600`).

Since the detour only runs at the entry, the `constructor_after_*` fields stay
zeroed in v16: the before/after of one object comes from two ring entries. The
report stays at 472 bytes and `mc3_inject.py` now prints
`group ctor detour = N (orig 27BDFFF0 / 3C020062)`.

The v16 test should show `version=16`, `group ctor detour = 1`,
`shader patched=3`, `ctor calls` above v14's 96 direct ones and, in the ring,
which site touched `0x00938710` before `0x00394364`.
