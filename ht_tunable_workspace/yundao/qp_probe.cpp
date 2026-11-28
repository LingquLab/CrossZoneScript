/* Copyright (c) 2026 TileXR Project.
 * Exercise a shared-QP layout, fullmesh registration and teardown on eight cards.
 */
#include <acl/acl.h>
#include <tilexr/comm/api.h>

#include <cstdlib>
#include <iostream>

int main(int argc, char **argv)
{
    if (argc != 5) {
        return 2;
    }
    const int rank = std::atoi(argv[1]);
    const unsigned expectedSix = std::atoi(argv[2]);
    const unsigned expectedTwo = std::atoi(argv[3]);
    const unsigned expectedDirect = std::atoi(argv[4]);
    TileXRCommPtr comm = nullptr;
    void *memory = nullptr;
    TileXRUDMAMemHandle handle = 0;
    unsigned six = 0, two = 0, base = 0, direct = 0, count = 0;
    TileXR::TileXRUDMAFullmeshHostView view {};
    const int acl = aclInit(nullptr);
    const int device = acl == 0 ? aclrtSetDevice(rank) : -1;
    const int init = device == 0 ? TileXRCommInitRankWithSharedQpDomain(0, 8, rank, &comm) : -1;
    const int layout = init == 0 ? TileXRUDMAGetSharedQpLayout(comm, &six, &two, &base, &direct) : -1;
    const int qp = init == 0 ? TileXRUDMAGetQpCount(comm, &count) : -1;
    constexpr size_t bytes = 65536;
    const int alloc = init == 0 ? aclrtMalloc(&memory, bytes, ACL_MEM_MALLOC_NORMAL_ONLY) : -1;
    const int reg = init == 0 ? TileXRUDMARegister(comm, static_cast<GM_ADDR>(memory),
                                                   alloc == 0 ? bytes : 0, &handle) : -1;
    const int fullmesh = reg == 0 ? TileXRUDMAFullmeshQuery(comm, &view) : -1;
    const bool mask = fullmesh == 0 && TileXR::UDMAFullmeshHostViewValid(
        view, rank, 8, view.registrationGeneration);
    const int unreg = reg == 0 ? TileXRUDMAUnregister(comm, handle) : -1;
    const int destroy = init == 0 ? TileXRCommDestroy(comm) : -1;
    if (alloc == 0) {
        aclrtFree(memory);
    }
    if (device == 0) {
        aclrtResetDevice(rank);
    }
    if (acl == 0) {
        aclFinalize();
    }
    std::cout << "QP_RESULT " << acl << ' ' << device << ' ' << init << ' ' << layout
              << ' ' << qp << ' ' << reg << ' ' << fullmesh << ' ' << unreg << ' ' << destroy
              << ' ' << six << ' ' << two << ' ' << base << ' ' << direct << ' ' << count
              << ' ' << view.connectedCount << ' ' << view.validPeerMask << '\n';
    return acl != 0 || device != 0 || init != 0 || layout != 0 || qp != 0 || reg != 0 ||
        !mask || unreg != 0 || destroy != 0 || six != expectedSix || two != expectedTwo ||
        base != six + two || direct != expectedDirect || count != base + direct;
}
