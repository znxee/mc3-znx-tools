#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <winsock2.h>
#include <ws2tcpip.h>
#include <Windows.h>
#include <reshade.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <thread>

#pragma comment(lib, "Ws2_32.lib")

namespace {

constexpr uint16_t PINE_PORT = 28011;
constexpr uint32_t MAILBOX = 0x0061C990;
constexpr uint32_t MAGIC = 0x4D433354;
constexpr uint32_t VERSION_SIZE = 0x00300001; // size 48, ABI v1
constexpr uint8_t PINE_READ32 = 2;
constexpr size_t MAILBOX_WORDS = 12;
constexpr size_t READ_COUNT = MAILBOX_WORDS + 2;

struct sample {
    bool connected = false;
    uint32_t sequence = 0;
    uint32_t flags = 0;
    float speed_kmh = 0.0f;
    float rpm = 0.0f;
    int32_t gear = 0;
    float velocity[3] = {};
};

std::atomic_bool g_stop = false;
std::atomic_uintptr_t g_socket = static_cast<uintptr_t>(INVALID_SOCKET);
std::thread g_worker;
std::mutex g_sample_mutex;
sample g_sample;

uint32_t as_u32(const uint8_t *p)
{
    uint32_t value;
    std::memcpy(&value, p, sizeof(value));
    return value;
}

float as_float(uint32_t word)
{
    float value;
    std::memcpy(&value, &word, sizeof(value));
    return value;
}

bool send_all(SOCKET socket, const uint8_t *data, size_t size)
{
    while (size != 0) {
        const int sent = send(socket, reinterpret_cast<const char *>(data),
                              static_cast<int>(size), 0);
        if (sent <= 0)
            return false;
        data += sent;
        size -= static_cast<size_t>(sent);
    }
    return true;
}

bool recv_all(SOCKET socket, uint8_t *data, size_t size)
{
    while (size != 0) {
        const int got = recv(socket, reinterpret_cast<char *>(data),
                             static_cast<int>(size), 0);
        if (got <= 0)
            return false;
        data += got;
        size -= static_cast<size_t>(got);
    }
    return true;
}

bool read_snapshot(SOCKET socket, sample &out)
{
    uint32_t addresses[READ_COUNT];
    addresses[0] = MAILBOX + 8;
    for (size_t i = 0; i < MAILBOX_WORDS; ++i)
        addresses[i + 1] = MAILBOX + static_cast<uint32_t>(i * 4);
    addresses[READ_COUNT - 1] = MAILBOX + 8;

    uint8_t request[4 + READ_COUNT * 5] = {};
    const uint32_t request_size = static_cast<uint32_t>(sizeof(request));
    std::memcpy(request, &request_size, sizeof(request_size));
    size_t cursor = 4;
    for (uint32_t address : addresses) {
        request[cursor++] = PINE_READ32;
        std::memcpy(request + cursor, &address, sizeof(address));
        cursor += sizeof(address);
    }
    if (!send_all(socket, request, sizeof(request)))
        return false;

    uint32_t response_size = 0;
    if (!recv_all(socket, reinterpret_cast<uint8_t *>(&response_size), 4))
        return false;
    constexpr uint32_t EXPECTED = 4 + 1 + READ_COUNT * 4;
    if (response_size != EXPECTED)
        return false;

    uint8_t response[EXPECTED - 4] = {};
    if (!recv_all(socket, response, sizeof(response)) || response[0] != 0)
        return false;

    uint32_t reads[READ_COUNT];
    for (size_t i = 0; i < READ_COUNT; ++i)
        reads[i] = as_u32(response + 1 + i * 4);

    const uint32_t before = reads[0];
    const uint32_t after = reads[READ_COUNT - 1];
    const uint32_t *words = reads + 1;
    if (before != after || (before & 1) || words[0] != MAGIC ||
        words[1] != VERSION_SIZE)
        return true; // connection is healthy; this particular frame is stale

    out.connected = true;
    out.sequence = before;
    out.flags = words[3];
    out.speed_kmh = as_float(words[4]);
    out.rpm = as_float(words[5]);
    std::memcpy(&out.gear, &words[6], sizeof(out.gear));
    out.velocity[0] = as_float(words[7]);
    out.velocity[1] = as_float(words[8]);
    out.velocity[2] = as_float(words[9]);
    return true;
}

void publish(const sample &value)
{
    std::lock_guard<std::mutex> lock(g_sample_mutex);
    g_sample = value;
}

void worker_main()
{
    WSADATA data;
    if (WSAStartup(MAKEWORD(2, 2), &data) != 0)
        return;

    while (!g_stop.load(std::memory_order_acquire)) {
        SOCKET socket = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (socket == INVALID_SOCKET) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            continue;
        }

        DWORD timeout_ms = 250;
        setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO,
                   reinterpret_cast<const char *>(&timeout_ms), sizeof(timeout_ms));
        setsockopt(socket, SOL_SOCKET, SO_SNDTIMEO,
                   reinterpret_cast<const char *>(&timeout_ms), sizeof(timeout_ms));

