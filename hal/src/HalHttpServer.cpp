/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：提供本机 HTTP 健康探测入口；当前业务控制通过 DDS。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#endif

#include "HalHttpServer.h"
#include "HttpConnectionWorkers.h"

#include "HalJson.h"

#include <array>
#include <cctype>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

namespace appstation::hal {
namespace {

bool wantsClose(const std::string& request) {
  // 只解析 Connection 头，保持这个兼容 HTTP 面足够小。
  const auto headerEnd = request.find("\r\n\r\n");
  if (headerEnd == std::string::npos) {
    return true;
  }
  const auto headers = lowercase(request.substr(0, headerEnd));
  const auto marker = std::string("connection:");
  const auto markerPos = headers.find(marker);
  if (markerPos == std::string::npos) {
    return false;
  }
  auto valueStart = markerPos + marker.size();
  while (valueStart < headers.size() && std::isspace(static_cast<unsigned char>(headers[valueStart]))) {
    ++valueStart;
  }
  return headers.compare(valueStart, 5, "close") == 0;
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
std::string readHttpRequest(SOCKET client) {
  std::string request;
  std::array<char, 4096> buffer{};
  while (true) {
    const int bytes = recv(client, buffer.data(), static_cast<int>(buffer.size()), 0);
    if (bytes == 0) {
      return {};
    }
    if (bytes < 0) {
      return request.empty() ? std::string{} : request;
    }
    request.append(buffer.data(), static_cast<size_t>(bytes));
    const auto headerEnd = request.find("\r\n\r\n");
    if (headerEnd != std::string::npos) {
      return request;
    }
    // 这里只服务本地短请求，超过上限直接断开，避免 keep-alive 连接无限占用内存。
    if (request.size() > 65536) {
      throw std::runtime_error("HTTP request too large");
    }
  }
}

void serveConnection(
    SOCKET client,
    HalCommandDispatcher& commandDispatcher) {
  DWORD recvTimeoutMs = 30000;
  if (setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&recvTimeoutMs), sizeof(recvTimeoutMs)) == SOCKET_ERROR
      || setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&recvTimeoutMs), sizeof(recvTimeoutMs)) == SOCKET_ERROR) {
    throw std::runtime_error("HTTP socket timeout configuration failed");
  }
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
      break;
    }

    bool keepAlive = !wantsClose(request);
    std::string body;
    int code = 200;
    try {
      if (request.rfind("GET /health ", 0) == 0) {
        // HTTP 入口保留健康检查兼容性；控制命令优先通过 DDS/统一 dispatcher 进入。
        body = commandDispatcher.handle("hal.reconnect", "{}");
      } else if (request.rfind("GET /force/state ", 0) == 0) {
        body = commandDispatcher.handle("force.state", "{}");
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
}
#endif

}  // namespace

int runHalHttpServer(
    int halPort,
    HalCommandDispatcher& commandDispatcher) {
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
  address.sin_port = htons(static_cast<u_short>(halPort));
  // HAL HTTP 面只绑定 127.0.0.1，避免把硬件控制口暴露到外部网络。
  if (bind(server, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR ||
      listen(server, SOMAXCONN) == SOCKET_ERROR) {
    std::cerr << "Failed to bind HalServer on 127.0.0.1:" << halPort << "\n";
    closesocket(server);
    WSACleanup();
    return 1;
  }

  std::cout << "HalServer listening on http://127.0.0.1:" << halPort << "\n";
  HttpConnectionWorkers<32> connections;
  while (true) {
    SOCKET client = accept(server, nullptr, nullptr);
    if (client == INVALID_SOCKET) {
      continue;
    }
    try {
      const bool accepted = connections.tryStart([client, &commandDispatcher]() {
        struct CloseClient {
          SOCKET socket;
          ~CloseClient() { closesocket(socket); }
        } cleanup{client};
        serveConnection(client, commandDispatcher);
      });
      if (!accepted) closesocket(client);
    } catch (const std::exception& error) {
      closesocket(client);
      std::fprintf(stderr, "HAL HTTP worker could not start: %s\n", error.what());
    } catch (...) {
      closesocket(client);
      std::fputs("HAL HTTP worker could not start: unknown C++ exception\n", stderr);
    }
  }
#else
  std::cerr << "HalServer currently supports Windows Winsock only.\n";
  return 1;
#endif
}

}  // namespace appstation::hal
