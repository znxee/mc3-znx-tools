SECTIONS
{
  . = 0x0;
  .text : SUBALIGN(4) { *(.text.start) *(.text .text.*) }
  .mc3hooks : { PROVIDE(__mc3hooks_start = .); KEEP(*(.mc3hooks)) PROVIDE(__mc3hooks_end = .); }
  .rodata : { *(.rodata .rodata.*) }
  .data : { *(.data .data.*) }
  .bss : { *(.bss .bss.*) *(COMMON) }
  /DISCARD/ : { *(.comment) *(.pdr) *(.reginfo) *(.MIPS.abiflags) *(.gnu.attributes) }
}
