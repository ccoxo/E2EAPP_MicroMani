#include "HalDdsControlServer.h"

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
#include <fastdds/dds/subscriber/SampleInfo.hpp>
#include <fastdds/dds/subscriber/Subscriber.hpp>
#include <fastdds/dds/subscriber/qos/DataReaderQos.hpp>
#include <fastdds/dds/topic/Topic.hpp>
#include <fastdds/dds/topic/TopicDataType.hpp>
#include <fastdds/dds/topic/TypeSupport.hpp>
#include <fastdds/rtps/common/SerializedPayload.h>
#include <fastdds/rtps/transport/UDPv4TransportDescriptor.h>
#include <fastrtps/types/TypesBase.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

namespace appstation::hal {
namespace {

// 这些运行时 TopicDataType 按字段镜像 IDL，避免轻量 HAL 构建额外依赖生成的 C++ 源码。
using eprosima::fastdds::dds::BEST_EFFORT_RELIABILITY_QOS;
using eprosima::fastdds::dds::DATAREADER_QOS_DEFAULT;
using eprosima::fastdds::dds::DATAWRITER_QOS_DEFAULT;
using eprosima::fastdds::dds::DataReader;
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
using eprosima::fastdds::dds::RELIABLE_RELIABILITY_QOS;
using eprosima::fastdds::dds::SampleInfoSeq;
using eprosima::fastdds::dds::SUBSCRIBER_QOS_DEFAULT;
using eprosima::fastdds::dds::Subscriber;
using eprosima::fastdds::dds::TOPIC_QOS_DEFAULT;
using eprosima::fastdds::dds::TRANSIENT_LOCAL_DURABILITY_QOS;
using eprosima::fastdds::dds::Topic;
using eprosima::fastdds::dds::TopicDataType;
using eprosima::fastdds::dds::TypeSupport;
using eprosima::fastdds::dds::VOLATILE_DURABILITY_QOS;
using eprosima::fastrtps::types::ReturnCode_t;

constexpr const char* kJsonEnvelopeType = "appstation.JsonEnvelope";
constexpr const char* kCommandRequestType = "appstation.HalCommandRequest";
constexpr const char* kCommandReplyType = "appstation.HalCommandReply";

constexpr const char* kTopicHealth = "AppStation.Hal.Health";
constexpr const char* kTopicMotionState = "AppStation.Hal.MotionState";
constexpr const char* kTopicOmegaState = "AppStation.Hal.OmegaState";
constexpr const char* kTopicNativeTeleopStatus = "AppStation.Hal.NativeTeleopStatus";
constexpr const char* kTopicForceState = "AppStation.Hal.ForceState";
constexpr const char* kTopicCommandRequest = "AppStation.Hal.CommandRequest";
constexpr const char* kTopicCommandReply = "AppStation.Hal.CommandReply";
constexpr const char* kTopicEmergencyStop = "AppStation.Hal.EmergencyStop";

struct JsonEnvelopeSample {
  std::uint64_t stamp_unix_ms{0};
  std::uint64_t stamp_monotonic_ms{0};
  std::string source;
  std::string payload_json;
};

struct HalCommandRequestSample {
  std::string request_id;
  std::uint64_t stamp_unix_ms{0};
  std::string name;
  std::string payload_json;
};

struct HalCommandReplySample {
  std::string request_id;
  bool ok{false};
  std::string result_json;
  std::string error;
};

std::uint64_t unixMs() {
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(now).count());
}

std::uint64_t monotonicMs() {
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(now).count());
}

std::uint32_t stringPayloadSize(const std::string& value) {
  return static_cast<std::uint32_t>(value.size() + 8);
}

std::uint32_t estimatedSerializedSize(const JsonEnvelopeSample& sample) {
  return 64 + stringPayloadSize(sample.source) + stringPayloadSize(sample.payload_json);
}

std::uint32_t estimatedSerializedSize(const HalCommandRequestSample& sample) {
  return 64 + stringPayloadSize(sample.request_id) + stringPayloadSize(sample.name) + stringPayloadSize(sample.payload_json);
}

std::uint32_t estimatedSerializedSize(const HalCommandReplySample& sample) {
  return 64 + stringPayloadSize(sample.request_id) + stringPayloadSize(sample.result_json) + stringPayloadSize(sample.error);
}

void writeSample(eprosima::fastcdr::Cdr& cdr, const JsonEnvelopeSample& sample) {
  cdr << sample.stamp_unix_ms;
  cdr << sample.stamp_monotonic_ms;
  cdr << sample.source;
  cdr << sample.payload_json;
}

