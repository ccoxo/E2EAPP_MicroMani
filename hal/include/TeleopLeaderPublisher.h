/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明TeleopLeaderPublisher 的接口与状态结构；把主手采样状态发布到 DDS LeaderState 主题。
 * 先看：TeleopLeaderPublisher → Impl。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
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
