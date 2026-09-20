#pragma once

#include <fastdds/dds/domain/qos/DomainParticipantQos.hpp>
#include <fastdds/rtps/transport/shared_mem/SharedMemTransportDescriptor.h>
#include <memory>

namespace appstation::dds {

// HAL 与后端必须使用相同的本机传输策略；发现和数据都不启用网络 transport。
inline void configureLocalTransport(eprosima::fastdds::dds::DomainParticipantQos& qos) {
  auto shm = std::make_shared<eprosima::fastdds::rtps::SharedMemTransportDescriptor>();
  // 控制面类型预留 1 MiB 样本，segment 必须更大以容纳并发遥测与 RTPS 开销。
  shm->segment_size(8 * 1024 * 1024);
  qos.transport().use_builtin_transports = false;
  qos.transport().user_transports.clear();
  qos.transport().user_transports.push_back(shm);
}

}  // namespace appstation::dds
