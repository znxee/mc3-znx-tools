OUTPUT_ARCH(mips)
ENTRY(mod_main)
SECTIONS
{
  . = 0;
  .text : { *(.text.start) *(.text*) *(.rodata*) }
  .data : { *(.data*) }
  .bss : { *(.bss*) *(COMMON) }
  /DISCARD/ : { *(.comment*) *(.eh_frame*) }
}