        sockaddr_in server = {};
        server.sin_family = AF_INET;
        server.sin_port = htons(PINE_PORT);
        InetPtonA(AF_INET, "127.0.0.1", &server.sin_addr);
        if (connect(socket, reinterpret_cast<const sockaddr *>(&server),
                    sizeof(server)) == SOCKET_ERROR) {
            closesocket(socket);
            publish(sample{});
            std::this_thread::sleep_for(std::chrono::seconds(1));
            continue;
        }

        g_socket.store(static_cast<uintptr_t>(socket), std::memory_order_release);
        while (!g_stop.load(std::memory_order_acquire)) {
            sample next;
            if (!read_snapshot(socket, next))
                break;
            publish(next);
            std::this_thread::sleep_for(std::chrono::milliseconds(8));
        }

        const uintptr_t held = g_socket.exchange(
            static_cast<uintptr_t>(INVALID_SOCKET), std::memory_order_acq_rel);
        if (held == static_cast<uintptr_t>(socket))
            closesocket(socket);
        publish(sample{});
        // PINE rejects memory reads until a VM exists. Avoid a tight
        // reconnect loop (and a noisy PCSX2 log) during emulator startup.
        if (!g_stop.load(std::memory_order_acquire))
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
    WSACleanup();
}

void update_uniform(reshade::api::effect_runtime *runtime,
                    reshade::api::effect_uniform_variable variable,
                    void *user_data)
{
    const sample &value = *static_cast<const sample *>(user_data);
    char source[32] = {};
    size_t source_size = sizeof(source);
    if (!runtime->get_annotation_string_from_uniform_variable(
            variable, "source", source, &source_size))
        return;

    if (std::strcmp(source, "mc3_connected") == 0) {
        runtime->set_uniform_value_float(variable, value.connected ? 1.0f : 0.0f);
    } else if (std::strcmp(source, "mc3_speed_kmh") == 0) {
        runtime->set_uniform_value_float(variable, value.speed_kmh);
    } else if (std::strcmp(source, "mc3_rpm") == 0) {
        runtime->set_uniform_value_float(variable, value.rpm);
    } else if (std::strcmp(source, "mc3_gear") == 0) {
        runtime->set_uniform_value_int(variable, value.gear);
    } else if (std::strcmp(source, "mc3_flags") == 0) {
        runtime->set_uniform_value_uint(variable, value.flags);
    } else if (std::strcmp(source, "mc3_velocity") == 0) {
        runtime->set_uniform_value_float(variable, value.velocity, 3);
    }
}

void update_effects(reshade::api::effect_runtime *runtime,
                    reshade::api::command_list *,
                    reshade::api::resource_view,
                    reshade::api::resource_view)
{
    sample current;
    {
        std::lock_guard<std::mutex> lock(g_sample_mutex);
        current = g_sample;
    }
    runtime->enumerate_uniform_variables(nullptr, update_uniform, &current);
}

} // namespace

extern "C" __declspec(dllexport) const char *NAME = "MC3 Telemetry";
extern "C" __declspec(dllexport) const char *DESCRIPTION =
    "Reads mc3_telemetry.mod through PCSX2 PINE and updates ReShade uniforms.";

extern "C" __declspec(dllexport) bool AddonInit(
    HMODULE addon_module, HMODULE reshade_module)
{
    if (!reshade::register_addon(addon_module, reshade_module))
        return false;
    reshade::register_event<reshade::addon_event::reshade_begin_effects>(
        update_effects);
    g_stop.store(false, std::memory_order_release);
    g_worker = std::thread(worker_main);
    return true;
}

extern "C" __declspec(dllexport) void AddonUninit(
    HMODULE addon_module, HMODULE reshade_module)
{
    g_stop.store(true, std::memory_order_release);
    const SOCKET socket = static_cast<SOCKET>(g_socket.exchange(
        static_cast<uintptr_t>(INVALID_SOCKET), std::memory_order_acq_rel));
    if (socket != INVALID_SOCKET) {
        shutdown(socket, SD_BOTH);
        closesocket(socket);
    }
    if (g_worker.joinable())
        g_worker.join();
    reshade::unregister_event<reshade::addon_event::reshade_begin_effects>(
        update_effects);
    reshade::unregister_addon(addon_module, reshade_module);
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH)
        DisableThreadLibraryCalls(module);
    return TRUE;
}
