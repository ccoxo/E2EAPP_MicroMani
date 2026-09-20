/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：把主手采样状态发布到 DDS LeaderState 主题。
 * 先看：dds::LeaderState → dds::PlainType → TeleopLeaderPublisher → TeleopLeaderPublisher::enabled。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include "TeleopLeaderPublisher.h"

#include "HalJson.h"
#include "TeleopDdsPlainTypes.h"

#include <fastdds/dds/domain/DomainParticipant.hpp>
#include <fastdds/dds/domain/DomainParticipantFactory.hpp>
#include <fastdds/dds/domain/qos/DomainParticipantQos.hpp>
#include <fastdds/dds/publisher/DataWriter.hpp>
#include <fastdds/dds/publisher/Publisher.hpp>
#include <fastdds/dds/publisher/qos/DataWriterQos.hpp>
#include <fastdds/dds/topic/Topic.hpp>
#include <fastdds/dds/topic/TypeSupport.hpp>
#include "LocalDdsTransport.h"
#include <fastrtps/types/TypesBase.h>

#include <chrono>
#include <memory>
#include <stdexcept>

namespace appstation::hal {
namespace {

// LeaderState 使用固定布局，发布时直接填充 DDS 借出的样本。
using eprosima::fastdds::dds::DATAWRITER_QOS_DEFAULT;
using eprosima::fastdds::dds::DataWriter;
using eprosima::fastdds::dds::DataWriterQos;
using eprosima::fastdds::dds::DomainParticipant;
using eprosima::fastdds::dds::DomainParticipantFactory;
using eprosima::fastdds::dds::DomainParticipantQos;
using eprosima::fastdds::dds::PUBLISHER_QOS_DEFAULT;
using eprosima::fastdds::dds::Publisher;
using eprosima::fastdds::dds::TOPIC_QOS_DEFAULT;
using eprosima::fastdds::dds::Topic;
using eprosima::fastdds::dds::TypeSupport;
using eprosima::fastrtps::types::ReturnCode_t;

void check(ReturnCode_t code, const char* operation) {
  if (code != ReturnCode_t::RETCODE_OK) throw std::runtime_error(operation);
}

std::uint64_t unixMs() {
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::system_clock::now().time_since_epoch()).count());
}
std::uint64_t monotonicMs() {
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count());
}
DataWriterQos leaderWriterQos() {
  DataWriterQos qos = DATAWRITER_QOS_DEFAULT;
  dds::configure(qos, 1);
  return qos;
}

}  // namespace

struct TeleopLeaderPublisher::Impl {
  bool enabled{false};
  DomainParticipant* participant{nullptr};
  Publisher* publisher{nullptr};
  TypeSupport leaderType;
  Topic* topic{nullptr};
  DataWriter* writer_{nullptr};

  Impl() : enabled(envBoolValue("APPSTATION_HAL_DDS_ENABLED", false)) {
    if (!enabled) {
      return;
    }
    const int domainId = envIntValue("APPSTATION_DDS_DOMAIN_ID", 42);
    DomainParticipantQos participantQos;
    check(DomainParticipantFactory::get_instance()->get_default_participant_qos(participantQos), "get participant qos");
    participantQos.name("AppStationTeleopLeaderPublisher");
    appstation::dds::configureLocalTransport(participantQos);
    participant = DomainParticipantFactory::get_instance()->create_participant(
        static_cast<eprosima::fastdds::dds::DomainId_t>(domainId),
        participantQos);
    if (!participant) {
      throw std::runtime_error("create teleop leader DDS participant failed");
    }
    leaderType = TypeSupport(new dds::PlainType<dds::LeaderState>(dds::kLeaderType));
    check(leaderType.register_type(participant), "register leader plain type");
    publisher = participant->create_publisher(PUBLISHER_QOS_DEFAULT);
    topic = participant->create_topic(dds::kLeaderTopic, dds::kLeaderType, TOPIC_QOS_DEFAULT);
    if (!publisher || !topic) {
      throw std::runtime_error("create teleop leader DDS publisher/topic failed");
    }
    writer_ = publisher->create_datawriter(topic, leaderWriterQos());
    if (!writer_) {
      throw std::runtime_error("create teleop leader DDS writer failed");
    }
  }

  ~Impl() {
    if (participant) {
      (void)participant->delete_contained_entities();
      (void)DomainParticipantFactory::get_instance()->delete_participant(participant);
    }
  }

  void publish(const std::array<Omega7State, 2>& hands) {
    if (!enabled || !writer_) {
      return;
    }
    dds::publish<dds::LeaderState>(*writer_, [&](dds::LeaderState& sample) {
      sample.stampUnixMs = unixMs();
      sample.stampMonotonicMs = monotonicMs();
      for (std::size_t i = 0; i < hands.size(); ++i) dds::fillHand(sample.hands[i], hands[i]);
    });
  }
};

TeleopLeaderPublisher::TeleopLeaderPublisher() : impl_(std::make_unique<Impl>()) {}

TeleopLeaderPublisher::~TeleopLeaderPublisher() = default;

bool TeleopLeaderPublisher::enabled() const {
  return impl_->enabled;
}

void TeleopLeaderPublisher::publish(const std::array<Omega7State, 2>& hands) {
  impl_->publish(hands);
}

}  // namespace appstation::hal
