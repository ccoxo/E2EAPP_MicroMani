/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：提供后端 DDS 控制面：发布状态、接收命令并按请求编号返回应答。
 * 先看：JsonEnvelopeSample → HalCommandRequestSample → HalCommandReplySample → HalTopicDataType。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include "HalDdsControlServer.h"

#include "HalJson.h"
#include "WorkerExceptionBoundary.h"
#include "CommandDeadline.h"

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
#include "LocalDdsTransport.h"
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
  std::thread telemetryWorker;
  std::mutex replyMutex_;
  std::mutex lifecycleMutex_;
  std::atomic_bool workerFailed_{false};

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
    appstation::dds::configureLocalTransport(participantQos);

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
    std::scoped_lock lifecycleLock(lifecycleMutex_);
    if (!enabled || running.load()) {
      return;
    }
    joinWorkers();
    if (workerFailed_.load() && motion_.estopActive()) {
      throw std::runtime_error("DDS worker failure requires emergency-stop acknowledgement before restart");
    }
    workerFailed_.store(false);
    running = true;
    // 命令、急停/租约、遥测各自运行，Tare 等同步命令不能暂停状态发布。
    try {
      worker = std::thread([this]() {
        runWorkerBoundary([this]() { loop(); }, [this](const char* error) { reportWorkerFailure(error); });
      });
      emergencyWorker = std::thread([this]() {
        runWorkerBoundary([this]() { emergencyLoop(); }, [this](const char* error) { reportWorkerFailure(error); });
      });
      telemetryWorker = std::thread([this]() {
        runWorkerBoundary([this]() { telemetryLoop(); }, [this](const char* error) { reportWorkerFailure(error); });
      });
    } catch (...) {
      reportWorkerFailure("DDS control worker could not be created");
      joinWorkers();
      throw;
    }
  }

  void stop() {
    std::scoped_lock lifecycleLock(lifecycleMutex_);
    running = false;
    joinWorkers();
  }

  void joinWorkers() {
    if (telemetryWorker.joinable()) {
      telemetryWorker.join();
    }
    if (emergencyWorker.joinable()) {
      emergencyWorker.join();
    }
    if (worker.joinable()) {
      worker.join();
    }
  }

  void reportWorkerFailure(const char* message) noexcept {
    running.store(false);
    if (workerFailed_.exchange(true)) return;
    motion_.failControlLease();
    motion_.latchEmergencyStop();
    nativeTeleop_.latchControlStop();
    omega_.latchForceStop();
    nativeTeleop_.reportControlFailure(message);
    try { forceRuntime_.recordExternalEmergencyStop("dds_worker_failed", forceMonotonicMilliseconds()); }
    catch (...) { std::fputs("DDS worker fault: force latch update failed\n", stderr); }
    std::fprintf(stderr, "HAL DDS workers stopped: %s\n", message);
  }

  void loop() {
    while (running) {
      if (!pollCommands()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
      }
    }
  }

  void telemetryLoop() {
    auto nextTelemetryAt = std::chrono::steady_clock::now();
    auto nextForceAt = nextTelemetryAt;
    while (running) {
      const auto now = std::chrono::steady_clock::now();
      if (now >= nextTelemetryAt) {
        // 普通遥测 100 Hz、力状态 200 Hz；只有本线程写入这些 topic。
        publishTelemetry();
        nextTelemetryAt = now + std::chrono::milliseconds(10);
      }
      if (now >= nextForceAt) {
        publishForceState();
        nextForceAt = now + std::chrono::milliseconds(5);
      }
      std::this_thread::sleep_until((std::min)(nextTelemetryAt, nextForceAt));
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
      throw std::runtime_error("Fast-DDS HAL command request take failed");
    }
    bool handled = false;
    for (int32_t i = 0; i < samples.length(); ++i) {
      if (!running.load()) break;
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
      throw std::runtime_error("Fast-DDS HAL emergency stop take failed");
    }
    bool handled = false;
    for (int32_t i = 0; i < samples.length(); ++i) {
      if (!running.load()) break;
      if (!infos[i].valid_data) {
        continue;
      }
      handleEmergencyStopCommand(samples[i]);
      handled = true;
    }
    return handled;
  }

  void handleCommand(const HalCommandRequestSample& request) {
    const auto commandEpoch = motion_.commandEpoch();
    HalCommandReplySample reply;
    reply.request_id = request.request_id;
    try {
      const bool stopOrRead = request.name == "motion.emergency_stop"
          || request.name == "motion.disable_side"
          || request.name == "motion.teleop_stop_side"
          || request.name == "teleop.native.stop"
          || request.name == "teleop.native.status"
          || request.name == "force.state"
          || request.name == "omega7.zero_force_feedback"
          || (request.name == "omega7.gravity_compensation"
              && !jsonBoolValue(request.payload_json, "leftEnabled", true)
              && !jsonBoolValue(request.payload_json, "rightEnabled", true));
      if (!stopOrRead && request.stamp_unix_ms <= motion_.lastEmergencyStopUnixMs()) {
        throw std::runtime_error("HAL command predates emergency stop; submit a new request after acknowledgement");
      }
      if (!stopOrRead) ensureCommandNotExpired(request.payload_json, static_cast<double>(unixMs()));
      reply.result_json = commandDispatcher_.handle(
          request.name,
          request.payload_json.empty() ? std::string("{}") : request.payload_json, commandEpoch);
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
      if (request.name == "motion.emergency_stop") {
        reply.result_json = commandDispatcher_.handleEmergencyStop();
      } else if (request.name == "control.lease") {
        // 租约续期必须独立于可能正在回原点的普通命令线程；有效期由 dispatcher 检查。
        reply.result_json = commandDispatcher_.handle(request.name, request.payload_json);
      } else {
        throw std::invalid_argument("command is not allowed on the emergency DDS topic");
      }
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
    // 单参数 write 返回 bool，成功为 true，不能与 RETCODE_OK 比较。
    if (!replyWriter_->write(&reply)) {
      throw std::runtime_error("Fast-DDS HAL command reply publication failed");
    }
  }

  void publishTelemetry() {
      // DDS 遥测仍使用现有 JSON 序列化结果，避免 HTTP 和 DDS 两边字段定义漂移。
      const double uptime =
          std::chrono::duration<double>(std::chrono::steady_clock::now() - started_).count();
      publishJson(healthWriter_, jsonHealth(motion_.health(uptime), omega_.ok(), omega_.lastError()));
      publishJson(motionWriter_, jsonMotionState(motion_.readState()));
      publishJson(omegaWriter_, jsonOmegaState(omega_.readState()));
      publishJson(nativeTeleopWriter_, nativeTeleop_.statusJson());
  }

  void publishForceState() {
      publishJson(
          forceWriter_,
          forceRuntime_.forceStateJson(forceMonotonicMilliseconds()));
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
    if (!writer->write(&sample)) {
      throw std::runtime_error("Fast-DDS HAL telemetry publication failed");
    }
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