void writeSample(eprosima::fastcdr::Cdr& cdr, const HalCommandRequestSample& sample) {
  cdr << sample.request_id;
  cdr << sample.stamp_unix_ms;
  cdr << sample.name;
  cdr << sample.payload_json;
}

void writeSample(eprosima::fastcdr::Cdr& cdr, const HalCommandReplySample& sample) {
  cdr << sample.request_id;
  cdr << sample.ok;
  cdr << sample.result_json;
  cdr << sample.error;
}

void readSample(eprosima::fastcdr::Cdr& cdr, JsonEnvelopeSample& sample) {
  cdr >> sample.stamp_unix_ms;
  cdr >> sample.stamp_monotonic_ms;
  cdr >> sample.source;
  cdr >> sample.payload_json;
}

void readSample(eprosima::fastcdr::Cdr& cdr, HalCommandRequestSample& sample) {
  cdr >> sample.request_id;
  cdr >> sample.stamp_unix_ms;
  cdr >> sample.name;
  cdr >> sample.payload_json;
}

void readSample(eprosima::fastcdr::Cdr& cdr, HalCommandReplySample& sample) {
  cdr >> sample.request_id;
  cdr >> sample.ok;
  cdr >> sample.result_json;
  cdr >> sample.error;
}

template <typename Sample>
class HalTopicDataType final : public TopicDataType {
 public:
  explicit HalTopicDataType(const char* typeName, std::uint32_t typeSize = 1024 * 1024) {
    setName(typeName);
    m_typeSize = typeSize;
    m_isGetKeyDefined = false;
    auto_fill_type_object(false);
    auto_fill_type_information(false);
  }

  bool serialize(void* data, eprosima::fastrtps::rtps::SerializedPayload_t* payload) override {
    auto* sample = static_cast<Sample*>(data);
    try {
      // Fast-CDR 会直接写 DDS payload 缓冲区，缓冲区偏小时会失败，所以先按保守估算预留。
      payload->reserve(estimatedSerializedSize(*sample));
      eprosima::fastcdr::FastBuffer buffer(reinterpret_cast<char*>(payload->data), payload->max_size);
      eprosima::fastcdr::Cdr cdr(buffer, eprosima::fastcdr::Cdr::DEFAULT_ENDIAN, eprosima::fastcdr::XCDRv1);
      payload->encapsulation = 0x0001;
      cdr.serialize_encapsulation();
      writeSample(cdr, *sample);
      payload->length = static_cast<std::uint32_t>(cdr.get_serialized_data_length());
      return true;
    } catch (const std::exception& exc) {
      std::cerr << "Fast-DDS HAL control serialize failed: " << exc.what() << "\n";
      return false;
    }
  }

