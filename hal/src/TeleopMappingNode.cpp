#include "TeleopMappingNode.h"

#include "HalJson.h"

#include <fastcdr/Cdr.h>
#include <fastcdr/FastBuffer.h>
#include <fastdds/dds/core/LoanableSequence.hpp>
#include <fastdds/dds/domain/DomainParticipant.hpp>
#include <fastdds/dds/domain/DomainParticipantFactory.hpp>
#include <fastdds/dds/domain/qos/DomainParticipantQos.hpp>
#include <fastdds/dds/publisher/DataWriter.hpp>
#include <fastdds/dds/publisher/Publisher.hpp>
#include <fastdds/dds/publisher/qos/DataWriterQos.hpp>
#include <fastdds/dds/subscriber/DataReader.hpp>
#include <fastdds/dds/subscriber/DataReaderListener.hpp>
#include <fastdds/dds/subscriber/SampleInfo.hpp>
#include <fastdds/dds/subscriber/Subscriber.hpp>
#include <fastdds/dds/subscriber/qos/DataReaderQos.hpp>
#include <fastdds/dds/topic/Topic.hpp>
#include <fastdds/dds/topic/TopicDataType.hpp>
#include <fastdds/dds/topic/TypeSupport.hpp>
#include <fastdds/rtps/common/SerializedPayload.h>
#include <fastdds/rtps/transport/UDPv4TransportDescriptor.h>
#include <fastrtps/types/TypesBase.h>

#include <array>
#include <functional>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

