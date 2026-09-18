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

#include <atomic>
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

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
using eprosima::fastdds::dds::SUBSCRIBER_QOS_DEFAULT;
using eprosima::fastdds::dds::SampleInfoSeq;
using eprosima::fastdds::dds::Subscriber;
using eprosima::fastdds::dds::TOPIC_QOS_DEFAULT;
using eprosima::fastdds::dds::TRANSIENT_LOCAL_DURABILITY_QOS;
using eprosima::fastdds::dds::Topic;
using eprosima::fastdds::dds::TopicDataType;
using eprosima::fastdds::dds::TypeSupport;
using eprosima::fastdds::dds::VOLATILE_DURABILITY_QOS;
using eprosima::fastrtps::types::ReturnCode_t;

namespace {

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

// C ABI 返回码：Python 通过 ctypes 读取这些值，不暴露 C++ 异常或 STL 类型。
constexpr int kResultNoData = 0;
constexpr int kResultOk = 1;
constexpr int kResultError = -1;
constexpr int kResultBufferTooSmall = -2;

enum TopicId {
  kHealth = 0,
  kMotion = 1,
  kOmega = 2,
  kNativeTeleop = 3,
  kForce = 4,
};

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

bool envBoolValue(const char* key, bool fallback) {
  const char* raw = std::getenv(key);
  if (!raw || !*raw) {
    return fallback;
  }
  std::string value(raw);
  for (char& ch : value) {
    ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
  }
  return value == "1" || value == "true" || value == "yes" || value == "on";
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

// 字段顺序必须和 hal/dds/appstation_hal.idl 以及 HAL C++ Fast-DDS bridge 一致。
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
class AppStationTopicDataType final : public TopicDataType {
 public:
  explicit AppStationTopicDataType(const char* typeName, std::uint32_t typeSize = 1024 * 1024) {
    setName(typeName);
    // Fast-DDS writer 创建时会按 m_typeSize 初始化 payload pool；动态 JSON 字符串不能填 0。
    m_typeSize = typeSize;
    m_isGetKeyDefined = false;
    auto_fill_type_object(false);
    auto_fill_type_information(false);
  }

  bool serialize(void* data, eprosima::fastrtps::rtps::SerializedPayload_t* payload) override {
    auto* sample = static_cast<Sample*>(data);
    try {
      payload->reserve(estimatedSerializedSize(*sample));
      eprosima::fastcdr::FastBuffer buffer(reinterpret_cast<char*>(payload->data), payload->max_size);
      eprosima::fastcdr::Cdr cdr(buffer, eprosima::fastcdr::Cdr::DEFAULT_ENDIAN, eprosima::fastcdr::XCDRv1);
      payload->encapsulation = 0x0001;
      cdr.serialize_encapsulation();
      writeSample(cdr, *sample);
      payload->length = static_cast<std::uint32_t>(cdr.get_serialized_data_length());
      return true;
    } catch (...) {
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
    } catch (...) {
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

void configureHistory(DataWriterQos& qos, int depth) {
  qos.history().kind = KEEP_LAST_HISTORY_QOS;
  qos.history().depth = depth;
  qos.resource_limits().max_samples = depth;
  qos.resource_limits().max_instances = 1;
  qos.resource_limits().max_samples_per_instance = depth;
  qos.resource_limits().allocated_samples = depth;
  qos.representation().m_value.clear();
  qos.representation().m_value.push_back(DataRepresentationId_t::XCDR_DATA_REPRESENTATION);
}

void configureHistory(DataReaderQos& qos, int depth) {
  qos.history().kind = KEEP_LAST_HISTORY_QOS;
  qos.history().depth = depth;
  qos.resource_limits().max_samples = depth;
  qos.resource_limits().max_instances = 1;
  qos.resource_limits().max_samples_per_instance = depth;
  qos.resource_limits().allocated_samples = depth;
  qos.type_consistency().representation.m_value.clear();
  qos.type_consistency().representation.m_value.push_back(DataRepresentationId_t::XCDR_DATA_REPRESENTATION);
}

DataReaderQos telemetryReaderQos(bool reliable, bool transientLocal, int depth) {
  DataReaderQos qos = DATAREADER_QOS_DEFAULT;
  configureHistory(qos, depth);
  qos.reliability().kind = reliable ? RELIABLE_RELIABILITY_QOS : BEST_EFFORT_RELIABILITY_QOS;
  qos.durability().kind = transientLocal ? TRANSIENT_LOCAL_DURABILITY_QOS : VOLATILE_DURABILITY_QOS;
  return qos;
}

DataWriterQos commandRequestWriterQos() {
  DataWriterQos qos = DATAWRITER_QOS_DEFAULT;
  configureHistory(qos, 32);
  qos.reliability().kind = RELIABLE_RELIABILITY_QOS;
  qos.durability().kind = VOLATILE_DURABILITY_QOS;
  return qos;
}

DataReaderQos commandReplyReaderQos() {
  DataReaderQos qos = DATAREADER_QOS_DEFAULT;
  configureHistory(qos, 32);
  qos.reliability().kind = RELIABLE_RELIABILITY_QOS;
  qos.durability().kind = VOLATILE_DURABILITY_QOS;
  return qos;
}

void check(ReturnCode_t code, const char* operation) {
  if (code != ReturnCode_t::RETCODE_OK) {
    throw std::runtime_error(std::string(operation) + " failed");
  }
}

void setError(char* buffer, int capacity, const std::string& message) {
  if (!buffer || capacity <= 0) {
    return;
  }
  const auto count = (std::min)(static_cast<std::size_t>(capacity - 1), message.size());
  std::memcpy(buffer, message.data(), count);
  buffer[count] = '\0';
}

int copyString(const std::string& value, char* buffer, int capacity, int* required) {
  const int need = static_cast<int>(value.size() + 1);
  if (required) {
    *required = need;
  }
  if (!buffer || capacity < need) {
    return kResultBufferTooSmall;
  }
  std::memcpy(buffer, value.c_str(), static_cast<std::size_t>(need));
  return kResultOk;
}

struct AppStationFastDdsTransport {
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
  DataReader* healthReader{nullptr};
  DataReader* motionReader{nullptr};
  DataReader* omegaReader{nullptr};
  DataReader* nativeTeleopReader{nullptr};
  DataReader* forceReader{nullptr};
  DataReader* commandReplyReader{nullptr};
  DataWriter* commandRequestWriter{nullptr};
  DataWriter* emergencyStopWriter{nullptr};
  std::atomic<bool> running{false};
  std::thread readerThread;
  std::mutex mutex;
  std::condition_variable replyCv;
  std::map<int, JsonEnvelopeSample> latest;
  std::map<std::string, HalCommandReplySample> replies;

  explicit AppStationFastDdsTransport(int domainId) {
    initialize(domainId);
  }

  ~AppStationFastDdsTransport() {
    close();
    if (participant) {
      (void)participant->delete_contained_entities();
      (void)DomainParticipantFactory::get_instance()->delete_participant(participant);
    }
  }

  void initialize(int domainId) {
    DomainParticipantQos participantQos;
    check(DomainParticipantFactory::get_instance()->get_default_participant_qos(participantQos), "get_default_participant_qos");
    participantQos.name("AppStationBackendFastDds");
    // 默认 localhost-only，和 HAL C++ participant 保持相同安全边界。
    if (!envBoolValue("APPSTATION_DDS_LAN_DISCOVERY", false)) {
      auto udp = std::make_shared<eprosima::fastdds::rtps::UDPv4TransportDescriptor>();
      udp->interfaceWhiteList.push_back("127.0.0.1");
      participantQos.transport().use_builtin_transports = false;
      participantQos.transport().user_transports.push_back(udp);
    }

    participant = DomainParticipantFactory::get_instance()->create_participant(
        static_cast<eprosima::fastdds::dds::DomainId_t>(domainId),
        participantQos);
    if (!participant) {
      throw std::runtime_error("create Fast-DDS participant failed");
    }

    jsonType = TypeSupport(new AppStationTopicDataType<JsonEnvelopeSample>(kJsonEnvelopeType));
    commandRequestType = TypeSupport(new AppStationTopicDataType<HalCommandRequestSample>(kCommandRequestType));
    commandReplyType = TypeSupport(new AppStationTopicDataType<HalCommandReplySample>(kCommandReplyType));
    check(jsonType.register_type(participant), "register JsonEnvelope type");
    check(commandRequestType.register_type(participant), "register HalCommandRequest type");
    check(commandReplyType.register_type(participant), "register HalCommandReply type");

    publisher = participant->create_publisher(PUBLISHER_QOS_DEFAULT);
    subscriber = participant->create_subscriber(SUBSCRIBER_QOS_DEFAULT);
    if (!publisher || !subscriber) {
      throw std::runtime_error("create Fast-DDS publisher/subscriber failed");
    }

    healthTopic = createTopic(kTopicHealth, kJsonEnvelopeType);
    motionTopic = createTopic(kTopicMotionState, kJsonEnvelopeType);
    omegaTopic = createTopic(kTopicOmegaState, kJsonEnvelopeType);
    nativeTeleopTopic = createTopic(kTopicNativeTeleopStatus, kJsonEnvelopeType);
    forceTopic = createTopic(kTopicForceState, kJsonEnvelopeType);
    commandRequestTopic = createTopic(kTopicCommandRequest, kCommandRequestType);
    commandReplyTopic = createTopic(kTopicCommandReply, kCommandReplyType);
    emergencyStopTopic = createTopic(kTopicEmergencyStop, kCommandRequestType);

    healthReader = subscriber->create_datareader(healthTopic, telemetryReaderQos(true, true, 1));
    motionReader = subscriber->create_datareader(motionTopic, telemetryReaderQos(false, false, 1));
    omegaReader = subscriber->create_datareader(omegaTopic, telemetryReaderQos(false, false, 1));
    nativeTeleopReader = subscriber->create_datareader(nativeTeleopTopic, telemetryReaderQos(false, false, 1));
    forceReader = subscriber->create_datareader(forceTopic, telemetryReaderQos(false, false, 1));
    commandReplyReader = subscriber->create_datareader(commandReplyTopic, commandReplyReaderQos());
    commandRequestWriter = publisher->create_datawriter(commandRequestTopic, commandRequestWriterQos());
    emergencyStopWriter = publisher->create_datawriter(emergencyStopTopic, commandRequestWriterQos());
    if (!healthReader || !motionReader || !omegaReader || !nativeTeleopReader || !forceReader || !commandReplyReader
        || !commandRequestWriter || !emergencyStopWriter) {
      throw std::runtime_error("create Fast-DDS readers/writer failed");
    }
  }

  Topic* createTopic(const char* topicName, const char* typeName) {
    Topic* topic = participant->create_topic(topicName, typeName, TOPIC_QOS_DEFAULT);
    if (!topic) {
      throw std::runtime_error(std::string("create Fast-DDS topic failed: ") + topicName);
    }
    return topic;
  }

  void start() {
    bool expected = false;
    if (!running.compare_exchange_strong(expected, true)) {
      return;
    }
    readerThread = std::thread([this]() { readLoop(); });
  }

  void close() {
    running = false;
    replyCv.notify_all();
    if (readerThread.joinable()) {
      readerThread.join();
    }
  }

  void readLoop() {
    while (running) {
      bool updated = false;
      updated = pollTelemetry(kHealth, healthReader) || updated;
      updated = pollTelemetry(kMotion, motionReader) || updated;
      updated = pollTelemetry(kOmega, omegaReader) || updated;
      updated = pollTelemetry(kNativeTeleop, nativeTeleopReader) || updated;
      updated = pollTelemetry(kForce, forceReader) || updated;
      updated = pollReplies() || updated;
      std::this_thread::sleep_for(updated ? std::chrono::milliseconds(2) : std::chrono::milliseconds(10));
    }
  }

  bool pollTelemetry(int topicId, DataReader* reader) {
    eprosima::fastdds::dds::LoanableSequence<JsonEnvelopeSample> samples(16);
    SampleInfoSeq infos(16);
    const auto result = reader->take(samples, infos, 16);
    if (result == ReturnCode_t::RETCODE_NO_DATA) {
      return false;
    }
    if (result != ReturnCode_t::RETCODE_OK) {
      return false;
    }
    bool updated = false;
    std::lock_guard<std::mutex> lock(mutex);
    for (int32_t i = 0; i < samples.length(); ++i) {
      if (infos[i].valid_data) {
        latest[topicId] = samples[i];
        updated = true;
      }
    }
    return updated;
  }

  bool pollReplies() {
    eprosima::fastdds::dds::LoanableSequence<HalCommandReplySample> samples(16);
    SampleInfoSeq infos(16);
    const auto result = commandReplyReader->take(samples, infos, 16);
    if (result == ReturnCode_t::RETCODE_NO_DATA) {
      return false;
    }
    if (result != ReturnCode_t::RETCODE_OK) {
      return false;
    }
    bool updated = false;
    {
      std::lock_guard<std::mutex> lock(mutex);
      for (int32_t i = 0; i < samples.length(); ++i) {
        if (infos[i].valid_data) {
          replies[samples[i].request_id] = samples[i];
          updated = true;
        }
      }
    }
    if (updated) {
      replyCv.notify_all();
    }
    return updated;
  }
};

}  // namespace

extern "C" {

__declspec(dllexport) const char* appstation_fastdds_version() {
  return "appstation-fastdds-transport/0.1";
}

__declspec(dllexport) AppStationFastDdsTransport* appstation_fastdds_create(int domainId, char* error, int errorCapacity) {
  try {
    return new AppStationFastDdsTransport(domainId);
  } catch (const std::exception& exc) {
    setError(error, errorCapacity, exc.what());
    return nullptr;
  }
}

__declspec(dllexport) int appstation_fastdds_start(AppStationFastDdsTransport* handle, char* error, int errorCapacity) {
  if (!handle) {
    setError(error, errorCapacity, "Fast-DDS transport handle is null");
    return kResultError;
  }
  try {
    handle->start();
    return kResultOk;
  } catch (const std::exception& exc) {
    setError(error, errorCapacity, exc.what());
    return kResultError;
  }
}

__declspec(dllexport) void appstation_fastdds_close(AppStationFastDdsTransport* handle) {
  if (handle) {
    handle->close();
  }
}

__declspec(dllexport) void appstation_fastdds_destroy(AppStationFastDdsTransport* handle) {
  delete handle;
}

__declspec(dllexport) int appstation_fastdds_get_latest(
    AppStationFastDdsTransport* handle,
    int topicId,
    std::uint64_t* stampUnixMs,
    std::uint64_t* stampMonotonicMs,
    char* source,
    int sourceCapacity,
    int* sourceRequired,
    char* payloadJson,
    int payloadCapacity,
    int* payloadRequired,
    char* error,
    int errorCapacity) {
  if (!handle) {
    setError(error, errorCapacity, "Fast-DDS transport handle is null");
    return kResultError;
  }
  std::lock_guard<std::mutex> lock(handle->mutex);
  const auto found = handle->latest.find(topicId);
  if (found == handle->latest.end()) {
    return kResultNoData;
  }
  const JsonEnvelopeSample& sample = found->second;
  if (stampUnixMs) {
    *stampUnixMs = sample.stamp_unix_ms;
  }
  if (stampMonotonicMs) {
    *stampMonotonicMs = sample.stamp_monotonic_ms;
  }
  const int sourceResult = copyString(sample.source, source, sourceCapacity, sourceRequired);
  const int payloadResult = copyString(sample.payload_json, payloadJson, payloadCapacity, payloadRequired);
  if (sourceResult == kResultBufferTooSmall || payloadResult == kResultBufferTooSmall) {
    return kResultBufferTooSmall;
  }
  return kResultOk;
}

__declspec(dllexport) int appstation_fastdds_publish_command_request(
    AppStationFastDdsTransport* handle,
    const char* requestId,
    std::uint64_t stampUnixMs,
    const char* name,
    const char* payloadJson,
    char* error,
    int errorCapacity) {
  if (!handle) {
    setError(error, errorCapacity, "Fast-DDS transport handle is null");
    return kResultError;
  }
  try {
    HalCommandRequestSample sample;
    sample.request_id = requestId ? requestId : "";
    sample.stamp_unix_ms = stampUnixMs;
    sample.name = name ? name : "";
    sample.payload_json = payloadJson ? payloadJson : "{}";
    const bool written = handle->commandRequestWriter->write(&sample);
    if (!written) {
      setError(error, errorCapacity, "Fast-DDS command request write failed");
      return kResultError;
    }
    return kResultOk;
  } catch (const std::exception& exc) {
    setError(error, errorCapacity, exc.what());
    return kResultError;
  }
}

__declspec(dllexport) int appstation_fastdds_publish_emergency_stop(
    AppStationFastDdsTransport* handle,
    const char* requestId,
    std::uint64_t stampUnixMs,
    const char* name,
    const char* payloadJson,
    char* error,
    int errorCapacity) {
  if (!handle) {
    setError(error, errorCapacity, "Fast-DDS transport handle is null");
    return kResultError;
  }
  try {
    HalCommandRequestSample sample;
    sample.request_id = requestId ? requestId : "";
    sample.stamp_unix_ms = stampUnixMs;
    sample.name = name ? name : "";
    sample.payload_json = payloadJson ? payloadJson : "{}";
    const bool written = handle->emergencyStopWriter->write(&sample);
    if (!written) {
      setError(error, errorCapacity, "Fast-DDS emergency stop write failed");
      return kResultError;
    }
    return kResultOk;
  } catch (const std::exception& exc) {
    setError(error, errorCapacity, exc.what());
    return kResultError;
  }
}

__declspec(dllexport) int appstation_fastdds_wait_for_command_reply(
    AppStationFastDdsTransport* handle,
    const char* requestId,
    int timeoutMs,
    int* ok,
    char* resultJson,
    int resultCapacity,
    int* resultRequired,
    char* replyError,
    int replyErrorCapacity,
    int* replyErrorRequired,
    char* error,
    int errorCapacity) {
  if (!handle) {
    setError(error, errorCapacity, "Fast-DDS transport handle is null");
    return kResultError;
  }
  const std::string key = requestId ? requestId : "";
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds((std::max)(timeoutMs, 0));
  std::unique_lock<std::mutex> lock(handle->mutex);
  while (handle->running) {
    const auto found = handle->replies.find(key);
    if (found != handle->replies.end()) {
      const HalCommandReplySample& reply = found->second;
      if (ok) {
        *ok = reply.ok ? 1 : 0;
      }
      const int resultCopy = copyString(reply.result_json, resultJson, resultCapacity, resultRequired);
      const int errorCopy = copyString(reply.error, replyError, replyErrorCapacity, replyErrorRequired);
      if (resultCopy == kResultBufferTooSmall || errorCopy == kResultBufferTooSmall) {
        return kResultBufferTooSmall;
      }
      handle->replies.erase(found);
      return kResultOk;
    }
    if (handle->replyCv.wait_until(lock, deadline) == std::cv_status::timeout) {
      return kResultNoData;
    }
  }
  return kResultNoData;
}

}  // extern "C"
