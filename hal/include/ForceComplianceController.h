#pragma once

#include <array>
#include <cstdint>
#include <string>

namespace appstation::hal {

struct ForceComplianceSideConfig {
  bool mappingConfirmed{false};
  std::array<double, 4> matrix{{1.0, 0.0, 0.0, 1.0}};
  std::array<double, 2> deadbandN{};
  std::array<double, 2> gainUmPerNs{};
  std::array<double, 2> maxStepUm{};
  std::array<double, 2> maxOffsetUm{};
};

struct ForceComplianceConfig {
  bool enabled{false};
  std::array<ForceComplianceSideConfig, 2> sides{};
};

struct ForceComplianceResult {
  bool active{false};
  double dtSec{0.0};
  std::array<double, 2> mappedN{};
  std::array<double, 2> deadbandedN{};
  std::array<double, 2> requestedUm{};
  std::array<double, 2> correctionUm{};
  std::array<std::string, 2> clipReason{};
};

class ForceComplianceController {
 public:
  void configure(const ForceComplianceConfig& config);
  ForceComplianceResult correction(
      int side,
      const std::array<double, 6>& force,
      bool sampleFresh,
      bool safetyLatched,
      std::uint64_t targetMonotonicMs);
  void commit(
      int side,
      const std::array<double, 2>& requestedUm,
      const std::array<double, 2>& actualUm);
  void reset();
  void resetSide(int side);
  std::array<double, 2> cumulativeOffset(int side) const;
  const ForceComplianceConfig& config() const;

 private:
  ForceComplianceConfig config_{};
  std::array<std::array<double, 2>, 2> cumulativeOffsetUm_{};
  std::array<std::uint64_t, 2> lastTargetMs_{};
  std::array<bool, 2> lastTargetValid_{{false, false}};
};

}  // namespace appstation::hal
