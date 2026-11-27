#include <acl/acl.h>
#include <tilexr_api.h>
#include <cstdlib>
#include <iostream>
#include <vector>
int TileXRCommHostAllGatherTimeout(TileXRCommPtr,const void*,size_t,void*,uint32_t);
static void check(int c,const char*s){std::cout<<s<<"="<<c<<std::endl;if(c)std::exit(1);}
int main(){
 int rank=std::atoi(std::getenv("RANK")), size=std::atoi(std::getenv("RANK_SIZE"));
 check(aclInit(nullptr),"aclInit");check(aclrtSetDevice(std::atoi(std::getenv("DEVICE_ID"))),"setDevice");
 TileXRCommPtr comm=nullptr;
 check(TileXRCommInitRankLocal(size,rank,&comm),"commInit");
 check(TileXRRDMAConfigureQps(comm,16),"configure16Qps");
 TileXRRDMAInfo info{};check(TileXRRDMAQuery(comm,&info),"query");
 if(!info.connected||!info.deviceDirect||info.failed)return 3;
 std::vector<int> all(size,-1);
 check(TileXRCommHostAllGatherTimeout(comm,&rank,sizeof(rank),all.data(),30000),"hostAllGather");
 for(int i=0;i<size;++i)if(all[i]!=i)return 4;
 check(TileXRCommDestroy(comm),"commDestroy");
 check(aclFinalize(),"finalize");
 std::cout<<"COMM_ONLY_PASS rank="<<rank<<std::endl;
}