  bool deserialize(eprosima::fastrtps::rtps::SerializedPayload_t* payload, void* data) override {
    auto* sample = static_cast<Sample*>(data);
    try {
      eprosima::fastcdr::FastBuffer buffer(reinterpret_cast<char*>(payload->data), payload->length);
      eprosima::fastcdr::Cdr cdr(buffer, eprosima::fastcdr::Cdr::DEFAULT_ENDIAN, eprosima::fastcdr::XCDRv1);
      cdr.read_encapsulation();
      readSample(cdr, *sample);
      return true;
    } catch (const std::exception& exc) {
      std::cerr << "Fast-DDS HAL control deserialize failed: " << exc.what() << "\n";
      return false;
    }
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

void configureWriterHistory(DataWriterQos& qos, int depth) {
  qos.history().kind = KEEP_LAST_HISTORY_QOS;
  qos.history().depth = depth;
  qos.resource_limits().max_samples = depth;
  qos.resource_limits().max_instances = 1;
  qos.resource_limits().max_samples_per_instance = depth;
  qos.resource_limits().allocated_samples = depth;
  qos.representation().m_value.clear();
  qos.representation().m_value.push_back(DataRepresentationId_t::XCDR_DATA_REPRESENTATION);
}

void configureReaderHistory(DataReaderQos& qos, int depth) {
  qos.history().kind = KEEP_LAST_HISTORY_QOS;
  qos.history().depth = depth;
  qos.resource_limits().max_samples = depth;
  qos.resource_limits().max_instances = 1;
  qos.resource_limits().max_samples_per_instance = depth;
  qos.resource_limits().allocated_samples = depth;
  qos.type_consistency().representation.m_value.clear();
  qos.type_consistency().representation.m_value.push_back(DataRepresentationId_t::XCDR_DATA_REPRESENTATION);
}

DataWriterQos telemetryWriterQos(bool reliable, bool transientLocal, int depth) {
  DataWriterQos qos = DATAWRITER_QOS_DEFAULT;
  configureWriterHistory(qos, depth);
  qos.reliability().kind = reliable ? RELIABLE_RELIABILITY_QOS : BEST_EFFORT_RELIABILITY_QOS;
  qos.durability().kind = transientLocal ? TRANSIENT_LOCAL_DURABILITY_QOS : VOLATILE_DURABILITY_QOS;
  return qos;
}

DataReaderQos commandRequestReaderQos() {
  DataReaderQos qos = DATAREADER_QOS_DEFAULT;
  configureReaderHistory(qos, 32);
  qos.reliability().kind = RELIABLE_RELIABILITY_QOS;
  qos.durability().kind = VOLATILE_DURABILITY_QOS;
  return qos;
}

DataWriterQos commandReplyWriterQos() {
  DataWriterQos qos = DATAWRITER_QOS_DEFAULT;
  configureWriterHistory(qos, 32);
  qos.reliability().kind = RELIABLE_RELIABILITY_QOS;
  qos.durability().kind = VOLATILE_DURABILITY_QOS;
  return qos;
}

}  // namespace

struct HalDdsControlServer::Impl {
  HalCommandDispatcher& commandDispatcher_;
  LTDMCDriver& motion_;
  Omega7Driver& omega_;
  NativeTeleopController& nativeTeleop_;
  ForceControlRuntime& forceRuntime_;
  const std::chrono::steady_clock::time_point& started_;
  bool enabled{false};
  std::atomic<bool> running{false};
  DomainParticipant* participant{nullptr};
  Publisher* publisher{nullptr};
  Subscriber* subscriber{nullptr};
  TypeSupport jsonType;
  TypeSupport commandRequestType;
  TypeSupport commandReplyType;
  Topic* healthTopic{nullptr};
  Topic* motionTopic{nullptr};
  Topic* omegaTopic{nullptr};
  Topic* nativeTeleopTopic{nullptr};
  Topic* forceTopic{nullptr};
  Topic* commandRequestTopic{nullptr};
  Topic* commandReplyTopic{nullptr};
  Topic* emergencyStopTopic{nullptr};
  DataWriter* healthWriter_{nullptr};
  DataWriter* motionWriter_{nullptr};
  DataWriter* omegaWriter_{nullptr};
  DataWriter* nativeTeleopWriter_{nullptr};
  DataWriter* forceWriter_{nullptr};
  DataWriter* replyWriter_{nullptr};
  DataReader* commandReader_{nullptr};
  DataReader* emergencyStopReader_{nullptr};
  std::thread worker;
  std::thread emergencyWorker;
  std::mutex replyMutex_;

  Impl(
      HalCommandDispatcher& commandDispatcher,
      LTDMCDriver& motion,
      Omega7Driver& omega,
      NativeTeleopController& nativeTeleop,
      ForceControlRuntime& forceRuntime,
      const std::chrono::steady_clock::time_point& started)
      : commandDispatcher_(commandDispatcher),
        motion_(motion),
        omega_(omega),
        nativeTeleop_(nativeTeleop),
        forceRuntime_(forceRuntime),
        started_(started),
        enabled(envBoolValue("APPSTATION_HAL_DDS_ENABLED", true)) {
    if (!enabled) {
      return;
    }
    initialize();
  }

  ~Impl() {
    stop();
    if (participant) {
      (void)participant->delete_contained_entities();
      (void)DomainParticipantFactory::get_instance()->delete_participant(participant);
    }
  }

