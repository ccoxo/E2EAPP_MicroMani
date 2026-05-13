#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#endif

#include <array>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

#include "LTDMCDriver.h"
#include "MotionControlThread.h"
#include "Omega7Driver.h"

namespace {

long long unixTimeMs() {
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
}

long long timestampOrNow(long long value) {
  return value > 0 ? value : unixTimeMs();
}

std::string jsonEscape(const std::string& value) {
  // HAL 直接拼接小型 JSON，因此所有外部字符串都先做转义。
  std::ostringstream out;
  for (char ch : value) {
    switch (ch) {
      case '"':
        out << "\\\"";
        break;
      case '\\':
        out << "\\\\";
        break;
      case '\n':
        out << "\\n";
        break;
      case '\r':
        out << "\\r";
        break;
      case '\t':
        out << "\\t";
        break;
      default:
        out << ch;
        break;
    }
  }
  return out.str();
}

std::string jsonHealth(const appstation::hal::HalHealth& motionHealth, bool omegaOk, const std::string& message) {
  std::ostringstream out;
  out << "{\"ltdmc_ok\":" << (motionHealth.ltdmcOk ? "true" : "false")
      << ",\"omega7_ok\":" << (omegaOk ? "true" : "false")
      << ",\"version\":\"" << motionHealth.version << "\""
      << ",\"uptime_s\":" << motionHealth.uptimeS
      << ",\"message\":\"" << jsonEscape(message) << "\"}";
  return out.str();
}

std::string jsonMotionState(const appstation::hal::MotionState& state) {
  std::ostringstream out;
  out << "{\"timestamp_ms\":" << timestampOrNow(state.readTimestampMs)
      << ",\"estop_active\":" << (state.estopActive ? "true" : "false") << ",\"positions\":[";
  for (size_t i = 0; i < state.axes.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << state.axes[i].uiPosition;
  }
  out << "],\"pulses\":[";
  for (size_t i = 0; i < state.axes.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << state.axes[i].pulse;
  }
  out << "],\"enabled\":[";
  for (size_t i = 0; i < state.axes.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << (state.axes[i].enabled ? "true" : "false");
  }
  out << "]}";
  return out.str();
}

std::string jsonOmegaState(const std::array<appstation::hal::Omega7State, 2>& state) {
  std::ostringstream out;
  out << "{\"timestamp_ms\":" << timestampOrNow(state[0].readTimestampMs) << ",\"hands\":[";
  for (size_t i = 0; i < state.size(); ++i) {
    const auto& hand = state[i];
    if (i > 0) {
      out << ",";
    }
    out << "{\"side\":\"" << (i == 0 ? "left" : "right") << "\""
        << ",\"connected\":" << (hand.connected ? "true" : "false")
        << ",\"calibrated\":" << (hand.calibrated ? "true" : "false")
        << ",\"openId\":" << hand.openId
        << ",\"deviceId\":" << hand.deviceId
        << ",\"serial\":\"" << jsonEscape(hand.serial) << "\""
        << ",\"systemName\":\"" << jsonEscape(hand.systemName) << "\"";
    if (hand.handednessKnown) {
      out << ",\"leftHanded\":" << (hand.leftHanded ? "true" : "false");
    } else {
      out << ",\"leftHanded\":null";
    }
    out << ",\"pose\":[";
    for (size_t axis = 0; axis < hand.pose.size(); ++axis) {
      if (axis > 0) {
        out << ",";
      }
      out << hand.pose[axis];
    }
    out << "]"
        << ",\"clutchPressed\":" << (hand.clutchPressed ? "true" : "false")
        << ",\"gripperPressed\":" << (hand.gripperPressed ? "true" : "false");
    if (hand.gripperGapAvailable) {
      out << ",\"gripperGapMm\":" << hand.gripperGap * 1000.0;
    } else {
      out << ",\"gripperGapMm\":null";
    }
    out << ",\"lastReadOk\":" << (hand.lastReadOk ? "true" : "false")
        << ",\"message\":\"" << jsonEscape(hand.lastReadError) << "\"}";
  }
  out << "]}";
  return out.str();
}

std::string requestBody(const std::string& request) {
  // 该服务器只处理简单 HTTP 请求，body 从 header 分隔符之后截取。
  const auto marker = request.find("\r\n\r\n");
  if (marker == std::string::npos) {
    return {};
  }
  return request.substr(marker + 4);
}

std::string lowercase(std::string value) {
  for (auto& ch : value) {
    ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
  }
  return value;
}

size_t contentLength(const std::string& request) {
  // keep-alive 场景下需要根据 Content-Length 判断请求体是否收完整。
  const auto headerEnd = request.find("\r\n\r\n");
  if (headerEnd == std::string::npos) {
    return 0;
  }
  const auto headers = lowercase(request.substr(0, headerEnd));
  const auto marker = std::string("content-length:");
  const auto markerPos = headers.find(marker);
  if (markerPos == std::string::npos) {
    return 0;
  }
  const auto valueStart = markerPos + marker.size();
  char* end = nullptr;
  const auto value = std::strtol(headers.c_str() + valueStart, &end, 10);
  return value > 0 ? static_cast<size_t>(value) : 0;
}

bool wantsClose(const std::string& request) {
  const auto headerEnd = request.find("\r\n\r\n");
  if (headerEnd == std::string::npos) {
    return true;
  }
  const auto headers = lowercase(request.substr(0, headerEnd));
  const auto marker = std::string("connection:");
  const auto markerPos = headers.find(marker);
  if (markerPos == std::string::npos) {
    // HTTP/1.1 未显式声明 Connection 时默认保持 keep-alive。
    return false;
  }
  auto valueStart = markerPos + marker.size();
  while (valueStart < headers.size() && std::isspace(static_cast<unsigned char>(headers[valueStart]))) {
    ++valueStart;
  }
  return headers.compare(valueStart, 5, "close") == 0;
}

#ifdef _WIN32
// 从 client 读取一个 HTTP/1.1 请求；对端未发送新请求就关闭时返回空字符串。
std::string readHttpRequest(SOCKET client) {
  std::string request;
  std::array<char, 4096> buffer{};
  while (true) {
    const int bytes = recv(client, buffer.data(), static_cast<int>(buffer.size()), 0);
    if (bytes == 0) {
      return {};  // 对端正常关闭
    }
    if (bytes < 0) {
      if (request.empty()) {
        return {};
      }
      break;
    }
    request.append(buffer.data(), static_cast<size_t>(bytes));
    const auto headerEnd = request.find("\r\n\r\n");
    if (headerEnd != std::string::npos) {
      const auto length = contentLength(request);
      const auto bodyStart = headerEnd + 4;
      if (length == 0 || request.size() >= bodyStart + length) {
        break;
      }
    }
    if (request.size() > 65536) {
      throw std::runtime_error("HTTP request too large");
    }
  }
  return request;
}
#endif

std::string jsonStringValue(const std::string& body, const std::string& key) {
  const auto marker = std::string("\"") + key + "\"";
  auto pos = body.find(marker);
  if (pos == std::string::npos) {
    return {};
  }
  pos = body.find(':', pos + marker.size());
  if (pos == std::string::npos) {
    return {};
  }
  pos = body.find('"', pos);
  if (pos == std::string::npos) {
    return {};
  }
  const auto end = body.find('"', pos + 1);
  if (end == std::string::npos) {
    return {};
  }
  return body.substr(pos + 1, end - pos - 1);
}

double jsonNumberValue(const std::string& body, const std::string& key, double fallback) {
  const auto marker = std::string("\"") + key + "\"";
  auto pos = body.find(marker);
  if (pos == std::string::npos) {
    return fallback;
  }
  pos = body.find(':', pos + marker.size());
  if (pos == std::string::npos) {
    return fallback;
  }
  const char* start = body.c_str() + pos + 1;
  char* end = nullptr;
  const double value = std::strtod(start, &end);
  return end == start ? fallback : value;
}

bool jsonBoolValue(const std::string& body, const std::string& key, bool fallback) {
  const auto marker = std::string("\"") + key + "\"";
  auto pos = body.find(marker);
  if (pos == std::string::npos) {
    return fallback;
  }
  pos = body.find(':', pos + marker.size());
  if (pos == std::string::npos) {
    return fallback;
  }
  ++pos;
  while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
    ++pos;
  }
  if (body.compare(pos, 4, "true") == 0) {
    return true;
  }
  if (body.compare(pos, 5, "false") == 0) {
    return false;
  }
  return fallback;
}

