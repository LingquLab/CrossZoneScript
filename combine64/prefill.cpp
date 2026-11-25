/* Copyright (c) 2026 TileXR Project.
 * Serialize candidate HT arguments and Prefill from fixture-owned route tables. */
#include "combine_ht_prefill.h"
#include <fstream>
#include <string>
#include <stdexcept>

template<class T> void Read(const std::string &name, T *data, size_t count) {
    std::ifstream f(name, std::ios::binary);
    f.read(reinterpret_cast<char *>(data), count * sizeof(T));
    if (!f) throw std::runtime_error("fixture_read");
}
template<class T> void Write(const std::string &name, const T *data, size_t count) {
    std::ofstream f(name, std::ios::binary);
    f.write(reinterpret_cast<const char *>(data), count * sizeof(T));
    if (!f) throw std::runtime_error("fixture_write");
}
int main(int argc, char **argv) {
    if (argc != 2 && argc != 3) return 2;
    using namespace TileXRCombineHT;
    std::string p(argv[1]);
    KernelArgs a{};
    Read(p+".config", &a.config, 1);
    if (TileXRMoonEpCombineHTGetLayout(&a.config,&a.layout)) return 3;
    std::vector<int64_t> backward(uint64_t(8)*a.layout.backward_capacity*3);
    std::array<int32_t,8> counts{};
    std::vector<int32_t> ranges(a.layout.backward_range_elements);
    Read(p+".backward",backward.data(),backward.size());
    Read(p+".backward_counts",counts.data(),counts.size());
    if (TileXRMoonEpCombineHTBuildBackwardRanges(&a.config,backward.data(),counts.data(),ranges.data())) return 4;
    Write(p+".backward_ranges",ranges.data(),ranges.size());
    const uint64_t stride=0x2000000000ULL,base=a.config.rank*stride;
    a.workspace=reinterpret_cast<uint8_t *>(base+0x200000000ULL);
    const uint64_t inputOffset=argc==3?std::stoull(argv[2]):
        (uint64_t(a.config.input_rows)*a.config.hidden*4 > 0x1000000 ? 0x440000000ULL : 0x404000000ULL);
    a.input=reinterpret_cast<uint8_t *>(base+inputOffset);
    a.forward=reinterpret_cast<uint8_t *>(base+0x400000000ULL);
    a.forward_counts=reinterpret_cast<uint8_t *>(base+0x401000000ULL);
    a.ranges=reinterpret_cast<uint8_t *>(base+0x402000000ULL);
    a.masks=reinterpret_cast<uint8_t *>(base+0x406000000ULL);
    a.backward=reinterpret_cast<uint8_t *>(base+0x407000000ULL);
    a.backward_counts=reinterpret_cast<uint8_t *>(base+0x408000000ULL);
    a.backward_ranges=reinterpret_cast<uint8_t *>(base+0x409000000ULL);
    a.status=reinterpret_cast<uint8_t *>(base+0x403000000ULL);
    a.comm=reinterpret_cast<uint8_t *>(base+0x2ff000000ULL);
    a.fullmesh_info=reinterpret_cast<uint8_t *>(base+0x300040000ULL);
    a.registry=reinterpret_cast<uint8_t *>(base+0x300060000ULL);
    a.prefill=reinterpret_cast<uint8_t *>(base+0x405000000ULL);
    a.magic=2026;
    a.comm_peer_begin=a.config.rank-a.config.rank%8;
    std::array<PrefillEndpoint,8> endpoints{};
    std::array<std::vector<int64_t>,8> rows;
    std::ifstream memory(p+".native_mem_info",std::ios::binary);
    for(uint32_t g=0;g<8;++g) {
        const uint32_t peer=a.comm_peer_begin+g;
        auto &e=endpoints[g];
        memory.seekg(uint64_t(peer*32+g)*sizeof(e.memory));
        memory.read(reinterpret_cast<char *>(&e.memory),sizeof(e.memory));
        if(!memory) return 5;
        e.base=peer*stride+0x200000000ULL;
        e.eid[0]=peer*stride;
        auto begin=backward.begin()+uint64_t(g)*a.layout.backward_capacity*3;
        rows[g].assign(begin,begin+uint64_t(counts[g])*3);
    }
    std::vector<uint8_t> image;
    if(BuildPrefillImage(a.config,uint64_t(a.input),uint64_t(a.workspace),rows,counts.data(),ranges.data(),endpoints,image)) return 6;
    Write(p+".prefill",image.data(),image.size());
    Write(p+".args",&a,1);
    Write(p+".layout",&a.layout,1);
    return 0;
}