  void initialize() {
    const int domainId = envIntValue("APPSTATION_DDS_DOMAIN_ID", 42);
    DomainParticipantQos participantQos;
    check(DomainParticipantFactory::get_instance()->get_default_participant_qos(participantQos), "get participant qos");
    participantQos.name("AppStationHalDdsControlServer");
    if (!envBoolValue("APPSTATION_DDS_LAN_DISCOVERY", false)) {
      // 默认只在本机发现 DDS 实体，避免现场工作站把控制面广播到局域网。
      auto udp = std::make_shared<eprosima::fastdds::rtps::UDPv4TransportDescriptor>();
      udp->interfaceWhiteList.push_back("127.0.0.1");
      participantQos.transport().use_builtin_transports = false;
      participantQos.transport().user_transports.push_back(udp);
    }

    participant = DomainParticipantFactory::get_instance()->create_participant(
        static_cast<eprosima::fastdds::dds::DomainId_t>(domainId),
        participantQos);
    if (!participant) {
      throw std::runtime_error("create HAL DDS control participant failed");
    }

    jsonType = TypeSupport(new HalTopicDataType<JsonEnvelopeSample>(kJsonEnvelopeType));
    commandRequestType = TypeSupport(new HalTopicDataType<HalCommandRequestSample>(kCommandRequestType));
    commandReplyType = TypeSupport(new HalTopicDataType<HalCommandReplySample>(kCommandReplyType));
    check(jsonType.register_type(participant), "register HAL control json type");
    check(commandRequestType.register_type(participant), "register HAL command request type");
    check(commandReplyType.register_type(participant), "register HAL command reply type");

    publisher = participant->create_publisher(PUBLISHER_QOS_DEFAULT);
    subscriber = participant->create_subscriber(SUBSCRIBER_QOS_DEFAULT);
    if (!publisher || !subscriber) {
      throw std::runtime_error("create HAL DDS control publisher/subscriber failed");
    }

    healthTopic = createTopic(kTopicHealth, kJsonEnvelopeType);
    motionTopic = createTopic(kTopicMotionState, kJsonEnvelopeType);
    omegaTopic = createTopic(kTopicOmegaState, kJsonEnvelopeType);
    nativeTeleopTopic = createTopic(kTopicNativeTeleopStatus, kJsonEnvelopeType);
    forceTopic = createTopic(kTopicForceState, kJsonEnvelopeType);
    commandRequestTopic = createTopic(kTopicCommandRequest, kCommandRequestType);
    commandReplyTopic = createTopic(kTopicCommandReply, kCommandReplyType);
    emergencyStopTopic = createTopic(kTopicEmergencyStop, kCommandRequestType);

    healthWriter_ = publisher->create_datawriter(healthTopic, telemetryWriterQos(true, true, 1));
    motionWriter_ = publisher->create_datawriter(motionTopic, telemetryWriterQos(false, false, 1));
    omegaWriter_ = publisher->create_datawriter(omegaTopic, telemetryWriterQos(false, false, 1));
    nativeTeleopWriter_ = publisher->create_datawriter(nativeTeleopTopic, telemetryWriterQos(false, false, 1));
    forceWriter_ = publisher->create_datawriter(forceTopic, telemetryWriterQos(false, false, 1));
    replyWriter_ = publisher->create_datawriter(commandReplyTopic, commandReplyWriterQos());
    commandReader_ = subscriber->create_datareader(commandRequestTopic, commandRequestReaderQos());
    emergencyStopReader_ = subscriber->create_datareader(emergencyStopTopic, commandRequestReaderQos());
    if (!healthWriter_ || !motionWriter_ || !omegaWriter_ || !nativeTeleopWriter_ || !forceWriter_
        || !replyWriter_ || !commandReader_
        || !emergencyStopReader_) {
      throw std::runtime_error("create HAL DDS control readers/writers failed");
    }
  }

  Topic* createTopic(const char* topicName, const char* typeName) {
    Topic* topic = participant->create_topic(topicName, typeName, TOPIC_QOS_DEFAULT);
    if (!topic) {
      throw std::runtime_error(std::string("create HAL DDS topic failed: ") + topicName);
    }
    return topic;
  }

  void start() {
    if (!enabled || worker.joinable()) {
      return;
    }
    running = true;
    // 普通命令和遥测共用 5 ms 级轮询；急停单独线程缩短响应路径。
    worker = std::thread([this]() { loop(); });
    emergencyWorker = std::thread([this]() { emergencyLoop(); });
  }

  void stop() {
    running = false;
    if (emergencyWorker.joinable()) {
      emergencyWorker.join();
    }
    if (worker.joinable()) {
      worker.join();
    }
  }

  void loop() {
    auto nextTelemetryAt = std::chrono::steady_clock::now();
    auto nextForceAt = nextTelemetryAt;
    while (running) {
      const bool handledCommand = pollCommands();
      const auto now = std::chrono::steady_clock::now();
      if (now >= nextTelemetryAt) {
        // 遥测以 100 Hz 发布，控制命令有数据时优先被处理。
        publishTelemetry();
        nextTelemetryAt = now + std::chrono::milliseconds(10);
      }
      if (now >= nextForceAt) {
        publishForceState();
        nextForceAt = now + std::chrono::milliseconds(5);
      }
      if (!handledCommand) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
      }
    }
  }

  void emergencyLoop() {
    while (running) {
      if (!pollEmergencyStops()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      }
    }
  }

