/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：订阅主手状态，调用原生映射算法，再发布 HardwareTarget。
 * 先看：dds::LeaderState → TeleopHardwareTarget → dds::PlainType → TeleopMappingNode。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include "TeleopMappingNode.h"

#include "HalJson.h"
#include "TeleopDdsPlainTypes.h"
#include "WorkerExceptionBoundary.h"

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
#include <fastdds/dds/topic/TypeSupport.hpp>
#include "LocalDdsTransport.h"
#include <fastrtps/types/TypesBase.h>

#include <array>
#include <atomic>
#include <mutex>
#include <memory>
#include <stdexcept>
#include <string>

namespace appstation::hal {
namespace {

// 主手状态和硬件目标均使用有界固定布局，允许 DataSharing。
// 字段顺序必须和 hal/dds/appstation_hal.idl 保持一致。
using eprosima::fastdds::dds::DATAREADER_QOS_DEFAULT;
using eprosima::fastdds::dds::DATAWRITER_QOS_DEFAULT;
using eprosima::fastdds::dds::DataReader;
using eprosima::fastdds::dds::DataReaderListener;
using eprosima::fastdds::dds::DataReaderQos;
using eprosima::fastdds::dds::DataWriter;
using eprosima::fastdds::dds::DataWriterQos;
using eprosima::fastdds::dds::DomainParticipant;
using eprosima::fastdds::dds::DomainParticipantFactory;
using eprosima::fastdds::dds::DomainParticipantQos;
using eprosima::fastdds::dds::PUBLISHER_QOS_DEFAULT;
using eprosima::fastdds::dds::Publisher;
using eprosima::fastdds::dds::SUBSCRIBER_QOS_DEFAULT;
using eprosima::fastdds::dds::Subscriber;
using eprosima::fastdds::dds::TOPIC_QOS_DEFAULT;
using eprosima::fastdds::dds::Topic;
using eprosima::fastdds::dds::TypeSupport;
using eprosima::fastrtps::types::ReturnCode_t;

void check(ReturnCode_t code, const char* operation) {
  if (code != ReturnCode_t::RETCODE_OK) throw std::runtime_error(operation);
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
  std::atomic_bool listening{false};
  std::mutex lifecycleMutex;
  std::mutex callbackMutex;
  DomainParticipant* participant{nullptr};
  Subscriber* subscriber{nullptr};
  Publisher* publisher{nullptr};
  TypeSupport leaderType;
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
    appstation::dds::configureLocalTransport(participantQos);
    participant = DomainParticipantFactory::get_instance()->create_participant(
        static_cast<eprosima::fastdds::dds::DomainId_t>(domainId),
        participantQos);
    if (!participant) throw std::runtime_error("create teleop mapping DDS participant failed");

    leaderType = TypeSupport(new dds::PlainType<dds::LeaderState>(dds::kLeaderType));
    targetType = TypeSupport(new dds::PlainType<TeleopHardwareTarget>(dds::kTargetType));
    check(leaderType.register_type(participant), "register mapping leader type");
    check(targetType.register_type(participant), "register mapping target type");

    subscriber = participant->create_subscriber(SUBSCRIBER_QOS_DEFAULT);
    publisher = participant->create_publisher(PUBLISHER_QOS_DEFAULT);
    leaderTopic = participant->create_topic(dds::kLeaderTopic, dds::kLeaderType, TOPIC_QOS_DEFAULT);
    targetTopic = participant->create_topic(dds::kTargetTopic, dds::kTargetType, TOPIC_QOS_DEFAULT);
    if (!subscriber || !publisher || !leaderTopic || !targetTopic) {
      throw std::runtime_error("create teleop mapping DDS entities failed");
    }
    DataReaderQos readerQos = DATAREADER_QOS_DEFAULT;
    dds::configure(readerQos, 1);
    leaderReader = subscriber->create_datareader(leaderTopic, readerQos);
    DataWriterQos writerQos = DATAWRITER_QOS_DEFAULT;
    dds::configure(writerQos, 8);
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
    std::scoped_lock lifecycleLock(lifecycleMutex);
    if (!enabled || listening) return;
    listening = true;
    // listener 只负责唤醒读取，实际映射仍交给 NativeTeleopController。
    try { check(leaderReader->set_listener(&listener), "attach mapping listener"); }
    catch (...) {
      listening = false;
      nativeTeleop_.reportControlFailure("DDS mapping listener could not be attached");
      throw;
    }
  }

  void stop() {
    std::scoped_lock lifecycleLock(lifecycleMutex);
    listening = false;
    runWorkerBoundary([&]() {
      if (leaderReader) check(leaderReader->set_listener(nullptr), "detach mapping listener");
    }, [&](const char* error) { nativeTeleop_.reportControlFailure(error); });
    std::scoped_lock callbackLock(callbackMutex);
  }

  void handleLeaderData(DataReader* reader) {
    while (listening.load()) {
      dds::ReadLoan<dds::LeaderState> loan(*reader);
      if (loan.samples.length() == 0) return;
      for (int32_t i = 0; i < loan.samples.length(); ++i) {
        if (!listening.load()) return;
        const auto snapshot = loan.snapshot(i);
        if (!snapshot) continue;
        const auto& sample = *snapshot;
        // 用 Leader 的单调时间戳估算 dt，时间戳缺失或回退时给控制器一个保守默认值。
        const double dtSec = lastLeaderMonotonicMs > 0 && sample.stampMonotonicMs > lastLeaderMonotonicMs
            ? static_cast<double>(sample.stampMonotonicMs - lastLeaderMonotonicMs) / 1000.0
            : 0.01;
        lastLeaderMonotonicMs = sample.stampMonotonicMs;
        nativeTeleop_.processLeaderState(dds::toHands(sample), dtSec);
      }
    }
  }

  void publishHardwareTarget(const TeleopHardwareTarget& target) {
    if (!enabled || !targetWriter_) return;
    dds::publish<TeleopHardwareTarget>(*targetWriter_, [&](TeleopHardwareTarget& sample) {
      sample = target;
    });
  }
};

void TeleopMappingNode::Impl::LeaderListener::on_data_available(DataReader* reader) {
  runWorkerBoundary([&]() {
    std::scoped_lock callbackLock(owner_.callbackMutex);
    try { if (owner_.listening.load()) owner_.handleLeaderData(reader); }
    catch (const std::exception& error) { owner_.nativeTeleop_.reportControlFailure(error.what()); }
    catch (...) { owner_.nativeTeleop_.reportControlFailure("unknown C++ exception in DDS leader callback"); }
  }, [&](const char* error) { owner_.nativeTeleop_.reportControlFailure(error); });
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
