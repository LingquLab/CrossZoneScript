// Exercise AIV allocation and peer-proof matching without a device or runtime launch.
#include "ccu/tilexr_ccu_direct_orchestrator.h"

#include <iostream>

using namespace TileXR;

int PreparePeerProof(const TileXRCcuResourceAllocation& allocation,
    TileXRCcuLowerLayerInstallPlan* plan, TileXRCcuLowerLayerPlanBuilderReport* report,
    void* userData)
{
    auto* observed = static_cast<bool*>(userData);
    *observed = true;
    if (allocation.remoteXn.num < allocation.channels.num ||
        allocation.localXn.num < allocation.channels.num ||
        allocation.remoteXn.startId < allocation.localXn.startId + allocation.localXn.num) {
        report->message = "channel XN resources missing or overlapping";
        return TILEXR_ERROR_PARA_CHECK_FAIL;
    }
    for (uint32_t index = 0; index < allocation.channels.num; ++index) {
        TileXRCcuRemoteXnBindingProof proof;
        proof.dieId = allocation.channels.dieId;
        proof.channelId = static_cast<uint16_t>(allocation.channels.startId + index);
        proof.localXn = static_cast<uint16_t>(allocation.localXn.startId + index);
        // Deliberately differ from the local reservation to check reconciliation.
        proof.remoteXn = static_cast<uint16_t>(2400 + index);
        proof.notifyCke = static_cast<uint16_t>(400 + index);
        proof.localWaitCke = static_cast<uint16_t>(allocation.localWaitCke.startId + index);
        proof.peerRank = index + 1;
        proof.peerExchangeObserved = true;
        proof.endpointRouteVerified = true;
        proof.channelResourceOwnerVerified = true;
        proof.transportResourceExchangeVerified = true;
        plan->remoteXnBindings.push_back(proof);
    }
    return TILEXR_SUCCESS;
}

int Check(uint32_t destinations, bool participationOnly)
{
    TileXRCcuBasicInfo basic;
    basic.dieId = 1;
    basic.msId = 0x45;
    basic.msidToken.valid = true;
    basic.msidToken.tokenId = 0x1234;
    basic.msidToken.tokenValue = 0x5678;
    basic.missionKey = 0x059b0f03U;
    basic.resourceAddr = 0x100000000ULL;
    basic.caps.cap0 = (7U << 24) | (11U << 16) | 169U;
    basic.caps.cap1 = (3071U << 16) | 31U;
    basic.caps.cap2 = (63U << 16) | 35U;
    basic.caps.cap3 = (127U << 16) | 3U;
    basic.caps.cap4 = 15U;

    bool callbackObserved = false;
    TileXRCcuDirectInstallOptions options;
    options.basicInfo = &basic;
    options.provider = "aiv-prepare-host-regression";
    options.offlineOnly = true;
    options.lowerLayerParticipationOnly = participationOnly;
    options.missionStartId = 6;
    options.instructionStartId = 475;
    options.xnStartId = 1961;
    options.gsaStartId = 510;
    options.ckeStartId = 332;
    options.channelStartId = 2;
    options.prepareLowerLayerPlan = PreparePeerProof;
    options.lowerLayerPlanUserData = &callbackObserved;

    TileXRCcuDirectAivXnCkeTriggerSpec trigger;
    trigger.sourcePoolAddr = 0x20000000ULL;
    trigger.sourceToken = 0x12345678ULL;
    trigger.fanoutLoopUntilAivEnd = true;
    for (uint32_t index = 0; index < destinations; ++index) {
        trigger.fanoutDestinations.push_back({index + 1, 0x30000000ULL + index * 4096, 0x34567890ULL});
    }

    TileXRCcuDirectInstallAttempt attempt;
    TileXRCcuDirectInstallReport report;
    const int ret = TileXRCcuRunDirectAivXnCkeTriggerInstallAttempt(options, trigger, &attempt, &report);
    if (!callbackObserved || attempt.allocation.remoteXn.num != destinations ||
        (participationOnly ? ret != TILEXR_SUCCESS : !report.pipelineBuilt)) {
        std::cerr << "destinations=" << destinations << " participationOnly=" << participationOnly
                  << " remoteXn=" << attempt.allocation.remoteXn.num << " ret=" << ret
                  << " message=" << report.message << "\n";
        return 1;
    }
    if (!participationOnly) {
        for (uint32_t index = 0; index < destinations; ++index) {
            const auto& resource = attempt.plan.syncResources[index];
            if (resource.localXn != attempt.allocation.localXn.startId + index ||
                resource.remoteXn != 2400 + index || resource.notifyCke != 400 + index) {
                std::cerr << "peer proof not reconciled for channel " << index << "\n";
                return 2;
            }
        }
    }
    return 0;
}

int main()
{
    for (uint32_t destinations : {1U, 3U}) {
        for (bool participationOnly : {false, true}) {
            if (Check(destinations, participationOnly) != 0) {
                return 1;
            }
        }
    }
    std::cout << "AIV prepare: 4 Host cases passed; no NPU workload\n";
    return 0;
}
