#include "TeleopDdsPlainTypes.h"
#include "LocalDdsTransport.h"
#include <fastdds/dds/domain/DomainParticipantFactory.hpp>
#include <fastdds/dds/domain/DomainParticipant.hpp>
#include <fastdds/dds/publisher/Publisher.hpp>
#include <fastdds/dds/subscriber/Subscriber.hpp>
#include <fastdds/dds/topic/TypeSupport.hpp>
#include <fastrtps/xmlparser/XMLProfileManager.h>
#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>
#include <vector>

using namespace appstation::hal;
namespace wire = appstation::hal::dds;
using namespace eprosima::fastdds::dds;
using Clock = std::chrono::steady_clock;

namespace {
void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}

template <typename Sample>
class CountedType : public wire::PlainType<Sample> {
 public:
  CountedType(const char* name, bool plain) : wire::PlainType<Sample>(name), plain_(plain) {}
  bool is_bounded() const override { return plain_; }
  bool is_plain() const override { return plain_; }
  bool is_plain(DataRepresentationId_t representation) const override {
    return plain_ && wire::PlainType<Sample>::is_plain(representation);
  }
  bool plain_;
  bool serialize(void* data, eprosima::fastrtps::rtps::SerializedPayload_t* payload) override {
    ++serialized;
    return wire::PlainType<Sample>::serialize(data, payload);
  }
  bool deserialize(eprosima::fastrtps::rtps::SerializedPayload_t* payload, void* data) override {
    ++deserialized;
    return wire::PlainType<Sample>::deserialize(payload, data);
  }
  std::atomic_int serialized{0};
  std::atomic_int deserialized{0};
};

template <typename Sample> void verifyLayout(const Sample& sample) {
  std::array<char, sizeof(Sample) + 4> bytes{};
  eprosima::fastcdr::FastBuffer buffer(bytes.data(), bytes.size());
  eprosima::fastcdr::Cdr cdr(buffer, eprosima::fastcdr::Cdr::LITTLE_ENDIANNESS, eprosima::fastcdr::XCDRv1);
  cdr.serialize_encapsulation();
  wire::writeSample(cdr, sample);
  require(cdr.get_serialized_data_length() == sizeof(Sample) + 4, "plain serialized size differs from sizeof");
  require(std::memcmp(bytes.data() + 4, &sample, sizeof(sample)) == 0, "CDR field offsets differ from native layout");
  Sample decoded{};
  eprosima::fastcdr::FastBuffer readBuffer(bytes.data(), bytes.size());
  eprosima::fastcdr::Cdr reader(readBuffer, eprosima::fastcdr::Cdr::DEFAULT_ENDIAN, eprosima::fastcdr::XCDRv1);
  reader.read_encapsulation();
  wire::readSample(reader, decoded);
  std::array<char, sizeof(Sample) + 4> roundtrip{};
  eprosima::fastcdr::FastBuffer againBuffer(roundtrip.data(), roundtrip.size());
  eprosima::fastcdr::Cdr again(againBuffer, eprosima::fastcdr::Cdr::LITTLE_ENDIANNESS, eprosima::fastcdr::XCDRv1);
  again.serialize_encapsulation();
  wire::writeSample(again, decoded);
  require(bytes == roundtrip, "field round-trip differs");
}

