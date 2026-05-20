#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <string>
#include <thread>

#include "HalTypes.h"
#include "JodellGripperDriver.h"
#include "LTDMCDriver.h"
#include "Omega7Driver.h"

namespace appstation::hal {

struct NativeTeleopConfig {
  std::string controlMode{"incremental_position"};
  std::string mappingMode{"direct"};
  int loopHz{100};
  bool swapTeleopChannels{true};
  bool requireClutch{false};
  bool leftGravityCompensation{true};
  bool rightGravityCompensation{true};

  std::array<double, 2> translationScale{{1.0, 1.0}};
  std::array<double, 2> rotationScale{{1.0, 1.0}};
  std::array<std::array<double, 6>, 2> axisOutputScale{{
      {0.40, 0.25, 0.25, 0.40, 0.20, 0.20},
      {0.40, 0.25, 0.25, 0.40, 0.20, 0.20},
  }};
  std::array<std::array<double, 6>, 2> impulseCoeff{{
      {-5000000.0, 10000000.0, -10000000.0, 1667.0, -2500.0, -333.3333},
      {-5000000.0, -10000000.0, -10000000.0, 1667.0, 2500.0, 3333.333},
  }};
  std::array<std::array<bool, 6>, 2> enabledAxes{{
      {true, true, true, true, true, true},
      {true, true, true, true, true, true},
  }};
  std::array<std::array<AxisLimit, 6>, 2> softLimits{};
  std::array<std::array<double, 6>, 2> workOriginPulse{};
  std::array<bool, 2> workOriginValid{{false, false}};

  double translationStepLimitPulse{4000.0};
  double rotationStepLimitPulse{1250.0};
  double translationPulseDeadband{2.0};
  double rotationPulseDeadband{2.0};
  double translationStartVelocityUmS{600.0};
  double translationMaxVelocityUmS{8000.0};
  double rotationStartVelocityDegS{1.0};
  double rotationMaxVelocityDegS{12.0};
  double accTimeSec{0.05};
  double decTimeSec{0.05};

  double nativeTranslationDeadzoneM{0.002};
  double nativeTranslationFullScaleM{0.040};
  double nativeRotationDeadzoneDeg{2.0};
  double nativeRotationFullScaleDeg{30.0};
  double nativeVelocitySmoothingMs{40.0};
  double translationDeadzoneM{0.00002};
  double rotationDeadzoneDeg{0.03};
  double incrementalTranslationMinEffectiveDeltaM{0.000025};
  double incrementalTranslationReverseDeadzoneM{0.00005};
  bool continuousIncrementMode{true};
  double translationInputEpsilonM{0.00002};
  double rotationInputEpsilonDeg{0.03};
  double translationMinActivePulse{3.0};
  double rotationMinActivePulse{3.0};
  int continuousMicroConfirmTicks{0};

  JodellGripperConfig gripper{};
  bool gripperTeleopEnabled{true};
  std::array<double, 2> gripperGapMinMm{{0.0, 0.0}};
  std::array<double, 2> gripperGapMaxMm{{25.0, 25.0}};
  std::array<bool, 2> gripperGapInvert{{false, false}};
  std::array<std::string, 2> gripperSourceHand{{"PhysicalRight", "PhysicalLeft"}};
  int gripperDeadbandCounts{1};
  double gripperMinCommandIntervalMs{20.0};
  bool gripperButtonFallback{true};
};

struct NativeTeleopAction {
  std::int64_t ts{};
  double monotonicS{};
  Side side{Side::Left};
  Side sourceSide{Side::Left};
  int axisIndex{};
  double delta{};
  std::array<double, 6> deltas{};
  std::array<double, 12> deltaVector{};
  std::array<double, 6> requestedDeltaPulse{};
  std::array<double, 6> appliedDeltaPulse{};
  std::array<double, 6> targetPulse{};
  std::array<double, 6> currentPulse{};
  std::array<double, 6> launchDeltaPulse{};
  std::array<double, 6> updateReturn{};
  std::array<bool, 6> movingBefore{};
  std::array<bool, 6> moveStarted{};
  std::array<bool, 6> clipped{};
};

class NativeTeleopController {
 public:
  NativeTeleopController(LTDMCDriver& motion, Omega7Driver& omega, JodellGripperDriver& gripper);
  ~NativeTeleopController();

  void configure(const NativeTeleopConfig& config);
  void start(bool leftConnected, bool rightConnected);
  void stop();
  std::string statusJson() const;
  bool running() const;