bool jsonNumberArrayValue(const std::string& body, const std::string& key, size_t index, double* out) {
  const auto marker = std::string("\"") + key + "\"";
  auto pos = body.find(marker);
  if (pos == std::string::npos) {
    return false;
  }
  pos = body.find('[', pos + marker.size());
  if (pos == std::string::npos) {
    return false;
  }
  ++pos;
  for (size_t current = 0; current <= index; ++current) {
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
      ++pos;
    }
    const char* start = body.c_str() + pos;
    char* end = nullptr;
    const double value = std::strtod(start, &end);
    if (end == start) {
      return false;
    }
    if (current == index) {
      *out = value;
      return true;
    }
    pos = static_cast<size_t>(end - body.c_str());
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
      ++pos;
    }
    if (pos >= body.size() || body[pos] != ',') {
      return false;
    }
    ++pos;
  }
  return false;
}

bool jsonBoolArrayValue(const std::string& body, const std::string& key, size_t index, bool* out) {
  const auto marker = std::string("\"") + key + "\"";
  auto pos = body.find(marker);
  if (pos == std::string::npos) {
    return false;
  }
  pos = body.find('[', pos + marker.size());
  if (pos == std::string::npos) {
    return false;
  }
  ++pos;
  for (size_t current = 0; current <= index; ++current) {
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
      ++pos;
    }
    bool value = true;
    size_t valueLength = 0;
    if (body.compare(pos, 4, "true") == 0) {
      value = true;
      valueLength = 4;
    } else if (body.compare(pos, 5, "false") == 0) {
      value = false;
      valueLength = 5;
    } else {
      return false;
    }
    if (current == index) {
      *out = value;
      return true;
    }
    pos += valueLength;
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
      ++pos;
    }
    if (pos >= body.size() || body[pos] != ',') {
      return false;
    }
    ++pos;
  }
  return false;
}