void testLayoutsAndLeaderMapping() {
  // 显式清零 padding；按真实字段序列化后与内存逐字节比较。
  alignas(TeleopHardwareTarget) std::array<std::byte, sizeof(TeleopHardwareTarget)> storage{};
  auto& target = *new (storage.data()) TeleopHardwareTarget();
  target.sequence = 123;
  target.stampUnixMs = 456;
  target.stampMonotonicMs = 789;
  target.side = 1;
  for (int i = 0; i < 6; ++i) {
    target.deltas[i] = i + .25;
    target.enabledAxes[i] = i % 2 == 0;
    target.softLimitMin[i] = -100 - i;
    target.softLimitMax[i] = 100 + i;
  }
  target.translationStepLimitPulse = 100;
  target.rotationStepLimitPulse = 25;
  target.translationPulseDeadband = 1;
  target.rotationPulseDeadband = 2;
  target.syncZeroDeltaTarget = true;
  target.translationVelocityUiPerSec = 50;
  target.rotationVelocityUiPerSec = 5;
  target.translationStartVelocityUiPerSec = 20;
  target.rotationStartVelocityUiPerSec = 2;
  target.accTimeSec = .1;
  target.decTimeSec = .2;
  verifyLayout(target);

  wire::LeaderState leader{};
  leader.stampUnixMs = 123;
  leader.stampMonotonicMs = 456;
  Omega7State hand;
  hand.connected = hand.lastReadOk = hand.clutchPressed = hand.gripperGapAvailable = true;
  hand.pose = {1, 2, 3, 4, 5, 6};
  hand.gripperGap = .003;
  hand.readTimestampMs = 987;
  hand.lastReadError = std::string(300, 'x');
  wire::fillHand(leader.hands[0], hand);
  verifyLayout(leader);
  const auto restored = wire::toHands(leader);
  require(restored[0].connected && restored[0].lastReadOk && restored[0].clutchPressed
      && restored[0].gripperGapAvailable && !restored[0].gripperPressed, "leader flags lost");
  require(restored[0].pose == hand.pose && restored[0].gripperGap == hand.gripperGap
      && restored[0].readTimestampMs == hand.readTimestampMs, "leader control values changed");
  require(restored[0].lastReadError.size() == 255 && !restored[1].connected, "bounded text or disconnected hand broken");
}

template <typename Sample> struct Pair {
  DomainParticipant* writerParticipant{};
  DomainParticipant* readerParticipant{};
  DataWriter* writer{};
  DataReader* reader{};
  CountedType<Sample>* writerType{};
  CountedType<Sample>* readerType{};
  Pair(const char* typeName, bool sharing, bool intraprocess) {
    eprosima::fastrtps::LibrarySettingsAttributes settings;
    settings.intraprocess_delivery = intraprocess ? eprosima::fastrtps::INTRAPROCESS_FULL
        : eprosima::fastrtps::INTRAPROCESS_OFF;
    eprosima::fastrtps::xmlparser::XMLProfileManager::library_settings(settings);
    DomainParticipantQos qos;
    appstation::dds::configureLocalTransport(qos);
    require(!qos.transport().use_builtin_transports && qos.transport().user_transports.size() == 1,
        "unexpected transport configuration");
    require(std::dynamic_pointer_cast<eprosima::fastdds::rtps::SharedMemTransportDescriptor>(
        qos.transport().user_transports.front()) != nullptr, "transport is not SHM");
    auto* factory = DomainParticipantFactory::get_instance();
    // 独立测试 domain，只有合成样本，不构建 HAL/驱动实例。
    writerParticipant = factory->create_participant(191, qos);
    readerParticipant = factory->create_participant(191, qos);
    require(writerParticipant && readerParticipant, "test participant creation failed");
    writerType = new CountedType<Sample>(typeName, sharing);
    readerType = new CountedType<Sample>(typeName, sharing);
    TypeSupport writerSupport(writerType), readerSupport(readerType);
    wire::check(writerSupport.register_type(writerParticipant), "register writer type failed");
    wire::check(readerSupport.register_type(readerParticipant), "register reader type failed");
    const std::string topicName = std::string("AppStation.Test.ZeroCopy.") + typeName;
    auto* wt = writerParticipant->create_topic(topicName, typeName, TOPIC_QOS_DEFAULT);
    auto* rt = readerParticipant->create_topic(topicName, typeName, TOPIC_QOS_DEFAULT);
    auto* pub = writerParticipant->create_publisher(PUBLISHER_QOS_DEFAULT);
    auto* sub = readerParticipant->create_subscriber(SUBSCRIBER_QOS_DEFAULT);
    require(wt && rt && pub && sub, "test entities creation failed");
    DataWriterQos wq;
    DataReaderQos rq;
    wire::configure(wq, 8);
    wire::configure(rq, 8);
    if (!sharing) { wq.data_sharing().off(); rq.data_sharing().off(); }
    writer = pub->create_datawriter(wt, wq);
    reader = sub->create_datareader(rt, rq);
    require(writer && reader, "zero-copy endpoints creation failed");
    const auto deadline = Clock::now() + std::chrono::seconds(5);
    PublicationMatchedStatus matched;
    SubscriptionMatchedStatus readerMatched;
    do {
      writer->get_publication_matched_status(matched);
      reader->get_subscription_matched_status(readerMatched);
      if (matched.current_count > 0 && readerMatched.current_count > 0) return;
      std::this_thread::sleep_for(std::chrono::milliseconds(5));
    } while (Clock::now() < deadline);
    throw std::runtime_error("test discovery timeout");
  }
  ~Pair() {
    auto* factory = DomainParticipantFactory::get_instance();
    for (auto* participant : {readerParticipant, writerParticipant}) {
      if (participant) { participant->delete_contained_entities(); factory->delete_participant(participant); }
    }
  }
};

