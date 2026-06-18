#include "TeleopLeaderPublisher.h"

#include "HalJson.h"

#include <fastcdr/Cdr.h>
#include <fastcdr/FastBuffer.h>
#include <fastdds/dds/domain/DomainParticipant.hpp>
#include <fastdds/dds/domain/DomainParticipantFactory.hpp>
#include <fastdds/dds/domain/qos/DomainParticipantQos.hpp>
#include <fastdds/dds/publisher/DataWriter.hpp>
#include <fastdds/dds/publisher/Publisher.hpp>
#include <fastdds/dds/publisher/qos/DataWriterQos.hpp>
#include <fastdds/dds/topic/Topic.hpp>
#include <fastdds/dds/topic/TopicDataType.hpp>
#include <fastdds/dds/topic/TypeSupport.hpp>
#include <fastdds/rtps/common/SerializedPayload.h>
#include <fastdds/rtps/transport/UDPv4TransportDescriptor.h>
#include <fastrtps/types/TypesBase.h>

#include <chrono>
#include <functional>
#include <iostream>
#include <memory>
#include <stdexcept>

namespace appstation::hal {
namespace {

// LeaderState 仍走 JSON envelope，便于沿用 Omega7State 的既有序列化格式。
using eprosima::fastdds::dds::BEST_EFFORT_RELIABILITY_QOS;
using eprosima::fastdds::dds::DATAWRITER_QOS_DEFAULT;
using eprosima::fastdds::dds::DataRepresentationId_t;
using eprosima::fastdds::dds::DataWriter;
using eprosima::fastdds::dds::DataWriterQos;
using eprosima::fastdds::dds::DomainParticipant;
using eprosima::fastdds::dds::DomainParticipantFactory;
using eprosima::fastdds::dds::DomainParticipantQos;
using eprosima::fastdds::dds::KEEP_LAST_HISTORY_QOS;
using eprosima::fastdds::dds::PUBLISHER_QOS_DEFAULT;
using eprosima::fastdds::dds::Publisher;
using eprosima::fastdds::dds::TOPIC_QOS_DEFAULT;
using eprosima::fastdds::dds::Topic;
using eprosima::fastdds::dds::TopicDataType;
using eprosima::fastdds::dds::TypeSupport;
using eprosima::fastdds::dds::VOLATILE_DURABILITY_QOS;
using eprosima::fastrtps::types::ReturnCode_t;

constexpr const char* kJsonEnvelopeType = "appstation.JsonEnvelope";
constexpr const char* kTopicTeleopLeaderState = "AppStation.Teleop.LeaderState";

struct JsonEnvelopeSample {
  std::uint64_t stamp_unix_ms{0};
  std::uint64_t stamp_monotonic_ms{0};
  std::string source;
  std::string payload_json;
};

std::uint64_t unixMs() {
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(now).count());
}

std::uint64_t monotonicMs() {
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(now).count());
}

std::uint32_t estimatedSerializedSize(const JsonEnvelopeSample& sample) {
  return static_cast<std::uint32_t>(80 + sample.source.size() + sample.payload_json.size());
}

void writeSample(eprosima::fastcdr::Cdr& cdr, const JsonEnvelopeSample& sample) {
  cdr << sample.stamp_unix_ms;
  cdr << sample.stamp_monotonic_ms;
  cdr << sample.source;
  cdr << sample.payload_json;
}

void readSample(eprosima::fastcdr::Cdr& cdr, JsonEnvelopeSample& sample) {
  cdr >> sample.stamp_unix_ms;
  cdr >> sample.stamp_monotonic_ms;
  cdr >> sample.source;
  cdr >> sample.payload_json;
}

class JsonEnvelopeTopicDataType final : public TopicDataType {
 public:
  JsonEnvelopeTopicDataType() {
    setName(kJsonEnvelopeType);
    m_typeSize = 1024 * 1024;
    m_isGetKeyDefined = false;
    auto_fill_type_object(false);
    auto_fill_type_information(false);
  }

  bool serialize(void* data, eprosima::fastrtps::rtps::SerializedPayload_t* payload) override {
    auto* sample = static_cast<JsonEnvelopeSample*>(data);
    try {
      payload->reserve(estimatedSerializedSize(*sample));
      eprosima::fastcdr::FastBuffer buffer(reinterpret_cast<char*>(payload->data), payload->max_size);
      eprosima::fastcdr::Cdr cdr(buffer, eprosima::fastcdr::Cdr::DEFAULT_ENDIAN, eprosima::fastcdr::XCDRv1);
      payload->encapsulation = 0x0001;
      cdr.serialize_encapsulation();
      writeSample(cdr, *sample);
      payload->length = static_cast<std::uint32_t>(cdr.get_serialized_data_length());
      return true;
    } catch (const std::exception& exc) {
      std::cerr << "Fast-DDS leader serialize failed: " << exc.what() << "\n";
      return false;
    }
  }