std::array<double, 12> jsonWorkOriginPulse(const std::string& body) {
  std::array<double, 12> pulses{};
  for (size_t i = 0; i < 6; ++i) {
    if (!jsonNumberArrayValue(body, "leftPulse", i, &pulses[i])) {
      throw std::runtime_error("home_all requires leftPulse[6] work origin payload");
    }
    if (!jsonNumberArrayValue(body, "rightPulse", i, &pulses[i + 6])) {
      throw std::runtime_error("home_all requires rightPulse[6] work origin payload");
    }
  }
  return pulses;
}

std::array<appstation::hal::AxisLimit, 6> jsonTeleopSoftLimits(const std::string& body) {
  std::array<appstation::hal::AxisLimit, 6> limits{};
  for (size_t i = 0; i < limits.size(); ++i) {
    if (!jsonNumberArrayValue(body, "softLimitMin", i, &limits[i].min)
        || !jsonNumberArrayValue(body, "softLimitMax", i, &limits[i].max)) {
      throw std::runtime_error("teleop_target_update requires softLimitMin[6] and softLimitMax[6]");
    }
    if (limits[i].min >= limits[i].max) {
      throw std::runtime_error("teleop_target_update soft limit min must be less than max");
    }
  }
  return limits;
}

std::array<bool, 6> jsonTeleopEnabledAxes(const std::string& body) {
  std::array<bool, 6> enabled{true, true, true, true, true, true};
  for (size_t i = 0; i < enabled.size(); ++i) {
    bool value = true;
    if (jsonBoolArrayValue(body, "enabledAxes", i, &value)) {
      enabled[i] = value;
    }
  }
  return enabled;
}

appstation::hal::Side parseSide(const std::string& value) {
  // 后端只允许左右两侧，解析失败直接返回 500 给调用方暴露配置错误。
  if (value == "left") {
    return appstation::hal::Side::Left;
  }
  if (value == "right") {
    return appstation::hal::Side::Right;
  }
  throw std::runtime_error("side must be left or right");
}

appstation::hal::SemanticAxis parseAxis(const std::string& value) {
  // 轴名使用 UI 语义名，驱动层再映射到具体控制卡通道。
  using appstation::hal::SemanticAxis;
  if (value == "X") return SemanticAxis::X;
  if (value == "Y") return SemanticAxis::Y;
  if (value == "Z") return SemanticAxis::Z;
  if (value == "Roll") return SemanticAxis::Roll;
  if (value == "Pitch") return SemanticAxis::Pitch;
  if (value == "Yaw") return SemanticAxis::Yaw;
  throw std::runtime_error("unknown axis");
}

int envIntValue(const char* key, int fallback) {
  const char* raw = std::getenv(key);
  if (!raw || !*raw) {
    return fallback;
  }
  char* end = nullptr;
  const auto value = std::strtol(raw, &end, 10);
  return end == raw ? fallback : static_cast<int>(value);
}

