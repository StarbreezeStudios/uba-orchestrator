#pragma once

#include "UbaCoordinator.h"

#if PLATFORM_WINDOWS
#define UBA_ORCHESTRATOR_API __declspec(dllexport)
#else
#define UBA_ORCHESTRATOR_API __attribute__((visibility("default")))
#endif

extern "C" UBA_ORCHESTRATOR_API uba::Coordinator* UbaCreateCoordinator(const uba::CoordinatorCreateInfo& info);
extern "C" UBA_ORCHESTRATOR_API void UbaDestroyCoordinator(uba::Coordinator* coordinator);
