#include <acl/acl.h>
#include <runtime/kernel.h>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
#include <cstdlib>
#include <cstdint>
static int stub;
int main(int argc, char** argv) {
 if (argc != 4) return 2;
 std::ifstream f(argv[1], std::ios::binary);
 std::vector<char> data((std::istreambuf_iterator<char>(f)), {});
 if (data.empty()) return 3;
 auto check=[](int code,const char* step) { std::cout<<step<<"="<<code<<std::endl; if(code) std::exit(1); };
 check(aclInit(nullptr),"aclInit");
 check(aclrtSetDevice(std::atoi(argv[2])),"setDevice");
 aclrtStream stream=nullptr;
 check(aclrtCreateStream(&stream),"createStream");
 rtDevBinary_t binary{}; binary.magic=0x41415246U; binary.data=data.data(); binary.length=data.size();
 void* handle=nullptr;
 check(rtDevBinaryRegister(&binary,&handle),"registerBinary");
 check(rtFunctionRegister(handle,&stub,"tilexr_ep_ubx_kernel","tilexr_ep_ubx_kernel",0),"registerFunction");
 uint64_t parameters[42]{};
 rtArgsEx_t args{}; args.args=parameters; args.argsSize=sizeof(parameters);
 rtTaskCfgInfo_t cfg{};
 check(rtKernelLaunchWithFlagV2(&stub,std::atoi(argv[3]),&args,nullptr,static_cast<rtStream_t>(stream),0,&cfg),"launch");
 std::cout<<"sync_begin"<<std::endl;
 check(aclrtSynchronizeStream(stream),"sync_done");
 check(aclrtDestroyStream(stream),"destroyStream");
 check(rtDevBinaryUnRegister(handle),"unregisterBinary");
 check(aclrtResetDevice(std::atoi(argv[2])),"resetOwnContext");
 check(aclFinalize(),"finalize");
}
