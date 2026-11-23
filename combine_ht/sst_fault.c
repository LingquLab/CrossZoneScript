/*
 * Copyright (c) 2026 TileXR Project.
 * Records register addresses and mapped ELF files when the diagnostic SST
 * subprocess receives SIGSEGV. Append mode lets an exec of the SST launcher
 * reopen the task-local evidence file. Interception retains this handler when
 * SST registers its crash handler. Only diagnostic runs preload this library.
 */
#define _GNU_SOURCE

#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <ucontext.h>
#include <unistd.h>

static int fault_output = -1;
static int lifecycle_output = -1;
static int (*real_sigaction)(int, const struct sigaction *, struct sigaction *);

static void write_hex(uintptr_t value)
{
    static const char digits[] = "0123456789abcdef";
    char line[2 * sizeof(value) + 1];
    for (size_t remaining = 2 * sizeof(value); remaining > 0; --remaining) {
        line[remaining - 1] = digits[value & 15U];
        value >>= 4;
    }
    line[sizeof(line) - 1] = '\n';
    (void)write(fault_output, line, sizeof(line));
}

static void record_fault(int signal_number, siginfo_t *info, void *context)
{
    const ucontext_t *machine = context;
#if defined(__aarch64__)
    write_hex(machine->uc_mcontext.pc);
    write_hex(machine->uc_mcontext.regs[30]);
#elif defined(__x86_64__)
    write_hex(machine->uc_mcontext.gregs[REG_RIP]);
    write_hex(0);
#endif
    write_hex((uintptr_t)info->si_addr);
    int maps = open("/proc/self/maps", O_RDONLY);
    if (maps >= 0) {
        char block[4096];
        ssize_t length;
        while ((length = read(maps, block, sizeof(block))) > 0) {
            if (write(fault_output, block, (size_t)length) != length) break;
        }
        (void)close(maps);
    }
    (void)raise(signal_number);
}

int sigaction(int number, const struct sigaction *action, struct sigaction *old)
{
    if (!real_sigaction) real_sigaction = dlsym(RTLD_NEXT, "sigaction");
    if (number == SIGSEGV && action && fault_output >= 0) {
        if (lifecycle_output >= 0) (void)write(lifecycle_output, "sigsegv-handler-request\n", 24);
        struct sigaction recorder = {0};
        recorder.sa_sigaction = record_fault;
        recorder.sa_flags = SA_SIGINFO | SA_RESETHAND;
        (void)sigemptyset(&recorder.sa_mask);
        return real_sigaction(number, &recorder, old);
    }
    return real_sigaction(number, action, old);
}

__attribute__((constructor)) static void set_fault_handler(void)
{
    const char *output = getenv("TILEXR_SST_FAULT_FILE");
    if (!output) return;
    char lifecycle[4096];
    if (snprintf(lifecycle, sizeof(lifecycle), "%s.lifecycle", output) < (int)sizeof(lifecycle)) {
        lifecycle_output = open(lifecycle, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
        char executable[4096];
        ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable));
        if (lifecycle_output >= 0 && length > 0) {
            (void)write(lifecycle_output, executable, (size_t)length);
            (void)write(lifecycle_output, "\n", 1);
        }
    }
    fault_output = open(output, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (fault_output < 0) return;
    struct sigaction handler = {0};
    handler.sa_sigaction = record_fault;
    handler.sa_flags = SA_SIGINFO | SA_RESETHAND;
    (void)sigemptyset(&handler.sa_mask);
    (void)sigaction(SIGSEGV, &handler, NULL);
}
