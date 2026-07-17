// Copyright Epic Games, Inc. All Rights Reserved.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using EpicGames.Core;
using EpicGames.UBA;
using Microsoft.Extensions.Logging;

namespace UnrealBuildTool
{
    using StatusUpdateAction = Action<uint, uint, string, LogEntryType, string?>;

    class UBAAgentCoordinatorOrchestrator : IUBAAgentCoordinator
    {
        readonly Microsoft.Extensions.Logging.ILogger _logger;
        readonly UnrealBuildAcceleratorConfig _ubaConfig;
        readonly UnrealBuildAcceleratorHordeConfig _config = new();
        readonly HttpClient _httpClient = new();
        readonly SemaphoreSlim _pollLock = new(1, 1);
        readonly HashSet<string> _addedHelpers = new();

        UBAExecutor? _executor;
        CancellationTokenSource? _cancellationSource;
        Timer? _timer;
        StatusUpdateAction? _updateStatus;
        string? _leaseId;
        string _crypto = String.Empty;

        public string? Server =>
            _config.HordeServer ??
            Environment.GetEnvironmentVariable("UBA_ORCHESTRATOR_URL");

        public bool Enabled =>
            !_ubaConfig.bDisableRemote &&
            !String.IsNullOrWhiteSpace(Server);

        public string ConnectionModeString => "Direct";

        public UBAAgentCoordinatorOrchestrator(
            Microsoft.Extensions.Logging.ILogger logger,
            UnrealBuildAcceleratorConfig ubaConfig,
            CommandLineArguments? additionalArguments = null,
            DirectoryReference? projectDir = null)
        {
            _logger = logger;
            _ubaConfig = ubaConfig;

            ConfigCache.ReadSettings(
                projectDir,
                BuildHostPlatform.Current.Platform,
                _config);

            additionalArguments?.ApplyTo(_config);

            _httpClient.Timeout = TimeSpan.FromSeconds(3);
        }

        public DirectoryReference? GetUBARootDir()
        {
            return null;
        }

        public Task InitAsync(UBAExecutor executor)
        {
            _executor = executor;
            _cancellationSource = new CancellationTokenSource();
            _crypto = String.Empty;

            executor.AgentCoordinatorInitialized(this, Enabled);

            return Task.CompletedTask;
        }

        public void Start(
            ImmediateActionQueue queue,
            Func<LinkedAction, bool> canRunRemotely,
            StatusUpdateAction updateStatus)
        {
            if (!Enabled || _cancellationSource == null)
            {
                return;
            }

            _updateStatus = updateStatus;
            _updateStatus(0, 1, "Orchestrator", LogEntryType.Info, Server);

            _timer = new Timer(
                async _ => await PollAsync(),
                null,
                TimeSpan.Zero,
                TimeSpan.FromSeconds(5));
        }

        async Task PollAsync()
        {
            if (_cancellationSource == null ||
                _cancellationSource.IsCancellationRequested ||
                _executor?.Server == null)
            {
                return;
            }

            if (!await _pollLock.WaitAsync(0))
            {
                return;
            }

            try
            {
                CancellationToken cancellationToken =
                    _cancellationSource.Token;

                if (_leaseId == null)
                {
                    _leaseId = await CreateLeaseAsync(cancellationToken);
                    if (_leaseId == null)
                    {
                        _logger.LogDebug(
                            "No orchestrator helper capacity available");
                        return;
                    }

                    _logger.LogInformation(
                        "Created orchestrator lease {LeaseId}",
                        _leaseId);
                }

                string response = await GetAsync(
                    $"/api/v1/leases/{_leaseId}",
                    cancellationToken);

                using JsonDocument document =
                    JsonDocument.Parse(response);

                JsonElement root = document.RootElement;
                string state =
                    root.GetProperty("state").GetString() ?? String.Empty;

                if (!String.Equals(
                        state,
                        "active",
                        StringComparison.OrdinalIgnoreCase))
                {
                    return;
                }

                JsonElement helpers = root.GetProperty("helpers");

                foreach (JsonElement helper in helpers.EnumerateArray())
                {
                    string helperId =
                        helper.GetProperty("helper_id").GetString() ??
                        String.Empty;

                    if (String.IsNullOrEmpty(helperId) ||
                        _addedHelpers.Contains(helperId))
                    {
                        continue;
                    }

                    bool agentReady =
                        helper.GetProperty("agent_ready").GetBoolean();

                    if (!agentReady)
                    {
                        continue;
                    }

                    string address =
                        helper.GetProperty("address").GetString() ??
                        String.Empty;

                    ushort port =
                        helper.GetProperty("port").GetUInt16();

                    if (String.IsNullOrEmpty(address) || port == 0)
                    {
                        continue;
                    }

                    if (_executor.Server.AddClient(
                            address,
                            port,
                            _crypto))
                    {
                        _addedHelpers.Add(helperId);

                        _logger.LogInformation(
                            "Connected UBA helper {HelperId} at {Address}:{Port}",
                            helperId,
                            address,
                            port);

                        _updateStatus?.Invoke(
                            0,
                            2,
                            $"Connected to {address}:{port}",
                            LogEntryType.Info,
                            null);

                        _timer?.Change(
                            Timeout.Infinite,
                            Timeout.Infinite);
                    }
                }
            }
            catch (Exception exception)
            {
                _logger.LogDebug(
                    exception,
                    "Orchestrator polling failed");
            }
            finally
            {
                _pollLock.Release();
            }
        }