namespace appstation::hal {
namespace {

// 该节点把 JSON leader-state 转换成紧凑的 hardware-target topic。
// 字段顺序必须和 hal/dds/appstation_hal.idl 保持一致。
using eprosima::fastdds::dds::BEST_EFFORT_RELIABILITY_QOS;
using eprosima::fastdds::dds::DATAREADER_QOS_DEFAULT;
using eprosima::fastdds::dds::DATAWRITER_QOS_DEFAULT;
using eprosima::fastdds::dds::DataReader;
using eprosima::fastdds::dds::DataReaderListener;
using eprosima::fastdds::dds::DataReaderQos;
using eprosima::fastdds::dds::DataRepresentationId_t;
using eprosima::fastdds::dds::DataWriter;
using eprosima::fastdds::dds::DataWriterQos;
using eprosima::fastdds::dds::DomainParticipant;
using eprosima::fastdds::dds::DomainParticipantFactory;
using eprosima::fastdds::dds::DomainParticipantQos;
using eprosima::fastdds::dds::KEEP_LAST_HISTORY_QOS;
using eprosima::fastdds::dds::PUBLISHER_QOS_DEFAULT;
using eprosima::fastdds::dds::Publisher;
using eprosima::fastdds::dds::SampleInfoSeq;
using eprosima::fastdds::dds::SUBSCRIBER_QOS_DEFAULT;
using eprosima::fastdds::dds::Subscriber;
using eprosima::fastdds::dds::TOPIC_QOS_DEFAULT;
using eprosima::fastdds::dds::Topic;
using eprosima::fastdds::dds::TopicDataType;
using eprosima::fastdds::dds::TypeSupport;
using eprosima::fastdds::dds::VOLATILE_DURABILITY_QOS;
using eprosima::fastrtps::types::ReturnCode_t;

constexpr const char* kJsonEnvelopeType = "appstation.JsonEnvelope";
constexpr const char* kTeleopHardwareTargetType = "appstation.TeleopHardwareTarget";
constexpr const char* kTopicTeleopLeaderState = "AppStation.Teleop.LeaderState";
constexpr const char* kTopicTeleopHardwareTarget = "AppStation.Teleop.HardwareTarget";

struct JsonEnvelopeSample {
  std::uint64_t stamp_unix_ms{0};
  std::uint64_t stamp_monotonic_ms{0};
  std::string source;
  std::string payload_json;
};

struct TeleopHardwareTargetSample {
  std::uint64_t sequence{0};
  std::uint64_t stamp_unix_ms{0};
  std::uint64_t stamp_monotonic_ms{0};
  std::int32_t side{0};
  std::array<double, 6> deltas{};
  double translation_step_limit_pulse{0.0};
  double rotation_step_limit_pulse{0.0};
  double translation_pulse_deadband{0.0};
  double rotation_pulse_deadband{0.0};
  std::array<bool, 6> enabled_axes{{true, true, true, true, true, true}};
  bool sync_zero_delta_target{false};
  std::array<double, 6> soft_limit_min{};
  std::array<double, 6> soft_limit_max{};
  double translation_velocity_ui_per_sec{0.0};
  double rotation_velocity_ui_per_sec{0.0};
  double translation_start_velocity_ui_per_sec{0.0};
  double rotation_start_velocity_ui_per_sec{0.0};
  double acc_time_sec{0.0};
  double dec_time_sec{0.0};
};

std::uint32_t estimatedSerializedSize(const JsonEnvelopeSample& sample) {
  return static_cast<std::uint32_t>(80 + sample.source.size() + sample.payload_json.size());
}

std::uint32_t estimatedSerializedSize(const TeleopHardwareTargetSample&) {
  return 512;
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

void writeSample(eprosima::fastcdr::Cdr& cdr, const TeleopHardwareTargetSample& sample) {
  // 不使用 memcpy：bool 和数组布局受编译器影响，而 DDS 线上的字段顺序必须稳定。
  cdr << sample.sequence;
  cdr << sample.stamp_unix_ms;
  cdr << sample.stamp_monotonic_ms;
  cdr << sample.side;
  for (double value : sample.deltas) cdr << value;
  cdr << sample.translation_step_limit_pulse;
  cdr << sample.rotation_step_limit_pulse;
  cdr << sample.translation_pulse_deadband;
  cdr << sample.rotation_pulse_deadband;
  for (bool value : sample.enabled_axes) cdr << value;
  cdr << sample.sync_zero_delta_target;
  for (double value : sample.soft_limit_min) cdr << value;
  for (double value : sample.soft_limit_max) cdr << value;
  cdr << sample.translation_velocity_ui_per_sec;
  cdr << sample.rotation_velocity_ui_per_sec;
  cdr << sample.translation_start_velocity_ui_per_sec;
  cdr << sample.rotation_start_velocity_ui_per_sec;
  cdr << sample.acc_time_sec;
  cdr << sample.dec_time_sec;
}

void readSample(eprosima::fastcdr::Cdr& cdr, TeleopHardwareTargetSample& sample) {
  cdr >> sample.sequence;
  cdr >> sample.stamp_unix_ms;
  cdr >> sample.stamp_monotonic_ms;
  cdr >> sample.side;
  for (double& value : sample.deltas) cdr >> value;
  cdr >> sample.translation_step_limit_pulse;
  cdr >> sample.rotation_step_limit_pulse;
  cdr >> sample.translation_pulse_deadband;
  cdr >> sample.rotation_pulse_deadband;
  for (bool& value : sample.enabled_axes) cdr >> value;
  cdr >> sample.sync_zero_delta_target;
  for (double& value : sample.soft_limit_min) cdr >> value;
  for (double& value : sample.soft_limit_max) cdr >> value;
  cdr >> sample.translation_velocity_ui_per_sec;
  cdr >> sample.rotation_velocity_ui_per_sec;
  cdr >> sample.translation_start_velocity_ui_per_sec;
  cdr >> sample.rotation_start_velocity_ui_per_sec;
  cdr >> sample.acc_time_sec;
  cdr >> sample.dec_time_sec;
}

template <typename Sample>
class TeleopTopicDataType final : public TopicDataType {
 public:
  TeleopTopicDataType(const char* typeName, std::uint32_t typeSize) {
    setName(typeName);
    m_typeSize = typeSize;
    m_isGetKeyDefined = false;
    auto_fill_type_object(false);
    auto_fill_type_information(false);
  }

  bool serialize(void* data, eprosima::fastrtps::rtps::SerializedPayload_t* payload) override {
    auto* sample = static_cast<Sample*>(data);
    payload->reserve(estimatedSerializedSize(*sample));
    eprosima::fastcdr::FastBuffer buffer(reinterpret_cast<char*>(payload->data), payload->max_size);
    eprosima::fastcdr::Cdr cdr(buffer, eprosima::fastcdr::Cdr::DEFAULT_ENDIAN, eprosima::fastcdr::XCDRv1);
    payload->encapsulation = 0x0001;
    cdr.serialize_encapsulation();
    writeSample(cdr, *sample);
    payload->length = static_cast<std::uint32_t>(cdr.get_serialized_data_length());
    return true;
  }

  bool deserialize(eprosima::fastrtps::rtps::SerializedPayload_t* payload, void* data) override {
    auto* sample = static_cast<Sample*>(data);
    eprosima::fastcdr::FastBuffer buffer(reinterpret_cast<char*>(payload->data), payload->length);
    eprosima::fastcdr::Cdr cdr(buffer, eprosima::fastcdr::Cdr::DEFAULT_ENDIAN, eprosima::fastcdr::XCDRv1);
    cdr.read_encapsulation();
    readSample(cdr, *sample);
    return true;
  }

  std::function<std::uint32_t()> getSerializedSizeProvider(void* data) override {
    auto* sample = static_cast<Sample*>(data);
    return [sample]() { return estimatedSerializedSize(*sample); };
  }

  void* createData() override {
    return new Sample();
  }

  void deleteData(void* data) override {
    delete static_cast<Sample*>(data);
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

void configureReaderHistory(DataReaderQos& qos, int depth) {
  qos.history().kind = KEEP_LAST_HISTORY_QOS;
  qos.history().depth = depth;
  qos.resource_limits().max_samples = depth;
  qos.resource_limits().max_instances = 1;
  qos.resource_limits().max_samples_per_instance = depth;
  qos.resource_limits().allocated_samples = depth;
  qos.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
  qos.durability().kind = VOLATILE_DURABILITY_QOS;
  qos.type_consistency().representation.m_value.clear();
  qos.type_consistency().representation.m_value.push_back(DataRepresentationId_t::XCDR_DATA_REPRESENTATION);
}

void configureWriterHistory(DataWriterQos& qos, int depth) {
  qos.history().kind = KEEP_LAST_HISTORY_QOS;
  qos.history().depth = depth;
  qos.resource_limits().max_samples = depth;
  qos.resource_limits().max_instances = 1;
  qos.resource_limits().max_samples_per_instance = depth;
  qos.resource_limits().allocated_samples = depth;
  qos.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
  qos.durability().kind = VOLATILE_DURABILITY_QOS;
  qos.representation().m_value.clear();
  qos.representation().m_value.push_back(DataRepresentationId_t::XCDR_DATA_REPRESENTATION);
}

TeleopHardwareTargetSample toSample(const TeleopHardwareTarget& target) {
  TeleopHardwareTargetSample sample;
  sample.sequence = target.sequence;
  sample.stamp_unix_ms = target.stampUnixMs;
  sample.stamp_monotonic_ms = target.stampMonotonicMs;
  sample.side = target.side;
  sample.deltas = target.deltas;
  sample.translation_step_limit_pulse = target.translationStepLimitPulse;
  sample.rotation_step_limit_pulse = target.rotationStepLimitPulse;
  sample.translation_pulse_deadband = target.translationPulseDeadband;
  sample.rotation_pulse_deadband = target.rotationPulseDeadband;
  sample.enabled_axes = target.enabledAxes;
  sample.sync_zero_delta_target = target.syncZeroDeltaTarget;
  sample.soft_limit_min = target.softLimitMin;
  sample.soft_limit_max = target.softLimitMax;
  sample.translation_velocity_ui_per_sec = target.translationVelocityUiPerSec;
  sample.rotation_velocity_ui_per_sec = target.rotationVelocityUiPerSec;
  sample.translation_start_velocity_ui_per_sec = target.translationStartVelocityUiPerSec;
  sample.rotation_start_velocity_ui_per_sec = target.rotationStartVelocityUiPerSec;
  sample.acc_time_sec = target.accTimeSec;
  sample.dec_time_sec = target.decTimeSec;
  return sample;
}

}  // namespace

struct TeleopMappingNode::Impl {
  struct LeaderListener final : public DataReaderListener {
    explicit LeaderListener(Impl& owner) : owner_(owner) {}
    void on_data_available(DataReader* reader) override;

   private:
    Impl& owner_;
  };

  NativeTeleopController& nativeTeleop_;
  LeaderListener listener;
  bool enabled{false};
  bool listening{false};
  DomainParticipant* participant{nullptr};
  Subscriber* subscriber{nullptr};
  Publisher* publisher{nullptr};
  TypeSupport jsonType;
  TypeSupport targetType;
  Topic* leaderTopic{nullptr};
  Topic* targetTopic{nullptr};
  DataReader* leaderReader{nullptr};
  DataWriter* targetWriter_{nullptr};
  std::uint64_t lastLeaderMonotonicMs{0};

  explicit Impl(NativeTeleopController& nativeTeleop)
      : nativeTeleop_(nativeTeleop),
        listener(*this),
        enabled(envBoolValue("APPSTATION_HAL_DDS_ENABLED", false)) {
    if (!enabled) return;
    const int domainId = envIntValue("APPSTATION_DDS_DOMAIN_ID", 42);
    DomainParticipantQos participantQos;
    check(DomainParticipantFactory::get_instance()->get_default_participant_qos(participantQos), "get participant qos");
    participantQos.name("AppStationTeleopMappingNode");
    if (!envBoolValue("APPSTATION_DDS_LAN_DISCOVERY", false)) {
      // 默认只在本机发现 Leader/Follower，避免误连到同网段其他工作站。
      auto udp = std::make_shared<eprosima::fastdds::rtps::UDPv4TransportDescriptor>();
      udp->interfaceWhiteList.push_back("127.0.0.1");
      participantQos.transport().use_builtin_transports = false;
      participantQos.transport().user_transports.push_back(udp);
    }
    participant = DomainParticipantFactory::get_instance()->create_participant(
        static_cast<eprosima::fastdds::dds::DomainId_t>(domainId),
        participantQos);
    if (!participant) throw std::runtime_error("create teleop mapping DDS participant failed");

    jsonType = TypeSupport(new TeleopTopicDataType<JsonEnvelopeSample>(kJsonEnvelopeType, 1024 * 1024));
    targetType = TypeSupport(new TeleopTopicDataType<TeleopHardwareTargetSample>(
        kTeleopHardwareTargetType,
        estimatedSerializedSize(TeleopHardwareTargetSample{})));
    check(jsonType.register_type(participant), "register mapping json type");
    check(targetType.register_type(participant), "register mapping hardware target type");

    subscriber = participant->create_subscriber(SUBSCRIBER_QOS_DEFAULT);
    publisher = participant->create_publisher(PUBLISHER_QOS_DEFAULT);
    leaderTopic = participant->create_topic(kTopicTeleopLeaderState, kJsonEnvelopeType, TOPIC_QOS_DEFAULT);
    targetTopic = participant->create_topic(kTopicTeleopHardwareTarget, kTeleopHardwareTargetType, TOPIC_QOS_DEFAULT);
    if (!subscriber || !publisher || !leaderTopic || !targetTopic) {
      throw std::runtime_error("create teleop mapping DDS entities failed");
    }
    DataReaderQos readerQos = DATAREADER_QOS_DEFAULT;
    configureReaderHistory(readerQos, 8);
    leaderReader = subscriber->create_datareader(leaderTopic, readerQos);
    DataWriterQos writerQos = DATAWRITER_QOS_DEFAULT;
    configureWriterHistory(writerQos, 8);
    targetWriter_ = publisher->create_datawriter(targetTopic, writerQos);
    if (!leaderReader || !targetWriter_) {
      throw std::runtime_error("create teleop mapping reader/writer failed");
    }
  }

  ~Impl() {
    stop();
    if (participant) {
      (void)participant->delete_contained_entities();
      (void)DomainParticipantFactory::get_instance()->delete_participant(participant);
    }
  }

  void start() {
    if (!enabled || listening) return;
    listening = true;
    // listener 只负责唤醒读取，实际映射仍交给 NativeTeleopController。
    (void)leaderReader->set_listener(&listener);
  }

  void stop() {
    if (!listening) return;
    listening = false;
    if (leaderReader) (void)leaderReader->set_listener(nullptr);
  }

  void handleLeaderData(DataReader* reader) {
    for (;;) {
      eprosima::fastdds::dds::LoanableSequence<JsonEnvelopeSample> samples(16);
      SampleInfoSeq infos(16);
      const auto result = reader->take(samples, infos, 16);
      if (result == ReturnCode_t::RETCODE_NO_DATA) return;
      if (result != ReturnCode_t::RETCODE_OK) {
        std::cerr << "Fast-DDS teleop leader take failed\n";
        return;
      }
      for (int32_t i = 0; i < samples.length(); ++i) {
        if (!infos[i].valid_data) continue;
        const auto& sample = samples[i];
        // 用 Leader 的单调时间戳估算 dt，时间戳缺失或回退时给控制器一个保守默认值。
        const double dtSec = lastLeaderMonotonicMs > 0 && sample.stamp_monotonic_ms > lastLeaderMonotonicMs
            ? static_cast<double>(sample.stamp_monotonic_ms - lastLeaderMonotonicMs) / 1000.0
            : 0.01;
        lastLeaderMonotonicMs = sample.stamp_monotonic_ms;
        nativeTeleop_.processLeaderState(jsonOmegaStateValue(sample.payload_json), dtSec);
      }
    }
  }

  void publishHardwareTarget(const TeleopHardwareTarget& target) {
    if (!enabled || !targetWriter_) return;
    // NativeTeleopController 生成的进程内目标在这里转换为 DDS 线格式。
    auto sample = toSample(target);
    (void)targetWriter_->write(&sample);
  }
};

void TeleopMappingNode::Impl::LeaderListener::on_data_available(DataReader* reader) {
  owner_.handleLeaderData(reader);
}

TeleopMappingNode::TeleopMappingNode(NativeTeleopController& nativeTeleop)
    : impl_(std::make_unique<Impl>(nativeTeleop)) {}

TeleopMappingNode::~TeleopMappingNode() = default;

bool TeleopMappingNode::enabled() const {
  return impl_->enabled;
}

void TeleopMappingNode::start() {
  impl_->start();
}

void TeleopMappingNode::stop() {
  impl_->stop();
}

void TeleopMappingNode::publishHardwareTarget(const TeleopHardwareTarget& target) {
  impl_->publishHardwareTarget(target);
}

}  // namespace appstation::hal