 private:
  struct PendingGripperCommand {
    bool pending{};
    int targetIndex{};
    Side side{Side::Left};
    double targetMm{};
    int speed{};
    int torque{};
  };

  void loop();
  void startGripperWorker();
  void stopGripperWorker();
  void gripperLoop();
  void tick(double dtSec);
  void tickSideBestEffort(int sourceIndex, const Omega7State& hand, double dtSec);
  void tickSide(int sourceIndex, const Omega7State& hand, double dtSec);
  void tickGrippers(const std::array<Omega7State, 2>& hands);
  void enqueueGripperCommand(int targetIndex, Side side, double targetMm, int speed, int torque);
  void setBlockerUnlocked(int sourceIndex, const std::string& state, const std::string& message);
  void recordActionUnlocked(Side sourceSide, Side targetSide, const TeleopTargetUpdateResult& result);
  void recordZeroStopActionUnlocked(Side sourceSide, Side targetSide);
  void syncIncrementalZeroDeltaUnlocked(
      Side sourceSide,
      Side targetSide,
      int sourceIndex,
      int targetIndex,
      const std::array<double, 6>& semanticPose,
      const std::string& message);
  std::array<double, 6> velocityDeltasUi(
      int sourceIndex,
      Side targetSide,
      const std::array<double, 6>& pose,
      double dtSec);
  std::array<double, 6> incrementalDeltasUi(int sourceIndex, Side targetSide, const std::array<double, 6>& pose);
  long applyContinuousPulseGate(int sourceIndex, int axisIndex, long requestedPulse, double requestedPulseFloat);
  double mappedDirection(int sourceIndex, Side targetSide, int axisIndex) const;
  std::array<AxisLimit, 6> effectiveSoftLimits(Side targetSide, int targetIndex) const;
  int gripperSourceIndex(int targetIndex) const;
  Side sideFromIndex(int index) const;
  int sideIndex(Side side) const;

  LTDMCDriver& motion_;
  Omega7Driver& omega_;
  JodellGripperDriver& gripper_;
  mutable std::mutex mutex_;
  NativeTeleopConfig config_{};
  std::atomic<bool> running_{false};
  std::thread worker_;
  std::array<bool, 2> logicalConnected_{{false, false}};
  std::array<bool, 2> targetActive_{{false, false}};
  std::array<std::array<double, 6>, 2> referencePose_{};
  std::array<bool, 2> referenceValid_{{false, false}};
  std::array<std::array<double, 6>, 2> velocityUiPerSec_{};
  std::array<std::array<double, 6>, 2> incrementalCarry_{};
  std::array<std::array<int, 6>, 2> incrementalDirection_{};
  std::array<bool, 2> incrementalInputActive_{{false, false}};
  std::array<std::array<double, 6>, 2> continuousPulseCarry_{};
  std::array<std::array<int, 6>, 2> continuousDirection_{};
  std::array<std::array<int, 6>, 2> continuousStreak_{};
  std::array<std::array<double, 6>, 2> lastSemanticPose_{};
  std::array<std::array<double, 6>, 2> lastRawDelta_{};
  std::array<std::array<double, 6>, 2> lastFilteredDelta_{};
  std::array<std::array<double, 6>, 2> lastRequestedPulse_{};
  std::array<std::array<double, 6>, 2> lastEmittedPulse_{};
  std::array<std::array<double, 6>, 2> lastOutputDeltaUi_{};
  std::array<Side, 2> lastDiagnosticTargetSide_{{Side::Right, Side::Left}};
  std::array<std::string, 2> blockerState_{{"idle", "idle"}};
  std::array<std::string, 2> blockerMessage_{};
  std::string lastError_;
  NativeTeleopAction lastAction_{};
  bool hasLastAction_{false};
  std::deque<NativeTeleopAction> actionHistory_;
  std::array<int, 2> gripperLastRaw_{{-1, -1}};
  std::array<std::chrono::steady_clock::time_point, 2> gripperLastCommandAt_{};
  std::array<double, 2> gripperTargetsMm_{{0.0, 0.0}};
  std::array<bool, 2> gripperLastCommandOk_{{false, false}};
  std::array<std::string, 2> gripperLastMessage_{};
  std::array<std::int64_t, 2> gripperLastCommandTs_{{0, 0}};
  std::mutex gripperMutex_;
  std::condition_variable gripperCv_;
  std::thread gripperWorker_;
  std::atomic<bool> gripperWorkerRunning_{false};
  std::array<PendingGripperCommand, 2> pendingGripperCommands_{};
};

}  // namespace appstation::hal
