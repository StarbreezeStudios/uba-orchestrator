using UnrealBuildTool;
using System.IO;

[SupportedPlatforms(UnrealPlatformClass.Desktop)]
public class UbaCoordinatorOrchestratorTarget : TargetRules
{
    public UbaCoordinatorOrchestratorTarget(TargetInfo Target) : base(Target)
    {
        LaunchModuleName = "UbaCoordinatorOrchestrator";
        bShouldCompileAsDLL = true;
        UbaAgentTarget.CommonUbaSettings(this, Target, true);
        var folder = Path.Combine("Binaries", Target.Platform.ToString(), "UnrealBuildAccelerator");
        if (Target.Platform.IsInGroup(UnrealPlatformGroup.Windows))
            OutputFile = Path.Combine(folder, Target.Architectures.SingleArchitecture.bIsX64 ? "x64" : "arm64", "UbaCoordinatorOrchestrator.dll");
    }
}