  bool pollCommands() {
    // take 会把当前批次样本从 reader 中移走，处理完再写同 request_id 的 reply。
    eprosima::fastdds::dds::LoanableSequence<HalCommandRequestSample> samples(16);
    SampleInfoSeq infos(16);
    const auto result = commandReader_->take(samples, infos, 16);
    if (result == ReturnCode_t::RETCODE_NO_DATA) {
      return false;
    }
    if (result != ReturnCode_t::RETCODE_OK) {
      std::cerr << "Fast-DDS HAL command request take failed\n";
      return false;
    }
    bool handled = false;
    for (int32_t i = 0; i < samples.length(); ++i) {
      if (!infos[i].valid_data) {
        continue;
      }
      handleCommand(samples[i]);
      handled = true;
    }
    return handled;
  }

  bool pollEmergencyStops() {
    // 急停 topic 也带 request_id，这样上层仍能收到明确的应答。
    eprosima::fastdds::dds::LoanableSequence<HalCommandRequestSample> samples(16);
    SampleInfoSeq infos(16);
    const auto result = emergencyStopReader_->take(samples, infos, 16);
    if (result == ReturnCode_t::RETCODE_NO_DATA) {
      return false;
    }
    if (result != ReturnCode_t::RETCODE_OK) {
      std::cerr << "Fast-DDS HAL emergency stop take failed\n";
      return false;
    }
    bool handled = false;
    for (int32_t i = 0; i < samples.length(); ++i) {
      if (!infos[i].valid_data) {
        continue;
      }
      handleEmergencyStopCommand(samples[i]);
      handled = true;
    }
    return handled;
  }

  void handleCommand(const HalCommandRequestSample& request) {
    HalCommandReplySample reply;
    reply.request_id = request.request_id;
    try {
      reply.result_json = commandDispatcher_.handle(
          request.name,
          request.payload_json.empty() ? std::string("{}") : request.payload_json);
      reply.ok = true;
      reply.error.clear();
    } catch (const std::exception& exc) {
      reply.ok = false;
      reply.result_json = "{}";
      reply.error = exc.what();
    }
    writeReply(reply);
  }

  void handleEmergencyStopCommand(const HalCommandRequestSample& request) {
    HalCommandReplySample reply;
    reply.request_id = request.request_id;
    try {
      reply.result_json = commandDispatcher_.handleEmergencyStop();
      reply.ok = true;
      reply.error.clear();
    } catch (const std::exception& exc) {
      reply.ok = false;
      reply.result_json = "{}";
      reply.error = exc.what();
    }
    writeReply(reply);
  }

  void writeReply(HalCommandReplySample& reply) {
    std::scoped_lock lock(replyMutex_);
    (void)replyWriter_->write(&reply);
  }

  void publishTelemetry() {
    try {
      // DDS 遥测仍使用现有 JSON 序列化结果，避免 HTTP 和 DDS 两边字段定义漂移。
      const double uptime =
          std::chrono::duration<double>(std::chrono::steady_clock::now() - started_).count();
      publishJson(healthWriter_, jsonHealth(motion_.health(uptime), omega_.ok(), omega_.lastError()));
      publishJson(motionWriter_, jsonMotionState(motion_.readState()));
      publishJson(omegaWriter_, jsonOmegaState(omega_.readState()));
      publishJson(nativeTeleopWriter_, nativeTeleop_.statusJson());
    } catch (const std::exception& exc) {
      std::cerr << "Fast-DDS HAL telemetry publish failed: " << exc.what() << "\n";
    }
  }

  void publishForceState() {
    try {
      publishJson(
          forceWriter_,
          forceRuntime_.forceStateJson(forceMonotonicMilliseconds()));
    } catch (const std::exception& exc) {
      std::cerr << "Fast-DDS HAL force telemetry publish failed: " << exc.what() << "\n";
    }
  }

  void publishJson(DataWriter* writer, const std::string& payloadJson) {
    if (!writer) {
      return;
    }
    JsonEnvelopeSample sample;
    sample.stamp_unix_ms = unixMs();
    sample.stamp_monotonic_ms = monotonicMs();
    sample.source = "hal-cpp";
    sample.payload_json = payloadJson;
    (void)writer->write(&sample);
  }
};

HalDdsControlServer::HalDdsControlServer(
    HalCommandDispatcher& commandDispatcher,
    LTDMCDriver& motion,
    Omega7Driver& omega,
    NativeTeleopController& nativeTeleop,
    ForceControlRuntime& forceRuntime,
    const std::chrono::steady_clock::time_point& started)
    : impl_(std::make_unique<Impl>(
          commandDispatcher,
          motion,
          omega,
          nativeTeleop,
          forceRuntime,
          started)) {}

HalDdsControlServer::~HalDdsControlServer() = default;

bool HalDdsControlServer::enabled() const {
  return impl_->enabled;
}

void HalDdsControlServer::start() {
  impl_->start();
}

void HalDdsControlServer::stop() {
  impl_->stop();
}

}  // namespace appstation::hal
