// Build simulator templates with the exact candidate's pure Host serializer.
#include "combine_ht_prefill.h"
#include <cstring>
extern "C" int Build(const TileXRCombineHTConfig *c, uint64_t input, uint64_t workspace,
 const int64_t *backward, const int32_t *counts, const int32_t *ranges,
 const TileXRCombineHT::PrefillEndpoint *endpoints, uint8_t *out, uint64_t capacity, uint64_t *written) {
 using namespace TileXRCombineHT;
 TileXRCombineHTLayout l{};
 int result = TileXRMoonEpCombineHTGetLayout(c, &l);
 if (result) return result;
 std::array<std::vector<int64_t>, kLocalRanks> rows;
 std::array<PrefillEndpoint, kLocalRanks> eps;
 for (uint32_t g=0; g<kLocalRanks; ++g) {
   rows[g].assign(backward+uint64_t(g)*l.backward_capacity*kBackwardFields,
     backward+uint64_t(g)*l.backward_capacity*kBackwardFields+uint64_t(counts[g])*kBackwardFields);
   eps[g] = endpoints[g];
 }
 std::vector<uint8_t> image;
 result=BuildPrefillImage(*c,input,workspace,rows,counts,ranges,eps,image);
 *written=image.size();
 if(result) return result;
 if(image.size()>capacity) return -3;
 std::memcpy(out,image.data(),image.size());
 return 0;
}
extern "C" uint64_t EndpointBytes() { return sizeof(TileXRCombineHT::PrefillEndpoint); }