bool envBoolValue(const char* key, bool fallback) {
  const char* raw = std::getenv(key);
  if (!raw || !*raw) {
    return fallback;
  }
  const auto value = lowercase(raw);
  return value == "1" || value == "true" || value == "yes" || value == "on";
}

std::string httpResponse(int code, const std::string& body, bool keepAlive) {
  std::ostringstream out;
  out << "HTTP/1.1 " << code << " OK\r\n"
      << "Content-Type: application/json; charset=utf-8\r\n"
      << "Content-Length: " << body.size() << "\r\n"
      << "Connection: " << (keepAlive ? "keep-alive" : "close") << "\r\n\r\n"
      << body;
  return out.str();
}

#ifdef _WIN32
// 服务单个 TCP 连接。客户端保持 HTTP/1.1 keep-alive 时持续处理请求，
// 避免 FastAPI WS 桥接层每 33ms 都付出一次 TCP 建连成本。
void serveConnection(
    SOCKET client,
    appstation::hal::LTDMCDriver& motion,
    appstation::hal::Omega7Driver& omega,
    const std::chrono::steady_clock::time_point& started) {
  // 限制单个 keep-alive socket 的空闲时间，避免长期占用连接资源。
  DWORD recv_timeout_ms = 30000;
  setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&recv_timeout_ms), sizeof(recv_timeout_ms));
  BOOL nodelay = TRUE;
  setsockopt(client, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&nodelay), sizeof(nodelay));

  while (true) {
    std::string request;
    try {
      request = readHttpRequest(client);
    } catch (const std::exception&) {
      break;
    }
    if (request.empty()) {
      break;  // 对端关闭连接
    }

    bool keepAlive = !wantsClose(request);
    std::string body;
    int code = 200;
    try {
      const double uptime =
          std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
      if (request.rfind("GET /health ", 0) == 0) {
        body = jsonHealth(motion.health(uptime), omega.ok(), omega.lastError());
      } else if (request.rfind("GET /motion/state ", 0) == 0) {
        body = jsonMotionState(motion.readState());
      } else if (request.rfind("GET /omega/state ", 0) == 0) {
        body = jsonOmegaState(omega.readState());
      } else if (request.rfind("POST /motion/emergency_stop ", 0) == 0) {
        motion.emergencyStop();
        body = "{\"ok\":true}";
      } else if (request.rfind("POST /motion/home_all ", 0) == 0) {
        motion.homeAll(jsonWorkOriginPulse(requestBody(request)));
        body = "{\"ok\":true}";
      } else if (request.rfind("POST /motion/enable_side ", 0) == 0) {
        const auto side = parseSide(jsonStringValue(requestBody(request), "side"));
        const auto message = motion.enableSide(side, true);
        body = "{\"ok\":true,\"message\":\"" + jsonEscape(message) + "\"}";
      } else if (request.rfind("POST /motion/disable_side ", 0) == 0) {
        const auto side = parseSide(jsonStringValue(requestBody(request), "side"));
        const auto message = motion.enableSide(side, false);
        body = "{\"ok\":true,\"message\":\"" + jsonEscape(message) + "\"}";
      } else if (request.rfind("POST /motion/home_side ", 0) == 0) {
        const auto side = parseSide(jsonStringValue(requestBody(request), "side"));
        motion.homeSide(side);
        body = "{\"ok\":true}";
      } else if (request.rfind("POST /motion/manual_axis_move ", 0) == 0) {
        const auto bodyText = requestBody(request);
        // 手动 jog 请求保持最小协议面，只接受必要的轴、步长和速度参数。
        const auto side = parseSide(jsonStringValue(bodyText, "side"));
        const auto axis = parseAxis(jsonStringValue(bodyText, "axis"));
        const auto direction = jsonNumberValue(bodyText, "direction", 0);
        const auto step = jsonNumberValue(bodyText, "step", 0);
        const auto maxVelocity = jsonNumberValue(bodyText, "maxVelocityUiPerSec", 0);
        const auto startVelocity = jsonNumberValue(bodyText, "startVelocityUiPerSec", 0);
        const auto accTime = jsonNumberValue(bodyText, "accTimeSec", 0);
        const auto decTime = jsonNumberValue(bodyText, "decTimeSec", 0);
        motion.moveRelativeUi(
            side,
            axis,
            step * (direction >= 0 ? 1.0 : -1.0),
            maxVelocity,
            startVelocity,
            accTime,
            decTime);
        body = "{\"ok\":true}";
      } else if (request.rfind("POST /motion/teleop_target_update ", 0) == 0) {
        const auto bodyText = requestBody(request);
        const auto side = parseSide(jsonStringValue(bodyText, "side"));
        const std::array<double, 6> deltas{
            jsonNumberValue(bodyText, "X", 0.0),
            jsonNumberValue(bodyText, "Y", 0.0),
            jsonNumberValue(bodyText, "Z", 0.0),
            jsonNumberValue(bodyText, "Roll", 0.0),
            jsonNumberValue(bodyText, "Pitch", 0.0),
            jsonNumberValue(bodyText, "Yaw", 0.0)};
        motion.updateTeleopTargetUi(
            side,
            deltas,
            jsonNumberValue(bodyText, "translationStepLimitPulse", 0.0),
            jsonNumberValue(bodyText, "rotationStepLimitPulse", 0.0),
            jsonTeleopEnabledAxes(bodyText),
            jsonBoolValue(bodyText, "syncZeroDeltaTarget", false),
            jsonTeleopSoftLimits(bodyText),
            jsonNumberValue(bodyText, "translationVelocityUiPerSec", 0.0),
            jsonNumberValue(bodyText, "rotationVelocityUiPerSec", 0.0),
            jsonNumberValue(bodyText, "translationStartVelocityUiPerSec", 0.0),
            jsonNumberValue(bodyText, "rotationStartVelocityUiPerSec", 0.0),
            jsonNumberValue(bodyText, "accTimeSec", 0.0),
            jsonNumberValue(bodyText, "decTimeSec", 0.0));
        body = "{\"ok\":true}";
      } else if (request.rfind("POST /motion/teleop_stop_side ", 0) == 0) {
        const auto side = parseSide(jsonStringValue(requestBody(request), "side"));
        motion.stopTeleopSide(side);
        body = "{\"ok\":true}";
      } else {
        code = 404;
        body = "{\"ok\":false,\"message\":\"not found\"}";
        keepAlive = false;
      }
    } catch (const std::exception& exc) {
      code = 500;
      body = std::string("{\"ok\":false,\"message\":\"") + jsonEscape(exc.what()) + "\"}";
    }
    const auto response = httpResponse(code, body, keepAlive);
    if (send(client, response.c_str(), static_cast<int>(response.size()), 0) == SOCKET_ERROR) {
      break;
    }
    if (!keepAlive) {
      break;
    }
  }
  closesocket(client);
}
#endif

}  // namespace

