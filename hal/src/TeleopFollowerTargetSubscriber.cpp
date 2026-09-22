/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：订阅 DDS 硬件目标并交给最终执行器，隔离传输与设备访问。
 * 先看：TeleopHardwareTarget → dds::PlainType → TeleopFollowerTargetSubscriber → TargetListener。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include "TeleopFollowerTargetSubscriber.h"

#include "HalJson.h"
#include "TeleopDdsPlainTypes.h"
#include "WorkerExceptionBoundary.h"

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
#include <fastdds/dds/topic/TypeSupport.hpp>
#include "LocalDdsTransport.h"
#include <fastrtps/types/TypesBase.h>

#include <array>
#include <atomic>
#include <mutex>
#include <memory>
#include <stdexcept>

namespace appstation::hal {
namespace {

// Follower 端只消费 hardware-target，不重新解释 Leader JSON，避免两端映射逻辑不一致。
using eprosima::fastdds::dds::DATAREADER_QOS_DEFAULT;
using eprosima::fastdds::dds::DataReader;
using eprosima::fastdds::dds::DataReaderListener;
using eprosima::fastdds::dds::DataReaderQos;
using eprosima::fastdds::dds::DomainParticipant;
using eprosima::fastdds::dds::DomainParticipantFactory;
using eprosima::fastdds::dds::DomainParticipantQos;
using eprosima::fastdds::dds::SUBSCRIBER_QOS_DEFAULT;
using eprosima::fastdds::dds::Subscriber;
using eprosima::fastdds::dds::TOPIC_QOS_DEFAULT;
using eprosima::fastdds::dds::Topic;
using eprosima::fastdds::dds::TypeSupport;
using eprosima::fastrtps::types::ReturnCode_t;

void check(ReturnCode_t code, const char* operation) {
  if (code != ReturnCode_t::RETCODE_OK) throw std::runtime_error(operation);
}

DataReaderQos targetReaderQos() {
  DataReaderQos qos = DATAREADER_QOS_DEFAULT;
  dds::configure(qos, 8);
  return qos;
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
  std::atomic_bool listening{false};
  std::mutex lifecycleMutex;
  std::mutex callbackMutex;
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
    appstation::dds::configureLocalTransport(participantQos);
    participant = DomainParticipantFactory::get_instance()->create_participant(
        static_cast<eprosima::fastdds::dds::DomainId_t>(domainId),
        participantQos);
    if (!participant) throw std::runtime_error("create follower target DDS participant failed");
    targetType = TypeSupport(new dds::PlainType<TeleopHardwareTarget>(dds::kTargetType));
    check(targetType.register_type(participant), "register hardware target type");
    subscriber = participant->create_subscriber(SUBSCRIBER_QOS_DEFAULT);
    topic = participant->create_topic(dds::kTargetTopic, dds::kTargetType, TOPIC_QOS_DEFAULT);
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
    std::scoped_lock lifecycleLock(lifecycleMutex);
    if (!enabled || listening) return;
    listening = true;
    // DataReaderListener 触发后立即 drain 当前批次，减少目标堆积造成的滞后。
    try { check(reader->set_listener(&listener), "attach follower listener"); }
    catch (...) {
      listening = false;
      executor_.reportControlFailure("DDS follower listener could not be attached");
      throw;
    }
  }

  void stop() {
    std::scoped_lock lifecycleLock(lifecycleMutex);
    listening = false;
    // 先撤销回调，再等待正在执行的回调退出；不持 callbackMutex 调 SDK。
    runWorkerBoundary([&]() {
      if (reader) check(reader->set_listener(nullptr), "detach follower listener");
    }, [&](const char* error) { executor_.reportControlFailure(error); });
    std::scoped_lock callbackLock(callbackMutex);
  }

  void handleTargetData(DataReader* dataReader) {
    while (listening.load()) {
      dds::ReadLoan<TeleopHardwareTarget> loan(*dataReader);
      if (loan.samples.length() == 0) return;
      for (int32_t i = 0; i < loan.samples.length(); ++i) {
        if (!listening.load()) return;
        const auto snapshot = loan.snapshot(i);
        if (!snapshot) continue;
        const auto& target = *snapshot;
        executor_.apply(target);
      }
    }
  }
};

void TeleopFollowerTargetSubscriber::Impl::TargetListener::on_data_available(DataReader* reader) {
  const auto failed = [&](const char* error) {
    owner_.listening.store(false);
    owner_.executor_.reportControlFailure(error);
  };
  runWorkerBoundary([&]() {
    std::scoped_lock callbackLock(owner_.callbackMutex);
    // 停止等待也覆盖失败处理本身，防止诊断/关断仍使用 owner 时外层开始析构。
    runWorkerBoundary([&]() {
      if (owner_.listening.load()) owner_.handleTargetData(reader);
    }, failed);
  }, failed);
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
