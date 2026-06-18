#include "TeleopFollowerTargetSubscriber.h"

#include "HalJson.h"

#include <fastcdr/Cdr.h>
#include <fastcdr/FastBuffer.h>
#include <fastdds/dds/core/LoanableSequence.hpp>
#include <fastdds/dds/domain/DomainParticipant.hpp>
#include <fastdds/dds/domain/DomainParticipantFactory.hpp>
#include <fastdds/dds/domain/qos/DomainParticipantQos.hpp>
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

namespace appstation::hal {
namespace {

// Follower 端只消费 hardware-target，不重新解释 Leader JSON，避免两端映射逻辑不一致。
using eprosima::fastdds::dds::BEST_EFFORT_RELIABILITY_QOS;
using eprosima::fastdds::dds::DATAREADER_QOS_DEFAULT;
using eprosima::fastdds::dds::DataReader;
using eprosima::fastdds::dds::DataReaderListener;
using eprosima::fastdds::dds::DataReaderQos;
using eprosima::fastdds::dds::DataRepresentationId_t;
using eprosima::fastdds::dds::DomainParticipant;
using eprosima::fastdds::dds::DomainParticipantFactory;
using eprosima::fastdds::dds::DomainParticipantQos;
using eprosima::fastdds::dds::KEEP_LAST_HISTORY_QOS;
using eprosima::fastdds::dds::SampleInfoSeq;
using eprosima::fastdds::dds::SUBSCRIBER_QOS_DEFAULT;
using eprosima::fastdds::dds::Subscriber;
using eprosima::fastdds::dds::TOPIC_QOS_DEFAULT;
using eprosima::fastdds::dds::Topic;
using eprosima::fastdds::dds::TopicDataType;
using eprosima::fastdds::dds::TypeSupport;
using eprosima::fastdds::dds::VOLATILE_DURABILITY_QOS;
using eprosima::fastrtps::types::ReturnCode_t;

constexpr const char* kTeleopHardwareTargetType = "appstation.TeleopHardwareTarget";
constexpr const char* kTopicTeleopHardwareTarget = "AppStation.Teleop.HardwareTarget";

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

std::uint32_t estimatedSerializedSize(const TeleopHardwareTargetSample&) {
  return 512;
}

void writeSample(eprosima::fastcdr::Cdr& cdr, const TeleopHardwareTargetSample& sample) {
  // 显式逐字段序列化，确保和 Mapping 端及 IDL 的 wire format 完全对齐。
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

class HardwareTargetTopicDataType final : public TopicDataType {
 public:
  HardwareTargetTopicDataType() {
    setName(kTeleopHardwareTargetType);
    m_typeSize = estimatedSerializedSize(TeleopHardwareTargetSample{});
    m_isGetKeyDefined = false;
    auto_fill_type_object(false);
    auto_fill_type_information(false);
  }

  bool serialize(void* data, eprosima::fastrtps::rtps::SerializedPayload_t* payload) override {
    auto* sample = static_cast<TeleopHardwareTargetSample*>(data);
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
    auto* sample = static_cast<TeleopHardwareTargetSample*>(data);
    eprosima::fastcdr::FastBuffer buffer(reinterpret_cast<char*>(payload->data), payload->length);
    eprosima::fastcdr::Cdr cdr(buffer, eprosima::fastcdr::Cdr::DEFAULT_ENDIAN, eprosima::fastcdr::XCDRv1);
    cdr.read_encapsulation();
    readSample(cdr, *sample);
    return true;
  }

  std::function<std::uint32_t()> getSerializedSizeProvider(void* data) override {
    auto* sample = static_cast<TeleopHardwareTargetSample*>(data);
    return [sample]() { return estimatedSerializedSize(*sample); };
  }

  void* createData() override {
    return new TeleopHardwareTargetSample();
  }

  void deleteData(void* data) override {
    delete static_cast<TeleopHardwareTargetSample*>(data);
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

DataReaderQos targetReaderQos() {
  DataReaderQos qos = DATAREADER_QOS_DEFAULT;
  qos.history().kind = KEEP_LAST_HISTORY_QOS;
  qos.history().depth = 8;
  qos.resource_limits().max_samples = 8;
  qos.resource_limits().max_instances = 1;
  qos.resource_limits().max_samples_per_instance = 8;
  qos.resource_limits().allocated_samples = 8;
  qos.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
  qos.durability().kind = VOLATILE_DURABILITY_QOS;
  qos.type_consistency().representation.m_value.clear();
  qos.type_consistency().representation.m_value.push_back(DataRepresentationId_t::XCDR_DATA_REPRESENTATION);
  return qos;
}

TeleopHardwareTarget toTarget(const TeleopHardwareTargetSample& sample) {
  TeleopHardwareTarget target;
  target.sequence = sample.sequence;
  target.stampUnixMs = sample.stamp_unix_ms;
  target.stampMonotonicMs = sample.stamp_monotonic_ms;
  target.side = sample.side;
  target.deltas = sample.deltas;
  target.translationStepLimitPulse = sample.translation_step_limit_pulse;
  target.rotationStepLimitPulse = sample.rotation_step_limit_pulse;
  target.translationPulseDeadband = sample.translation_pulse_deadband;
  target.rotationPulseDeadband = sample.rotation_pulse_deadband;
  target.enabledAxes = sample.enabled_axes;
  target.syncZeroDeltaTarget = sample.sync_zero_delta_target;
  target.softLimitMin = sample.soft_limit_min;
  target.softLimitMax = sample.soft_limit_max;
  target.translationVelocityUiPerSec = sample.translation_velocity_ui_per_sec;
  target.rotationVelocityUiPerSec = sample.rotation_velocity_ui_per_sec;
  target.translationStartVelocityUiPerSec = sample.translation_start_velocity_ui_per_sec;
  target.rotationStartVelocityUiPerSec = sample.rotation_start_velocity_ui_per_sec;
  target.accTimeSec = sample.acc_time_sec;
  target.decTimeSec = sample.dec_time_sec;
  return target;
}

}  // namespace

struct TeleopFollowerTargetSubscriber::Impl {
  struct TargetListener final : public DataReaderListener {
    explicit TargetListener(Impl& owner) : owner_(owner) {}
    void on_data_available(DataReader* reader) override;

   private:
    Impl& owner_;
  };

  TeleopHardwareTargetExecutor& executor_;
  TargetListener listener;
  bool enabled{false};
  bool listening{false};
  DomainParticipant* participant{nullptr};
  Subscriber* subscriber{nullptr};
  TypeSupport targetType;
  Topic* topic{nullptr};
  DataReader* reader{nullptr};

  explicit Impl(TeleopHardwareTargetExecutor& executor)
      : executor_(executor),
        listener(*this),
        enabled(envBoolValue("APPSTATION_HAL_DDS_ENABLED", false)) {
    if (!enabled) return;
    const int domainId = envIntValue("APPSTATION_DDS_DOMAIN_ID", 42);
    DomainParticipantQos participantQos;
    check(DomainParticipantFactory::get_instance()->get_default_participant_qos(participantQos), "get participant qos");
    participantQos.name("AppStationTeleopFollowerTargetSubscriber");
    if (!envBoolValue("APPSTATION_DDS_LAN_DISCOVERY", false)) {
      // 默认限制为本机订阅，跨机跟随需要显式打开局域网发现。
      auto udp = std::make_shared<eprosima::fastdds::rtps::UDPv4TransportDescriptor>();
      udp->interfaceWhiteList.push_back("127.0.0.1");
      participantQos.transport().use_builtin_transports = false;
      participantQos.transport().user_transports.push_back(udp);
    }
    participant = DomainParticipantFactory::get_instance()->create_participant(
        static_cast<eprosima::fastdds::dds::DomainId_t>(domainId),
        participantQos);
    if (!participant) throw std::runtime_error("create follower target DDS participant failed");
    targetType = TypeSupport(new HardwareTargetTopicDataType());
    check(targetType.register_type(participant), "register hardware target type");
    subscriber = participant->create_subscriber(SUBSCRIBER_QOS_DEFAULT);
    topic = participant->create_topic(kTopicTeleopHardwareTarget, kTeleopHardwareTargetType, TOPIC_QOS_DEFAULT);
    if (!subscriber || !topic) throw std::runtime_error("create follower target DDS subscriber/topic failed");
    reader = subscriber->create_datareader(topic, targetReaderQos());
    if (!reader) throw std::runtime_error("create follower target DDS reader failed");
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
    // DataReaderListener 触发后立即 drain 当前批次，减少目标堆积造成的滞后。
    (void)reader->set_listener(&listener);
  }

  void stop() {
    if (!listening) return;
    listening = false;
    if (reader) (void)reader->set_listener(nullptr);
  }

  void handleTargetData(DataReader* dataReader) {
    for (;;) {
      eprosima::fastdds::dds::LoanableSequence<TeleopHardwareTargetSample> samples(16);
      SampleInfoSeq infos(16);
      const auto result = dataReader->take(samples, infos, 16);
      if (result == ReturnCode_t::RETCODE_NO_DATA) return;
      if (result != ReturnCode_t::RETCODE_OK) {
        std::cerr << "Fast-DDS teleop hardware target take failed\n";
        return;
      }
      for (int32_t i = 0; i < samples.length(); ++i) {
        if (!infos[i].valid_data) continue;
        // 执行前先回到进程内结构，安全检查和 LTDMC 调用都集中在 executor。
        const auto target = toTarget(samples[i]);
        executor_.apply(target);
      }
    }
  }
};

void TeleopFollowerTargetSubscriber::Impl::TargetListener::on_data_available(DataReader* reader) {
  owner_.handleTargetData(reader);
}

TeleopFollowerTargetSubscriber::TeleopFollowerTargetSubscriber(TeleopHardwareTargetExecutor& executor)
    : impl_(std::make_unique<Impl>(executor)) {}

TeleopFollowerTargetSubscriber::~TeleopFollowerTargetSubscriber() = default;

bool TeleopFollowerTargetSubscriber::enabled() const {
  return impl_->enabled;
}

void TeleopFollowerTargetSubscriber::start() {
  impl_->start();
}

void TeleopFollowerTargetSubscriber::stop() {
  impl_->stop();
}

}  // namespace appstation::hal