int main() {
  using namespace appstation::hal;

  const auto started = std::chrono::steady_clock::now();
  // 驱动对象在主线程创建并长期持有，HTTP 处理线程只通过这些单例访问硬件。
  LTDMCDriver motion;
  Omega7Driver omega;
  const bool motionOk = motion.initialize();
  const int leftOpenId = envIntValue("APPSTATION_OMEGA7_LEFT_OPEN_ID", 0);
  const int rightOpenId = envIntValue("APPSTATION_OMEGA7_RIGHT_OPEN_ID", 1);
  const bool swapHands = envBoolValue("APPSTATION_OMEGA7_SWAP_HANDS", true);
  omega.initialize(leftOpenId, rightOpenId, swapHands);

  MotionControlThread motionThread(motion);
  if (motionOk) {
    // 运动控制线程独立跑 1kHz，HTTP 接口只提交目标命令和读取快照。
    motionThread.start(1000);
  }

#ifdef _WIN32
  WSADATA wsaData;
  if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) {
    std::cerr << "WSAStartup failed\n";
    return 1;
  }

  SOCKET server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  address.sin_port = htons(8091);
  if (bind(server, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR ||
      listen(server, SOMAXCONN) == SOCKET_ERROR) {
    std::cerr << "Failed to bind HalServer on 127.0.0.1:8091\n";
    closesocket(server);
    WSACleanup();
    return 1;
  }

  std::cout << "HalServer listening on http://127.0.0.1:8091\n";
  while (true) {
    SOCKET client = accept(server, nullptr, nullptr);
    if (client == INVALID_SOCKET) {
      continue;
    }
    // 每个 TCP 连接使用独立线程，避免慢速 keep-alive 客户端阻塞其他调用方。
    // WS 桥接层会长期占用一个连接，UI 命令则可能从其他线程发起。
    std::thread([client, &motion, &omega, &started]() {
      try {
        serveConnection(client, motion, omega, started);
      } catch (...) {
        closesocket(client);
      }
    }).detach();
  }
#else
  std::cerr << "HalServer currently supports Windows Winsock only.\n";
#endif

  motionThread.stop();
  return 0;
}
