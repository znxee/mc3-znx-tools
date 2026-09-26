// -----------------------------------------------------------------------------
// undefined_syscall_trace - captures the first call to an undefined BIOS
// syscall table entry, then stops deterministically for a savestate.
//
// The report stays inside this relocatable module. mc3_inject.py locates the
// initialized "USCT" magic in EE RAM, so no fixed shared report range is used.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    SYSCALL_TABLE       = 0x80014E80u,
    SYSCALL_TABLE_COUNT = 128u,
    BIOS_UNDEFINED      = 0x80001564u,
    FLUSH_CACHE         = 0x00546C20u,
    TRACE_MAGIC         = 0x54435355u, // bytes "USCT"
};

struct syscall_trace {
    mc3_u32 magic;
    mc3_u32 installed;
    mc3_u32 replaced;
    mc3_u32 hits;
    mc3_u32 raw_v1;
    mc3_u32 syscall_no;
    mc3_u32 epc;
    mc3_u32 cause;
    mc3_u32 bios_ra;
    mc3_u32 bios_sp;
    mc3_u32 instruction;
    mc3_u32 version;
    mc3_u32 game_ra;
    mc3_u32 game_sp;
    mc3_u32 v0;
    mc3_u32 a0;
    mc3_u32 a1;
    mc3_u32 a2;
    mc3_u32 a3;
    mc3_u32 t0;
    mc3_u32 t1;
    mc3_u32 t2;
    mc3_u32 t3;
    mc3_u32 t4;
    mc3_u32 t5;
    mc3_u32 t6;
    mc3_u32 t7;
    mc3_u32 t8;
    mc3_u32 t9;
    mc3_u32 s0;
    mc3_u32 s1;
    mc3_u32 s2;
    mc3_u32 s3;
    mc3_u32 s4;
    mc3_u32 s5;
    mc3_u32 s6;
    mc3_u32 s7;
    mc3_u32 gp;
    mc3_u32 fp;
    mc3_u32 lo;
    mc3_u32 hi;
};

extern "C" {
volatile syscall_trace g_trace = {
    TRACE_MAGIC, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u,
    2u
};
}

extern "C" void undefined_syscall_capture();

// This must not have a compiler-generated prologue.  The v1 C++ callback used
// a2 as a temporary before the savestate could see it, precisely the register
// that identifies the bad city slot.  k0/k1 are already kernel temporaries at
// this point, so the entry below snapshots every useful game register first and
// only then computes the derived fields.  The BIOS syscall frame at bios_sp
// contains the game's RA and SP in its first two words.
__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".globl undefined_syscall_capture\n"
    ".ent undefined_syscall_capture\n"
    "undefined_syscall_capture:\n"
    "lui   $26, %hi(g_trace)\n"
    "addiu $26, $26, %lo(g_trace)\n"
    "sw    $2,  56($26)\n"
    "sw    $4,  60($26)\n"
    "sw    $5,  64($26)\n"
    "sw    $6,  68($26)\n"
    "sw    $7,  72($26)\n"
    "sw    $8,  76($26)\n"
    "sw    $9,  80($26)\n"
    "sw    $10, 84($26)\n"
    "sw    $11, 88($26)\n"
    "sw    $12, 92($26)\n"
    "sw    $13, 96($26)\n"
    "sw    $14,100($26)\n"
    "sw    $15,104($26)\n"
    "sw    $24,108($26)\n"
    "sw    $25,112($26)\n"
    "sw    $16,116($26)\n"
    "sw    $17,120($26)\n"
    "sw    $18,124($26)\n"
    "sw    $19,128($26)\n"
    "sw    $20,132($26)\n"
    "sw    $21,136($26)\n"
    "sw    $22,140($26)\n"
    "sw    $23,144($26)\n"
    "sw    $28,148($26)\n"
    "sw    $30,152($26)\n"
    "mflo  $27\n"
    "sw    $27,156($26)\n"
    "mfhi  $27\n"
    "sw    $27,160($26)\n"
    "sw    $3,  16($26)\n"
    "srl   $27, $3, 2\n"
    "sw    $27, 20($26)\n"
    "mfc0  $27, $14\n"
    "sw    $27, 24($26)\n"
    "sll   $27, $27, 3\n"
    "srl   $27, $27, 3\n"
    "lw    $27, 0($27)\n"
    "sw    $27, 40($26)\n"
    "mfc0  $27, $13\n"
    "sw    $27, 28($26)\n"
    "sw    $31, 32($26)\n"
    "sw    $29, 36($26)\n"
    "lw    $27, 0($29)\n"
    "sw    $27, 48($26)\n"
    "lw    $27, 4($29)\n"
    "sw    $27, 52($26)\n"
    "lw    $27, 12($26)\n"
    "addiu $27, $27, 1\n"
    "sw    $27, 12($26)\n"
    "1: b 1b\n"
    "nop\n"
    ".end undefined_syscall_capture\n"
    ".set at\n"
    ".set reorder\n"
);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    volatile mc3_u32 *const table = (volatile mc3_u32 *)SYSCALL_TABLE;

    g_trace.installed = 1u;
    g_trace.replaced = 0u;
    g_trace.hits = 0u;
    g_trace.raw_v1 = 0u;
    g_trace.syscall_no = 0u;
    g_trace.epc = 0u;
    g_trace.cause = 0u;
    g_trace.bios_ra = 0u;
    g_trace.bios_sp = 0u;
    g_trace.instruction = 0u;

    for (mc3_u32 i = 0; i < (mc3_u32)SYSCALL_TABLE_COUNT; ++i) {
        if (table[i] == (mc3_u32)BIOS_UNDEFINED) {
            table[i] = (mc3_u32)&undefined_syscall_capture;
            g_trace.replaced += 1u;
        }
    }

    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    MC3_CALL1(void, FLUSH_CACHE, int)(2);
}
