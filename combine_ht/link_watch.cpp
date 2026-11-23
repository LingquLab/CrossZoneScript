/* Copyright (c) 2026 TileXR Project.
 * Captures the SST name before the pinned BA library sends through a null link.
 * Loaded only by the diagnostic runner; the baseline library is unchanged.
 */
#include <sst/core/sst_config.h>
#include <sst/core/component.h>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <unistd.h>

extern "C" void WatchLink(void *object, void *event)
    asm("_ZN3SST7Davinci7BaLayer17sendReqToBusEventEPNS0_15BusPayloadEventE");

extern "C" void WatchLink(void *object, void *event)
{
    // Verified by the exact library hash and native field-access disassembly.
    void *link = *reinterpret_cast<void **>(static_cast<char *>(object) + 4120);
    if (!link) {
        const char *path = std::getenv("TILEXR_NULL_LINK_REPORT");
        if (path) {
            FILE *file = std::fopen(path, "wx");
            if (file) {
                auto *component = static_cast<SST::BaseComponent *>(object);
                std::fprintf(file, "%s\n", component->getName().c_str());
                std::fclose(file);
            }
        }
        _exit(86);
    }
    using Original = void (*)(void *, void *);
    static Original original = reinterpret_cast<Original>(dlsym(
        RTLD_NEXT, "_ZN3SST7Davinci7BaLayer17sendReqToBusEventEPNS0_15BusPayloadEventE"));
    if (!original) _exit(87);
    original(object, event);
}