        async Task<string?> CreateLeaseAsync(
            CancellationToken cancellationToken)
        {
            string address =
                Environment.GetEnvironmentVariable(
                    "UBA_INITIATOR_ADDRESS") ??
                GetLocalAddress();

            string port =
                Environment.GetEnvironmentVariable(
                    "UBA_INITIATOR_PORT") ??
                "1345";

            int targetCoreCount = Environment.ProcessorCount;

            string? configuredTargetCores =
                Environment.GetEnvironmentVariable("UBA_TARGET_CORES");

            if (Int32.TryParse(configuredTargetCores, out int parsedTargetCores) &&
                parsedTargetCores > 0)
            {
                targetCoreCount = parsedTargetCores;
            }

            var payload = new
            {
                initiator_id = Environment.MachineName,
                initiator_address = address,
                initiator_port = Int32.Parse(port),
                target_core_count = targetCoreCount
            };

            using HttpResponseMessage response =
                await _httpClient.PostAsJsonAsync(
                    BuildUri("/api/v1/leases"),
                    payload,
                    cancellationToken);

            if ((int)response.StatusCode == 409)
            {
                return null;
            }

            response.EnsureSuccessStatusCode();

            using JsonDocument document =
                JsonDocument.Parse(
                    await response.Content.ReadAsStringAsync(
                        cancellationToken));

            return document.RootElement
                .GetProperty("lease_id")
                .GetString();
        }

        async Task<string> GetAsync(
            string path,
            CancellationToken cancellationToken)
        {
            using HttpResponseMessage response =
                await _httpClient.GetAsync(
                    BuildUri(path),
                    cancellationToken);

            response.EnsureSuccessStatusCode();

            return await response.Content.ReadAsStringAsync(
                cancellationToken);
        }

        async Task ReleaseLeaseAsync()
        {
            if (_leaseId == null)
            {
                return;
            }

            try
            {
                using HttpResponseMessage response =
                    await _httpClient.DeleteAsync(
                        BuildUri($"/api/v1/leases/{_leaseId}"));

                response.Dispose();
            }
            catch (Exception exception)
            {
                _logger.LogDebug(
                    exception,
                    "Failed to release orchestrator lease {LeaseId}",
                    _leaseId);
            }

            _leaseId = null;
        }

        Uri BuildUri(string path)
        {
            string server = Server!.TrimEnd('/');
            return new Uri(server + path);
        }

        static string GetLocalAddress()
        {
            return Dns.GetHostEntry(Dns.GetHostName())
                .AddressList
                .FirstOrDefault(
                    address => address.AddressFamily ==
                               System.Net.Sockets.AddressFamily.InterNetwork)
                ?.ToString() ?? "127.0.0.1";
        }

        public void Stop()
        {
            _timer?.Change(
                Timeout.Infinite,
                Timeout.Infinite);

            _cancellationSource?.Cancel();
        }

        public async Task CloseAsync()
        {
            Stop();
            await ReleaseLeaseAsync();
            _timer?.Dispose();
            _cancellationSource?.Dispose();
        }

        public void Done()
        {
            _updateStatus?.Invoke(
                0,
                6,
                "Done",
                LogEntryType.Info,
                null);
        }
    }
}
