#pragma once

#include "TeleopDdsTypes.h"
#include "Omega7Driver.h"
#include <fastcdr/Cdr.h>
#include <fastcdr/FastBuffer.h>
#include <fastdds/dds/topic/TopicDataType.hpp>
#include <fastdds/dds/publisher/DataWriter.hpp>
#include <fastdds/dds/subscriber/DataReader.hpp>
#include <fastdds/dds/subscriber/SampleInfo.hpp>
#include <fastdds/dds/core/LoanableSequence.hpp>
#include <fastdds/dds/publisher/qos/DataWriterQos.hpp>
#include <fastdds/dds/subscriber/qos/DataReaderQos.hpp>
#include <algorithm>
#include <cstring>
#include <new>
#include <optional>
#include <stdexcept>
#include <type_traits>

namespace appstation::hal::dds {
using namespace eprosima::fastdds::dds;
using eprosima::fastrtps::types::ReturnCode_t;

inline constexpr const char* kLeaderType = "appstation.TeleopLeaderStateV2";
inline constexpr const char* kLeaderTopic = "AppStation.Teleop.LeaderState.V2";
inline constexpr const char* kTargetType = "appstation.TeleopHardwareTarget";
inline constexpr const char* kTargetTopic = "AppStation.Teleop.HardwareTarget";

// 高频链路只携带控制所需字段；设备名称和序列号仍从 OmegaState 遥测获取。
struct LeaderHand {
  std::uint64_t flags{};
  std::array<double, 6> pose{};
  double gripperGap{};
  std::int64_t readTimestampMs{};
  std::array<char, 256> lastReadError{};
};
struct LeaderState {
  std::uint64_t stampUnixMs{};
  std::uint64_t stampMonotonicMs{};
  std::array<LeaderHand, 2> hands{};
};

inline void fillHand(LeaderHand& sample, const Omega7State& hand) {
  sample.flags = (hand.connected ? 1U : 0U) | (hand.lastReadOk ? 2U : 0U)
      | (hand.clutchPressed ? 4U : 0U) | (hand.gripperPressed ? 8U : 0U)
      | (hand.gripperGapAvailable ? 16U : 0U);
  sample.pose = hand.pose;
  sample.gripperGap = hand.gripperGap;
  sample.readTimestampMs = hand.readTimestampMs;
  sample.lastReadError.fill(0);
  std::memcpy(sample.lastReadError.data(), hand.lastReadError.data(),
      (std::min)(hand.lastReadError.size(), sample.lastReadError.size() - 1));
}
inline std::array<Omega7State, 2> toHands(const LeaderState& sample) {
  std::array<Omega7State, 2> hands{};
  for (std::size_t i = 0; i < hands.size(); ++i) {
    const auto& wire = sample.hands[i];
    auto& hand = hands[i];
    hand.connected = (wire.flags & 1) != 0;
    hand.lastReadOk = (wire.flags & 2) != 0;
    hand.clutchPressed = (wire.flags & 4) != 0;
    hand.gripperPressed = (wire.flags & 8) != 0;
    hand.gripperGapAvailable = (wire.flags & 16) != 0;
    hand.pose = wire.pose;
    hand.gripperGap = wire.gripperGap;
    hand.readTimestampMs = wire.readTimestampMs;
    const auto end = std::find(wire.lastReadError.begin(), wire.lastReadError.end(), '\0');
    hand.lastReadError.assign(wire.lastReadError.begin(), end);
  }
  return hands;
}

inline void writeSample(eprosima::fastcdr::Cdr& cdr, const LeaderState& sample) {
  cdr << sample.stampUnixMs << sample.stampMonotonicMs;
  for (const auto& hand : sample.hands) {
    cdr << hand.flags;
    for (auto value : hand.pose) cdr << value;
    cdr << hand.gripperGap << hand.readTimestampMs;
    cdr.serialize_array(hand.lastReadError.data(), hand.lastReadError.size());
  }
}
inline void readSample(eprosima::fastcdr::Cdr& cdr, LeaderState& sample) {
  cdr >> sample.stampUnixMs >> sample.stampMonotonicMs;
  for (auto& hand : sample.hands) {
    cdr >> hand.flags;
    for (auto& value : hand.pose) cdr >> value;
    cdr >> hand.gripperGap >> hand.readTimestampMs;
    cdr.deserialize_array(hand.lastReadError.data(), hand.lastReadError.size());
  }
}

inline void writeSample(eprosima::fastcdr::Cdr& cdr, const TeleopHardwareTarget& sample) {
  // 不使用 memcpy：bool 和数组布局受编译器影响，而 DDS 线上的字段顺序必须稳定。
  cdr << sample.sequence;
  cdr << sample.stampUnixMs;
  cdr << sample.stampMonotonicMs;
  cdr << sample.side;
  for (double value : sample.deltas) cdr << value;
  cdr << sample.translationStepLimitPulse;
  cdr << sample.rotationStepLimitPulse;
  cdr << sample.translationPulseDeadband;
  cdr << sample.rotationPulseDeadband;
  for (bool value : sample.enabledAxes) cdr << value;
  cdr << sample.syncZeroDeltaTarget;
  for (double value : sample.softLimitMin) cdr << value;
  for (double value : sample.softLimitMax) cdr << value;
  cdr << sample.translationVelocityUiPerSec;
  cdr << sample.rotationVelocityUiPerSec;
  cdr << sample.translationStartVelocityUiPerSec;
  cdr << sample.rotationStartVelocityUiPerSec;
  cdr << sample.accTimeSec;
  cdr << sample.decTimeSec;
}

inline void readSample(eprosima::fastcdr::Cdr& cdr, TeleopHardwareTarget& sample) {
  cdr >> sample.sequence;
  cdr >> sample.stampUnixMs;
  cdr >> sample.stampMonotonicMs;
  cdr >> sample.side;
  for (double& value : sample.deltas) cdr >> value;
  cdr >> sample.translationStepLimitPulse;
  cdr >> sample.rotationStepLimitPulse;
  cdr >> sample.translationPulseDeadband;
  cdr >> sample.rotationPulseDeadband;
  for (bool& value : sample.enabledAxes) cdr >> value;
  cdr >> sample.syncZeroDeltaTarget;
  for (double& value : sample.softLimitMin) cdr >> value;
  for (double& value : sample.softLimitMax) cdr >> value;
  cdr >> sample.translationVelocityUiPerSec;
  cdr >> sample.rotationVelocityUiPerSec;
  cdr >> sample.translationStartVelocityUiPerSec;
  cdr >> sample.rotationStartVelocityUiPerSec;
  cdr >> sample.accTimeSec;
  cdr >> sample.decTimeSec;
}


// XCDRv1 的字段布局必须和本机布局相同。布局测试逐字段检查编码，不能仅声明 plain。
static_assert(sizeof(LeaderHand) == 328 && sizeof(LeaderState) == 672);
static_assert(sizeof(TeleopHardwareTarget) == 264);

template <typename Sample>
class PlainType : public TopicDataType {
 public:
  explicit PlainType(const char* name) {
    static_assert(std::is_standard_layout_v<Sample> && std::is_trivially_copyable_v<Sample>);
    setName(name);
    m_typeSize = static_cast<std::uint32_t>(sizeof(Sample) + 4);
    m_isGetKeyDefined = false;
    auto_fill_type_object(false);
    auto_fill_type_information(false);
  }
  bool is_bounded() const override { return true; }
  bool is_plain() const override { return true; }
  bool is_plain(DataRepresentationId_t representation) const override {
    return representation == XCDR_DATA_REPRESENTATION;
  }
  bool construct_sample(void* memory) const override { new (memory) Sample(); return true; }
  bool serialize(void* data, eprosima::fastrtps::rtps::SerializedPayload_t* payload) override {
    eprosima::fastcdr::FastBuffer buffer(reinterpret_cast<char*>(payload->data), payload->max_size);
    eprosima::fastcdr::Cdr cdr(buffer, eprosima::fastcdr::Cdr::DEFAULT_ENDIAN, eprosima::fastcdr::XCDRv1);
    cdr.serialize_encapsulation();
    payload->encapsulation = cdr.endianness() == eprosima::fastcdr::Cdr::LITTLE_ENDIANNESS ? 0x0001 : 0x0000;
    writeSample(cdr, *static_cast<Sample*>(data));
    payload->length = static_cast<std::uint32_t>(cdr.get_serialized_data_length());
    return true;
  }
  bool deserialize(eprosima::fastrtps::rtps::SerializedPayload_t* payload, void* data) override {
    eprosima::fastcdr::FastBuffer buffer(reinterpret_cast<char*>(payload->data), payload->length);
    eprosima::fastcdr::Cdr cdr(buffer, eprosima::fastcdr::Cdr::DEFAULT_ENDIAN, eprosima::fastcdr::XCDRv1);
    cdr.read_encapsulation();
    readSample(cdr, *static_cast<Sample*>(data));
    return true;
  }
  std::function<std::uint32_t()> getSerializedSizeProvider(void*) override {
    return [] { return static_cast<std::uint32_t>(sizeof(Sample) + 4); };
  }
  void* createData() override { return new Sample(); }
  void deleteData(void* data) override { delete static_cast<Sample*>(data); }
  bool getKey(void*, eprosima::fastrtps::rtps::InstanceHandle_t*, bool = false) override { return false; }
};

inline void check(ReturnCode_t result, const char* operation) {
  if (result != ReturnCode_t::RETCODE_OK) throw std::runtime_error(operation);
}
inline void configure(DataWriterQos& qos, int depth) {
  qos.history().kind = KEEP_LAST_HISTORY_QOS;
  qos.history().depth = depth;
  qos.resource_limits().max_samples = depth;
  qos.resource_limits().max_instances = 1;
  qos.resource_limits().max_samples_per_instance = depth;
  qos.resource_limits().allocated_samples = depth;
  qos.resource_limits().extra_samples = depth;
  qos.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
  qos.reliability().max_blocking_time = {0, 1000000};
  qos.durability().kind = VOLATILE_DURABILITY_QOS;
  qos.endpoint().history_memory_policy = eprosima::fastrtps::rtps::PREALLOCATED_MEMORY_MODE;
  qos.representation().m_value = {XCDR_DATA_REPRESENTATION};
  qos.data_sharing().on("");
}
inline void configure(DataReaderQos& qos, int depth) {
  qos.history().kind = KEEP_LAST_HISTORY_QOS;
  qos.history().depth = depth;
  qos.resource_limits().max_samples = depth;
  qos.resource_limits().max_instances = 1;
  qos.resource_limits().max_samples_per_instance = depth;
  qos.resource_limits().allocated_samples = depth;
  qos.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
  qos.durability().kind = VOLATILE_DURABILITY_QOS;
  qos.type_consistency().representation.m_value = {XCDR_DATA_REPRESENTATION};
  qos.data_sharing().on("");
}

// 未发布/异常路径归还 writer loan；成功发布后不再访问该内存。
template <typename Sample, typename Fill>
void publish(DataWriter& writer, Fill fill) {
  void* raw = nullptr;
  check(writer.loan_sample(raw), "DDS sample loan failed");
  try {
    fill(*static_cast<Sample*>(raw));
    if (!writer.write(raw)) throw std::runtime_error("DDS loan publication failed");
  } catch (...) {
    (void)writer.discard_loan(raw);
    throw;
  }
}

template <typename Sample>
class ReadLoan {
 public:
  explicit ReadLoan(DataReader& reader) : reader_(reader) {
    const auto result = reader_.take(samples, infos, 16);
    if (result == ReturnCode_t::RETCODE_NO_DATA) return;
    check(result, "DDS sample take failed");
    borrowed_ = true;
  }
  ~ReadLoan() { if (borrowed_) (void)reader_.return_loan(samples, infos); }
  ReadLoan(const ReadLoan&) = delete;
  ReadLoan& operator=(const ReadLoan&) = delete;
  // DataSharing 池可能在 take 后复用。控制执行前保留一个稳定快照，
  // 并在复制前后验证样本；不能在硬件调用中持有可被覆盖的共享数据。
  std::optional<Sample> snapshot(int32_t i) {
    if (!infos[i].valid_data || !reader_.is_sample_valid(&samples[i], &infos[i])) return std::nullopt;
    auto copy = samples[i];
    if (!reader_.is_sample_valid(&samples[i], &infos[i])) return std::nullopt;
    return copy;
  }
  LoanableSequence<Sample> samples;
  SampleInfoSeq infos;
 private:
  DataReader& reader_;
  bool borrowed_{false};
};
}
