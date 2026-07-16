#include "UbaCoordinatorOrchestrator.h"

#if PLATFORM_WINDOWS
#include <windows.h>
#include <winhttp.h>
#include <cstdlib>
#include <string>

#pragma comment(lib, "winhttp.lib")

namespace
{
    std::wstring GetUrl()
    {
        wchar_t value[1024] = {};
        DWORD length = GetEnvironmentVariableW(L"UBA_ORCHESTRATOR_URL", value, 1024);
        if (length) return std::wstring(value, length);
        length = GetEnvironmentVariableW(L"UE_HORDE_URL", value, 1024);
        return length ? std::wstring(value, length) : L"http://127.0.0.1:8080";
    }

    bool Request(const std::wstring& method, const std::wstring& path, const std::string& body, std::string& response)
    {
        URL_COMPONENTS components{};
        components.dwStructSize = sizeof(components);
        wchar_t host[256] = {};
        wchar_t urlPath[2048] = {};
        components.lpszHostName = host;
        components.dwHostNameLength = 256;
        components.lpszUrlPath = urlPath;
        components.dwUrlPathLength = 2048;
        std::wstring url = GetUrl();
        if (!WinHttpCrackUrl(url.c_str(), 0, 0, &components))
            return false;
        HINTERNET session = WinHttpOpen(L"UBA Coordinator Orchestrator/0.1", WINHTTP_ACCESS_TYPE_NO_PROXY, nullptr, nullptr, 0);
        if (!session) return false;
        WinHttpSetTimeouts(session, 2000, 2000, 2000, 2000);
        HINTERNET connection = WinHttpConnect(session, host, components.nPort, 0);
        HINTERNET request = connection ? WinHttpOpenRequest(connection, method.c_str(), path.c_str(), nullptr,
                                                              WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
                                                              components.nScheme == INTERNET_SCHEME_HTTPS ? WINHTTP_FLAG_SECURE : 0) : nullptr;
        bool ok = request && WinHttpSendRequest(request, L"Content-Type: application/json\r\n", -1,
                                                 body.empty() ? WINHTTP_NO_REQUEST_DATA : (LPVOID)body.data(),
                                                 (DWORD)body.size(), (DWORD)body.size(), 0) && WinHttpReceiveResponse(request, nullptr);
        if (ok)
        {
            DWORD status = 0, size = sizeof(status);
            WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                WINHTTP_HEADER_NAME_BY_INDEX, &status, &size, WINHTTP_NO_HEADER_INDEX);
            ok = status >= 200 && status < 300;
            DWORD available = 0;
            while (ok && WinHttpQueryDataAvailable(request, &available) && available)
            {
                std::string chunk(available, '\0');
                DWORD read = 0;
                if (!WinHttpReadData(request, chunk.data(), available, &read)) { ok = false; break; }
                response.append(chunk.data(), read);
            }
        }
        if (request) WinHttpCloseHandle(request);
        if (connection) WinHttpCloseHandle(connection);
        WinHttpCloseHandle(session);
        return ok;
    }

    std::string JsonString(const std::string& json, const char* key)
    {
        std::string marker = std::string("\"") + key + "\":\"";
        size_t start = json.find(marker);
        if (start == std::string::npos) return {};
        start += marker.size();
        size_t end = json.find('"', start);
        return end == std::string::npos ? std::string() : json.substr(start, end - start);
    }

    int JsonInt(const std::string& json, const char* key)
    {
        std::string marker = std::string("\"") + key + "\":";
        size_t start = json.find(marker);
        return start == std::string::npos ? 0 : std::atoi(json.c_str() + start + marker.size());
    }

    class Coordinator final : public uba::Coordinator
    {
    public:
        explicit Coordinator(const uba::CoordinatorCreateInfo&) {}

        void SetAddClientCallback(AddClientCallback* callback, void* userData) override
        {
            m_callback = callback;
            m_userData = userData;
        }

        void SetTargetCoreCount(uba::u32 count) override
        {
            if (!m_callback || count == 0) return;
            if (m_lease.empty())
            {
                std::string response;
                std::string body = "{\"initiator_id\":\"uba-initiator\",\"initiator_address\":\"127.0.0.1\",\"initiator_port\":1345,\"target_core_count\":" + std::to_string(count) + "}";
                if (!Request(L"POST", L"/api/v1/leases", body, response)) return;
                m_lease = JsonString(response, "lease_id");
                if (m_lease.empty()) return;
            }
            std::string response;
            if (!Request(L"GET", std::wstring(L"/api/v1/leases/") + std::wstring(m_lease.begin(), m_lease.end()), {}, response)) return;
            if (JsonString(response, "state") != "active") return;
            std::string address = JsonString(response, "address");
            int port = JsonInt(response, "port");
            if (address.empty() || port == 0 || m_added) return;
            wchar_t wideAddress[256] = {};
            MultiByteToWideChar(CP_UTF8, 0, address.c_str(), -1, wideAddress, 256);
            if ((*m_callback)(m_userData, wideAddress, (uba::u16)port)) m_added = true;
        }

        ~Coordinator()
        {
            if (!m_lease.empty())
            {
                std::string ignored;
                Request(L"DELETE", std::wstring(L"/api/v1/leases/") + std::wstring(m_lease.begin(), m_lease.end()), {}, ignored);
            }
        }

    private:
        std::string m_lease;
        AddClientCallback* m_callback = nullptr;
        void* m_userData = nullptr;
        bool m_added = false;
    };
}

uba::Coordinator* UbaCreateCoordinator(const uba::CoordinatorCreateInfo& info) { return new Coordinator(info); }
void UbaDestroyCoordinator(uba::Coordinator* coordinator) { delete static_cast<Coordinator*>(coordinator); }
#else
uba::Coordinator* UbaCreateCoordinator(const uba::CoordinatorCreateInfo&) { return nullptr; }
void UbaDestroyCoordinator(uba::Coordinator*) {}
#endif