template <typename Sample> void runTransfer(const char* name, bool sharing, bool intraprocess) {
  Pair<Sample> pair(name, sharing, intraprocess);
  std::vector<double> latency;
  for (std::uint64_t i = 1; i <= 300; ++i) {
    // 超过资源池大小反复注入填充异常，验证 writer loan 不泄漏。
    if (sharing && i <= 32) {
      bool rejected = false;
      try { wire::publish<Sample>(*pair.writer, [](Sample&) { throw std::runtime_error("injected fill failure"); }); }
      catch (const std::runtime_error&) { rejected = true; }
      require(rejected, "fill exception did not propagate");
    }
    const auto sent = Clock::now();
    const auto fill = [&](Sample& sample) {
      sample = Sample{};
      sample.stampUnixMs = i;
      sample.stampMonotonicMs = i * 3;
    };
    if (sharing) wire::publish<Sample>(*pair.writer, fill);
    else { Sample sample; fill(sample); require(pair.writer->write(&sample), "baseline write failed"); }
    const auto deadline = Clock::now() + std::chrono::seconds(1);
    while (pair.reader->get_unread_count() == 0 && Clock::now() < deadline) std::this_thread::yield();
    require(pair.reader->get_unread_count() > 0, "sample receive timeout");
    if (sharing) {
      wire::ReadLoan<Sample> loan(*pair.reader);
      require(!loan.samples.has_ownership(), "reader copied instead of loaning");
      require(loan.samples.length() == 1, "unexpected sample count");
      const auto sample = loan.snapshot(0);
      require(sample && sample->stampUnixMs == i && sample->stampMonotonicMs == i * 3, "loan payload mismatch");
    } else {
      LoanableSequence<Sample> samples(16);
      SampleInfoSeq infos(16);
      wire::check(pair.reader->take(samples, infos, 16), "baseline take failed");
      require(samples.length() == 1 && samples[0].stampUnixMs == i, "baseline payload mismatch");
    }
    if (i > 20) latency.push_back(std::chrono::duration<double, std::micro>(Clock::now() - sent).count());
  }
  if (sharing) require(pair.writerType->serialized == 0 && pair.readerType->deserialized == 0,
      "data-sharing unexpectedly serialized or deserialized samples");
  else require(pair.writerType->serialized > 0 && pair.readerType->deserialized > 0,
      "SHM transport did not exercise serialization/copy path");
  std::sort(latency.begin(), latency.end());
  std::cout << name << " sharing=" << sharing << " intraprocess=" << intraprocess
      << " n=" << latency.size() << " p50_us=" << latency[latency.size() / 2]
      << " p95_us=" << latency[latency.size() * 95 / 100]
      << " serialize=" << pair.writerType->serialized << " deserialize=" << pair.readerType->deserialized << '\n';
}
}

int main(int argc, char** argv) {
  try {
    testLayoutsAndLeaderMapping();
    const int mode = argc > 1 ? std::stoi(argv[1]) : 2;
    require(mode >= 0 && mode <= 3, "unknown test mode");
    if (mode == 0) runTransfer<TeleopHardwareTarget>(wire::kTargetType, false, false);
    if (mode == 1) runTransfer<TeleopHardwareTarget>(wire::kTargetType, true, false);
    if (mode == 2) runTransfer<TeleopHardwareTarget>(wire::kTargetType, true, true);
    if (mode == 3) runTransfer<wire::LeaderState>(wire::kLeaderType, true, true);
    std::cout << "TeleopDdsZeroCopyTests passed (real DDS, synthetic data, no hardware)\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "TeleopDdsZeroCopyTests failed: " << error.what() << '\n';
    return 1;
  }
}
