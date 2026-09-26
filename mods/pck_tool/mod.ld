/* Linker script for the RELOCATABLE build (.mod).

   Base 0: the module carries its relocations, so where it was linked does not
   matter - and linking at the real cave address would drag in ~52 KB of page
   alignment padding ahead of the code, which would then be copied and stored
   for nothing.

   The layout still matches the loaded one: signature at +0, entry at +4. */
/* No ENTRY: a hook-only module has no entry point. The loader uses the entry
   field only for modules that take the per-frame callback instead of declaring
   hooks, and ld is told `--entry=0` so it does not warn about a symbol that is
   not supposed to exist. */
SECTIONS
{
  . = 0x0;
  /* .text.start first: the dispatcher calls the module at its entry, which
     the format records as an offset - keeping mod_main at the front makes
     that offset 0 and the layout obvious. */
  .text  : SUBALIGN(4) { *(.text.start) *(.text .text.*) }
  /* The hook table, kept as one contiguous run so the build tool can hand the
     loader an offset and a count. KEEP because nothing references it. */
  .mc3hooks : { PROVIDE(__mc3hooks_start = .); KEEP(*(.mc3hooks)) PROVIDE(__mc3hooks_end = .); }
  .rodata : { *(.rodata .rodata.*) }
  .data   : { *(.data .data.*) }
  .bss    : { *(.bss .bss.*) *(COMMON) }
  /DISCARD/ : { *(.comment) *(.pdr) *(.reginfo) *(.MIPS.abiflags) *(.gnu.attributes) }
}