  bool deserialize(eprosima::fastrtps::rtps::SerializedPayload_t* payload, void* data) override {
    auto* sample = static_cast<JsonEnvelopeSample*>(data);
    try {
      eprosima::fastcdr::FastBuffer buffer(reinterpret_cast<char*>(payload->data), payload->length);
      eprosima::fastcdr::Cdr cdr(buffer, eprosima::fastcdr::Cdr::DEFAULT_ENDIAN, eprosima::fastcdr::XCDRv1);
      cdr.read_encapsulation();
      readSample(cdr, *sample);
      return true;
    } catch (const std::exception& exc) {
      std::cerr << "Fast-DDS leader deserialize failed: " << exc.what() << "\n";
      return false;
    }
  }

  std::function<std::uint32_t()> getSerializedSizeProvider(void* data) override {
    auto* sample = static_cast<JsonEnvelopeSample*>(data);
    return [sample]() { return estimatedSerializedSize(*sample); };
  }

  void* createData() override {
    return new JsonEnvelopeSample();
  }

  void deleteData(void* data) override {
    delete static_cast<JsonEnvelopeSample*>(data);
  }

  bool getKey(void*, eprosima::fastrtps::rtps::InstanceHandle_t*, bool = false) override {
    return false;
  }
};

void check(ReturnCode_t code, const char* operation) {
  if (code != ReturnCode_t::RETCODE_OK) {
    throw std::runtime_error(std::string(operation) + " failed");
  }
}

DataWriterQos leaderWriterQos() {
  DataWriterQos qos = DATAWRITER_QOS_DEFAULT;
  // Leader 状态是高频瞬时数据，只保留最新一帧，丢旧帧比堆积延迟更安全。
  qos.history().kind = KEEP_LAST_HISTORY_QOS;
  qos.history().depth = 1;
  qos.resource_limits().max_samples = 1;
  qos.resource_limits().max_instances = 1;
  qos.resource_limits().max_samples_per_instance = 1;
  qos.resource_limits().allocated_samples = 1;
  qos.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
  qos.durability().kind = VOLATILE_DURABILITY_QOS;
  qos.representation().m_value.clear();
  qos.representation().m_value.push_back(DataRepresentationId_t::XCDR_DATA_REPRESENTATION);
  return qos;
}

}  // namespace

struct TeleopLeaderPublisher::Impl {
  bool enabled{false};
  DomainParticipant* participant{nullptr};
  Publisher* publisher{nullptr};
  TypeSupport jsonType;
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
    if (!envBoolValue("APPSTATION_DDS_LAN_DISCOVERY", false)) {
      // 默认限制在本机 DDS 通信；需要跨机器时由环境变量显式打开局域网发现。
      auto udp = std::make_shared<eprosima::fastdds::rtps::UDPv4TransportDescriptor>();
      udp->interfaceWhiteList.push_back("127.0.0.1");
      participantQos.transport().use_builtin_transports = false;
      participantQos.transport().user_transports.push_back(udp);
    }
    participant = DomainParticipantFactory::get_instance()->create_participant(
        static_cast<eprosima::fastdds::dds::DomainId_t>(domainId),
        participantQos);
    if (!participant) {
      throw std::runtime_error("create teleop leader DDS participant failed");
    }
    jsonType = TypeSupport(new JsonEnvelopeTopicDataType());
    check(jsonType.register_type(participant), "register leader json type");
    publisher = participant->create_publisher(PUBLISHER_QOS_DEFAULT);
    topic = participant->create_topic(kTopicTeleopLeaderState, kJsonEnvelopeType, TOPIC_QOS_DEFAULT);
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

  void publishJson(const std::string& payloadJson) {
    if (!enabled || !writer_) {
      return;
    }
    // source 用来区分主手发布者，调试多个 DDS 节点时可以直接从 envelope 追踪来源。
    JsonEnvelopeSample sample;
    sample.stamp_unix_ms = unixMs();
    sample.stamp_monotonic_ms = monotonicMs();
    sample.source = "hal-master";
    sample.payload_json = payloadJson;
    (void)writer_->write(&sample);
  }
};

TeleopLeaderPublisher::TeleopLeaderPublisher() : impl_(std::make_unique<Impl>()) {}

TeleopLeaderPublisher::~TeleopLeaderPublisher() = default;

bool TeleopLeaderPublisher::enabled() const {
  return impl_->enabled;
}

void TeleopLeaderPublisher::publishJson(const std::string& payloadJson) {
  impl_->publishJson(payloadJson);
}

}  // namespace appstation::hal
