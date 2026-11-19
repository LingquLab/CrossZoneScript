#define _GNU_SOURCE
#include <execinfo.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <ucontext.h>
#include <unistd.h>

static void write_message(const char *message, size_t length)
{
    while (length != 0) {
        ssize_t written = write(STDERR_FILENO, message, length);
        if (written <= 0)
            return;
        message += written;
        length -= (size_t)written;
    }
}

static void record_crash(int signo, siginfo_t *info, void *context)
{
    void *frames[48];
    static const char title[] = "[sst-crash] received fatal signal\n";
    write_message(title, sizeof(title) - 1);

    const ucontext_t *machine = context;
#if defined(__aarch64__)
    void *pc = (void *)machine->uc_mcontext.pc;
#elif defined(__x86_64__)
    void *pc = (void *)machine->uc_mcontext.gregs[REG_RIP];
#else
    (void)machine;
    void *pc = NULL;
#endif
    char fault[128];
    int length = snprintf(fault, sizeof(fault), "[sst-crash] signal=%d code=%d address=%p pc=%p\n",
                          signo, info->si_code, info->si_addr, pc);
    if (length > 0 && (size_t)length < sizeof(fault))
        write_message(fault, (size_t)length);
    if (pc)
        backtrace_symbols_fd(&pc, 1, STDERR_FILENO);
    int count = backtrace(frames, (int)(sizeof(frames) / sizeof(frames[0])));
    backtrace_symbols_fd(frames, count, STDERR_FILENO);
    (void)kill(getpid(), signo);
}

__attribute__((constructor))
static void attach_to_sst(void)
{
    char executable[1024];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0)
        return;
    executable[length] = '\0';
    const char *name = strrchr(executable, '/');
    name = name ? name + 1 : executable;
    if (strcmp(name, "sst") != 0 && strcmp(name, "sstsim.x") != 0)
        return;

    void *warmup[1];
    (void)backtrace(warmup, 1);
    struct sigaction action = {0};
    action.sa_sigaction = record_crash;
    sigemptyset(&action.sa_mask);
    action.sa_flags = SA_SIGINFO | SA_RESETHAND;
    (void)sigaction(SIGSEGV, &action, NULL);
}
