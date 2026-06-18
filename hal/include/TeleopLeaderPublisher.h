#pragma once

#include <memory>
#include <string>

namespace appstation::hal {

// Master/Leader 侧 DDS 发布器：把 Omega 双手状态以 JSON envelope 发布给 Mapping 节点。
class TeleopLeaderPublisher {
 public:
  TeleopLeaderPublisher();
  ~TeleopLeaderPublisher();

  TeleopLeaderPublisher(const TeleopLeaderPublisher&) = delete;
  TeleopLeaderPublisher& operator=(const TeleopLeaderPublisher&) = delete;

  bool enabled() const;
  void publishJson(const std::string& payloadJson);

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace appstation::hal
