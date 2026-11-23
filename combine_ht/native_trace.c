/* Copyright (c) 2026 TileXR Project.
 * Runs one diagnostic child and records registers and ELF mappings when any
 * traced thread stops on SIGSEGV. The Python runner owns the timeout and logs.
 */
#define _GNU_SOURCE
#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ptrace.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <unistd.h>
#if defined(__aarch64__)
#include <asm/ptrace.h>
#endif

int main(int argc, char **argv)
{
    if (argc < 3) return 2;
    pid_t child = fork();
    if (child == -1) return 3;
    if (child == 0) {
        (void)setpgid(0, 0);
        if (ptrace(PTRACE_TRACEME, 0, NULL, NULL) == -1) _exit(77);
        (void)raise(SIGSTOP);
        execvp(argv[2], argv + 2);
        _exit(78);
    }
    int status;
    if (waitpid(child, &status, 0) != child) return 4;
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    long options = PTRACE_O_TRACECLONE | PTRACE_O_TRACEFORK | PTRACE_O_TRACEVFORK |
                   PTRACE_O_TRACEEXEC | PTRACE_O_EXITKILL;
    if (ptrace(PTRACE_SETOPTIONS, child, NULL, (void *)options) == -1) {
        (void)kill(-child, SIGKILL);
        return 77;
    }
    (void)ptrace(PTRACE_CONT, child, NULL, NULL);
    for (;;) {
        pid_t thread = waitpid(-1, &status, __WALL);
        if (thread < 0) {
            if (errno == EINTR) continue;
            break;
        }
        if (WIFEXITED(status) || WIFSIGNALED(status)) {
            if (thread == child) return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
            continue;
        }
        if (!WIFSTOPPED(status)) continue;
        int number = WSTOPSIG(status);
        if (number == SIGSEGV || number == SIGBUS || number == SIGABRT) {
            FILE *out = fopen(argv[1], "wx");
            if (out) {
#if defined(__aarch64__)
                struct user_pt_regs registers = {0};
                struct iovec vector = {&registers, sizeof(registers)};
                int regs_ok = ptrace(PTRACE_GETREGSET, thread, (void *)NT_PRSTATUS, &vector) == 0;
                siginfo_t info = {0};
                int info_ok = ptrace(PTRACE_GETSIGINFO, thread, NULL, &info) == 0;
                fprintf(out, "%016llx\n%016llx\n%016llx\n", regs_ok ? registers.pc : 0,
                        regs_ok ? registers.regs[30] : 0, info_ok ? (unsigned long long)info.si_addr : 0);
                fprintf(stderr, "trace_fault signal=%d regs_ok=%d info_ok=%d sp=%llx\n",
                        number, regs_ok, info_ok, registers.sp);
                char register_path[4096];
                (void)snprintf(register_path, sizeof(register_path), "%s.regs", argv[1]);
                FILE *regfile = fopen(register_path, "wx");
                if (regfile) {
                    fprintf(regfile, "{\"sp\":\"0x%llx\"", registers.sp);
                    for (int i = 0; i < 31; ++i) fprintf(regfile, ",\"x%d\":\"0x%llx\"", i, registers.regs[i]);
                    fprintf(regfile, "}\n");
                    (void)fclose(regfile);
                }
#endif
                char name[80];
                (void)snprintf(name, sizeof(name), "/proc/%d/maps", thread);
                FILE *maps = fopen(name, "r");
                if (maps) {
                    char buffer[4096];
                    size_t bytes;
                    while ((bytes = fread(buffer, 1, sizeof(buffer), maps))) (void)fwrite(buffer, 1, bytes, out);
                    (void)fclose(maps);
                }
                (void)fclose(out);
            }
            (void)kill(-child, SIGKILL);
            (void)ptrace(PTRACE_KILL, thread, NULL, NULL);
            return 128 + number;
        }
        int deliver = (number == SIGTRAP || number == SIGSTOP) ? 0 : number;
        (void)ptrace(PTRACE_CONT, thread, NULL, (void *)(long)deliver);
    }
    return 0;
}
